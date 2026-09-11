from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.types import ASGIApp, Receive, Scope, Send


logger = logging.getLogger("posti.builder")
if not logger.handlers:
    logger.setLevel(logging.INFO)


STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", "/app/static")).resolve()
DATA_ROOT = Path(os.environ.get("POSTI_DATA_ROOT", "/app/data")).resolve()
PROJECT_ROOT = DATA_ROOT / "projects"
BINARY_ROOT = DATA_ROOT / "generated_binary"
HISTORY_ROOT = PROJECT_ROOT / ".history"
POSTI_APP_PIN = os.environ.get("POSTI_APP_PIN", "").strip()
SESSION_COOKIE = "posti_session"
SESSION_TTL_SECONDS = int(os.environ.get("POSTI_SESSION_TTL_SECONDS", "43200"))
COOKIE_SECURE = os.environ.get("POSTI_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
HISTORY_LIMIT = 10
MAX_SCRIPT_BYTES = int(os.environ.get("POSTI_MAX_SCRIPT_BYTES", "1048576"))
BUILD_TIMEOUT_SECONDS = int(os.environ.get("POSTI_BUILD_TIMEOUT_SECONDS", "180"))
BUILD_QUEUE_TIMEOUT_SECONDS = int(os.environ.get("POSTI_BUILD_QUEUE_TIMEOUT_SECONDS", "2"))
BUILD_TEMP_MAX_AGE_SECONDS = int(os.environ.get("POSTI_BUILD_TEMP_MAX_AGE_SECONDS", "86400"))
VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){0,2}$"
build_semaphore = asyncio.Semaphore(1)
sessions: dict[str, float] = {}
login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            response = JSONResponse(status_code=413, content={"detail": "Request body is too large."})
            await response(scope, receive, send)


class BuildRequest(BaseModel):
    script: str = Field(min_length=1, max_length=MAX_SCRIPT_BYTES)
    filename: str | None = Field(default=None, max_length=128)
    version: str | None = Field(default="1.0", pattern=VERSION_PATTERN, max_length=32)


class ScriptSaveRequest(BaseModel):
    script: str = Field(min_length=1, max_length=MAX_SCRIPT_BYTES)
    filename: str | None = Field(default=None, max_length=128)
    version: str = Field(pattern=VERSION_PATTERN, max_length=32)


class LoginRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=128, pattern=r"^[0-9]+$")


class ProjectSummary(BaseModel):
    filename: str
    size: int
    modified_at: float


def ensure_dir(path: Path) -> Path:
    """Create a persistence directory or fail with a useful startup error."""
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError(f"Persistence path is not a directory: {path}")
    return path


def _sanitize_name(name: str, default: str) -> str:
    candidate = name.strip() or default
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", candidate)
    return safe.strip("-_") or default


def _cleanup(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_stale_builds() -> None:
    cutoff = time.time() - BUILD_TEMP_MAX_AGE_SECONDS
    for candidate in BINARY_ROOT.glob("posti-build-*"):
        try:
            if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                _cleanup(candidate)
        except OSError as exc:
            logger.warning("Unable to inspect stale build directory %s: %s", candidate, exc)
    for root in (PROJECT_ROOT, BINARY_ROOT):
        for candidate in root.glob(".*.tmp"):
            try:
                if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except OSError as exc:
                logger.warning("Unable to inspect stale temporary file %s: %s", candidate, exc)


def _archive_project(target: Path) -> None:
    if not target.is_file():
        return
    history_dir = HISTORY_ROOT / target.stem
    history_dir.mkdir(parents=True, exist_ok=True)
    snapshot = history_dir / f"{time.time_ns()}.py"
    shutil.copy2(target, snapshot)
    snapshots = sorted(history_dir.glob("*.py"), key=lambda path: path.name, reverse=True)
    for stale in snapshots[HISTORY_LIMIT:]:
        stale.unlink(missing_ok=True)


def _normalize_script(script: str) -> str:
    """Apply backend-side compatibility patches to older generated scripts."""
    if 'art = """' in script and 'art = r"""' not in script:
        script = script.replace('art = """', 'art = r"""', 1)
    return script.replace(
        'prompt_bool("Enable dry-run mode?", default=True)',
        'prompt_bool("Enable dry-run mode?", default=False)',
    )


def _check_data_dir(label: str, path: Path) -> None:
    if not os.access(path, os.W_OK | os.X_OK):
        raise RuntimeError(
            f"{label} directory {path} is not writable by UID={os.geteuid()} GID={os.getegid()}"
        )


def _safe_static_candidate(full_path: str) -> Path | None:
    candidate = (STATIC_ROOT / full_path).resolve()
    if not candidate.is_relative_to(STATIC_ROOT):
        return None
    return candidate if candidate.is_file() else None


def _safe_project_candidate(filename: str) -> Path | None:
    if not filename or Path(filename).name != filename or not filename.lower().endswith(".py"):
        return None
    candidate = (PROJECT_ROOT / filename).resolve()
    if not candidate.is_relative_to(PROJECT_ROOT):
        return None
    return candidate if candidate.is_file() else None


def _require_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE, "")
    expires_at = sessions.get(token, 0)
    if not token or expires_at <= time.time():
        sessions.pop(token, None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="PIN login required.")


