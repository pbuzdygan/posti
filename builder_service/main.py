from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask


class BuildRequest(BaseModel):
    script: str
    filename: str | None = None
    version: str | None = None


class ScriptSaveRequest(BaseModel):
    script: str
    filename: str | None = None
    version: str


STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", "/app/static")).resolve()
DATA_ROOT = Path(os.environ.get("POSTI_DATA_ROOT", "/app/data")).resolve()


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists without changing or assuming permissions.

    If the process lacks permission to create the directory (e.g. on a NAS
    share managed externally), we swallow the error so that the application
    can still start. In that case, the directory is expected to be created
    and managed on the host side.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Do not crash on startup if the directory already exists
        # or must be managed externally (e.g. CIFS/NAS with ACLs).
        pass
    return path


ensure_dir(DATA_ROOT)
PROJECT_ROOT = ensure_dir(DATA_ROOT / "projects")
BINARY_ROOT = ensure_dir(DATA_ROOT / "generated_binary")


def _sanitize_name(name: str, default: str) -> str:
    candidate = name.strip() or default
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in candidate)
    return safe.strip("-_") or default

app = FastAPI(title="POSTI Binary Builder", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cleanup(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _check_data_dir(label: str, path: Path) -> None:
    """Log clear warnings if persistence directories are missing or not writable.

    The application will still run (downloads from the browser continue to work),
    but server-side persistence for the affected area will be disabled.
    """
    try:
        if not path.exists():
            logger.warning(
                "[DATA] %s directory %s does not exist. "
                "Server-side persistence for this area is disabled; browser downloads only.",
                label,
                path,
            )
            return
        if not path.is_dir():
            logger.warning(
                "[DATA] %s path %s exists but is not a directory. "
                "Server-side persistence for this area is disabled.",
                label,
                path,
            )
            return
        # Check basic write/execute permission from inside the container.
        if not os.access(path, os.W_OK | os.X_OK):
            logger.warning(
                "[DATA] %s directory %s is not writable by the runtime user. "
                "Binary/script downloads from the browser will still work, "
                "but nothing can be persisted to disk here.",
                label,
                path,
            )
            return
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.warning("[DATA] Unable to validate %s directory %s: %s", label, path, exc)


@app.on_event("startup")
async def validate_data_directories() -> None:
    """Run once at startup to log persistence status."""
    _check_data_dir("projects", PROJECT_ROOT)
    _check_data_dir("generated_binary", BINARY_ROOT)


@app.get("/api/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/build-binary")
async def build_binary(payload: BuildRequest):
    script = payload.script.strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script content is empty.")

    version = (payload.version or "1.0").strip() or "1.0"
    base_name = _sanitize_name(payload.filename or "posti_cli", "posti_cli")
    versioned_base = f"{base_name}_v{version}"

    tmp_root = Path(tempfile.mkdtemp(prefix="posti-build-", dir=BINARY_ROOT))
    source = tmp_root / "posti_cli.py"
    source.write_text(script, encoding="utf-8")

    try:
        proc = subprocess.run(
            ["pyinstaller", "--onefile", "--name", "posti_cli", str(source)],
            cwd=tmp_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            error = (proc.stderr or proc.stdout) or "PyInstaller failed."
            raise HTTPException(status_code=500, detail=error.strip()[:2000])

        binary = tmp_root / "dist" / ("posti_cli.exe" if os.name == "nt" else "posti_cli")
        if not binary.exists():
            raise HTTPException(status_code=500, detail="Binary not produced by PyInstaller.")

        current_mode = binary.stat().st_mode
        binary.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        extension = ".exe" if os.name == "nt" and not versioned_base.lower().endswith(".exe") else ""
        artifact_name = f"{versioned_base}{extension}"
        artifact_path = BINARY_ROOT / artifact_name
        shutil.copy2(binary, artifact_path)
        artifact_mode = artifact_path.stat().st_mode
        artifact_path.chmod(artifact_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        logger.info("Binary built for %s version=%s at %s", base_name, version, artifact_path)
        background_task = BackgroundTask(_cleanup, tmp_root)
        return FileResponse(
            path=artifact_path,
            filename=artifact_name,
            media_type="application/octet-stream",
            background=background_task,
            headers={"X-Posti-Filename": artifact_name},
        )
    except HTTPException as exc:
        logger.warning("Binary build failed: %s", exc.detail if hasattr(exc, "detail") else exc)
        _cleanup(tmp_root)
        raise
    except Exception as exc:
        logger.exception("Binary build error: %s", exc)
        _cleanup(tmp_root)
        return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.post("/api/save-script")
async def save_script(payload: ScriptSaveRequest):
    script = payload.script.strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script content is empty.")
    version = payload.version.strip() or "1.0"
    base = _sanitize_name(payload.filename or "posti", "posti")
    target = PROJECT_ROOT / f"{base}_v{version}.py"
    target.write_text(script, encoding="utf-8")
    # Ensure the saved script is executable in a consistent way (0755),
    # independent of the container's umask.
    try:
        target.chmod(
            stat.S_IRUSR
            | stat.S_IWUSR
            | stat.S_IXUSR
            | stat.S_IRGRP
            | stat.S_IXGRP
            | stat.S_IROTH
            | stat.S_IXOTH
        )
    except PermissionError:
        # If chmod is blocked by the underlying filesystem/ACLs, log but do not fail the save.
        logger.warning("Unable to chmod saved project script at %s; using filesystem defaults.", target)
    try:
        relative_path = str(target.relative_to(DATA_ROOT))
    except ValueError:
        relative_path = target.name
    headers = {
        "X-Posti-Filename": target.name,
        "X-Posti-Project-Path": relative_path,
    }
    logger.info(
        "Saved project script name=%s version=%s path=%s bytes=%d",
        target.name,
        version,
        target,
        len(script.encode("utf-8")),
    )
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="text/x-python",
        headers=headers,
    )


if STATIC_ROOT.exists():
    INDEX_FILE = STATIC_ROOT / "index.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_root():
        if INDEX_FILE.exists():
            return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="UI not built.")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_static(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = STATIC_ROOT / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        if INDEX_FILE.exists():
            return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404)

logger = logging.getLogger("posti.builder")
if not logger.handlers:
    logger.setLevel(logging.INFO)