def _run_pyinstaller(source: Path, tmp_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pyinstaller", "--clean", "--noconfirm", "--onefile", "--name", "posti_cli", str(source)],
        cwd=tmp_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=BUILD_TIMEOUT_SECONDS,
        env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(tmp_root / ".pyinstaller")},
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dir(DATA_ROOT)
    ensure_dir(PROJECT_ROOT)
    ensure_dir(BINARY_ROOT)
    ensure_dir(HISTORY_ROOT)
    _check_data_dir("projects", PROJECT_ROOT)
    _check_data_dir("generated_binary", BINARY_ROOT)
    _cleanup_stale_builds()
    if not re.fullmatch(r"[0-9]{4,}", POSTI_APP_PIN):
        raise RuntimeError("POSTI_APP_PIN must contain at least four digits and no other characters.")
    yield


app = FastAPI(
    title="POSTI Binary Builder",
    version="2.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

cors_origins = [origin.strip() for origin in os.environ.get("POSTI_CORS_ORIGINS", "").split(",") if origin.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Posti-Filename", "X-Posti-Project-Path"],
    )
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_SCRIPT_BYTES + 4096)


@app.middleware("http")
async def secure_responses_and_limit_content_length(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                if int(raw_length) > MAX_SCRIPT_BYTES + 4096:
                    return JSONResponse(status_code=413, content={"detail": "Request body is too large."})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header."})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'"
    )
    return response


@app.get("/api/healthz")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok", "authentication": "pin"}


@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE, "")
    return {"authenticated": bool(token and sessions.get(token, 0) > time.time())}


@app.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request):
    client = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = login_attempts[client]
    while attempts and attempts[0] < now - 300:
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Too many PIN attempts. Try again later.")
    if not secrets.compare_digest(payload.pin, POSTI_APP_PIN):
        attempts.append(now)
        raise HTTPException(status_code=401, detail="Invalid PIN.")
    login_attempts.pop(client, None)
    token = secrets.token_urlsafe(32)
    sessions[token] = now + SESSION_TTL_SECONDS
    response = JSONResponse({"authenticated": True})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
async def logout(request: Request):
    sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/api/build-binary", dependencies=[Depends(_require_session)])
async def build_binary(payload: BuildRequest):
    script = _normalize_script(payload.script.strip())
    if not script:
        raise HTTPException(status_code=400, detail="Script content is empty.")

    version = payload.version or "1.0"
    base_name = _sanitize_name(payload.filename or "posti_cli", "posti_cli")
    artifact_name = f"{base_name}_posti"

    try:
        await asyncio.wait_for(build_semaphore.acquire(), timeout=BUILD_QUEUE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(status_code=429, detail="Another binary build is already running.") from exc

    tmp_root: Path | None = None
    persisted_tmp: Path | None = None
    cleanup_on_error = True
    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="posti-build-", dir=BINARY_ROOT))
        source = tmp_root / "posti_cli.py"
        source.write_text(script, encoding="utf-8")

        try:
            proc = await asyncio.to_thread(_run_pyinstaller, source, tmp_root)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="PyInstaller build timed out.") from exc

        if proc.returncode != 0:
            error = (proc.stderr or proc.stdout) or "PyInstaller failed."
            raise HTTPException(status_code=500, detail=error.strip()[:2000])

        binary = tmp_root / "dist" / ("posti_cli.exe" if os.name == "nt" else "posti_cli")
        if not binary.exists():
            raise HTTPException(status_code=500, detail="Binary not produced by PyInstaller.")

        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        extension = ".exe" if os.name == "nt" else ""
        artifact_name += extension
        artifact_path = BINARY_ROOT / artifact_name
        persisted_tmp = BINARY_ROOT / f".{artifact_name}.{secrets.token_hex(8)}.tmp"
        shutil.copy2(binary, persisted_tmp)
        persisted_tmp.chmod(0o755)
        os.replace(persisted_tmp, artifact_path)
        persisted_tmp = None

        logger.info("Binary built for %s version=%s at %s", base_name, version, artifact_path)
        cleanup_on_error = False
        return FileResponse(
            path=binary,
            filename=artifact_name,
            media_type="application/octet-stream",
            background=BackgroundTask(_cleanup, tmp_root),
            headers={"X-Posti-Filename": artifact_name},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Binary build error: %s", exc)
        raise HTTPException(status_code=500, detail="Binary build failed.") from exc
    finally:
        if persisted_tmp is not None:
            persisted_tmp.unlink(missing_ok=True)
        if cleanup_on_error and tmp_root is not None:
            _cleanup(tmp_root)
        build_semaphore.release()


@app.post("/api/save-script", dependencies=[Depends(_require_session)])
async def save_script(payload: ScriptSaveRequest):
    script = _normalize_script(payload.script.strip())
    if not script:
        raise HTTPException(status_code=400, detail="Script content is empty.")

    base = _sanitize_name(payload.filename or "posti", "posti")
    target = PROJECT_ROOT / f"{base}_posti.py"
    temporary: Path | None = None
    try:
        _archive_project(target)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=PROJECT_ROOT)
        temporary = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(script)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        logger.exception("Unable to persist project %s: %s", target, exc)
        raise HTTPException(status_code=500, detail="Unable to persist project script.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    relative_path = str(target.relative_to(DATA_ROOT))
    logger.info("Saved project script path=%s bytes=%d", target, len(script.encode("utf-8")))
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="text/x-python",
        headers={"X-Posti-Filename": target.name, "X-Posti-Project-Path": relative_path},
    )


@app.get(
    "/api/projects",
    response_model=list[ProjectSummary],
    dependencies=[Depends(_require_session)],
)
async def list_projects(response: Response) -> list[ProjectSummary]:
    response.headers["Cache-Control"] = "no-store"
    projects: list[ProjectSummary] = []
    for candidate in PROJECT_ROOT.glob("*.py"):
        safe_candidate = _safe_project_candidate(candidate.name)
        if safe_candidate is None:
            continue
        try:
            metadata = safe_candidate.stat()
        except OSError as exc:
            logger.warning("Unable to inspect project %s: %s", safe_candidate, exc)
            continue
        projects.append(
            ProjectSummary(
                filename=safe_candidate.name,
                size=metadata.st_size,
                modified_at=metadata.st_mtime,
            )
        )
    return sorted(projects, key=lambda project: (-project.modified_at, project.filename.lower()))


@app.get("/api/projects/{filename}", dependencies=[Depends(_require_session)])
async def load_project(filename: str):
    project = _safe_project_candidate(filename)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return FileResponse(
        path=project,
        filename=project.name,
        media_type="text/x-python",
        headers={"Cache-Control": "no-store", "X-Posti-Filename": project.name},
    )


@app.post("/api/projects/{filename}/undo", dependencies=[Depends(_require_session)])
async def undo_project_save(filename: str):
    project = _safe_project_candidate(filename)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    history_dir = HISTORY_ROOT / project.stem
    snapshots = sorted(history_dir.glob("*.py"), key=lambda path: path.name, reverse=True)
    if not snapshots:
        raise HTTPException(status_code=409, detail="No earlier save is available.")

    snapshot = snapshots[0]
    temporary: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{project.name}.", suffix=".tmp", dir=PROJECT_ROOT)
        temporary = Path(temp_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(snapshot.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, project)
        temporary = None
        snapshot.unlink()
    except OSError as exc:
        logger.exception("Unable to restore project %s: %s", project, exc)
        raise HTTPException(status_code=500, detail="Unable to restore the earlier save.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return FileResponse(
        path=project,
        filename=project.name,
        media_type="text/x-python",
        headers={"Cache-Control": "no-store", "X-Posti-Filename": project.name},
    )


if STATIC_ROOT.exists():
    INDEX_FILE = STATIC_ROOT / "index.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_root():
        if INDEX_FILE.is_file():
            return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="UI not built.")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_static(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = _safe_static_candidate(full_path)
        if candidate is not None:
            return FileResponse(candidate)
        if INDEX_FILE.is_file():
            return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404)
