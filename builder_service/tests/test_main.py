import os
import sys
import tempfile
from pathlib import Path


TEST_ROOT = Path(tempfile.mkdtemp(prefix="posti-tests-"))
STATIC_ROOT = TEST_ROOT / "static"
DATA_ROOT = TEST_ROOT / "data"
STATIC_ROOT.mkdir()
(STATIC_ROOT / "index.html").write_text("<html>Posti test</html>", encoding="utf-8")
os.environ["STATIC_ROOT"] = str(STATIC_ROOT)
os.environ["POSTI_DATA_ROOT"] = str(DATA_ROOT)
os.environ["POSTI_APP_PIN"] = "1234"
os.environ["POSTI_MAX_SCRIPT_BYTES"] = "1024"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def login(client: TestClient, pin: str = "1234") -> None:
    response = client.post("/api/auth/login", json={"pin": pin})
    assert response.status_code == 200


def test_healthcheck_reports_authentication() -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "authentication": "pin"}


def test_api_requires_pin_login() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/save-script",
            json={"script": "print('hello')", "filename": "posti", "version": "1.0"},
        )
    assert response.status_code == 401


def test_invalid_pin_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "POSTI_APP_PIN", "12ab")
    with pytest.raises(RuntimeError, match="at least four digits"):
        with TestClient(main.app):
            pass


def test_login_requires_correct_numeric_pin() -> None:
    with TestClient(main.app) as client:
        invalid_format = client.post("/api/auth/login", json={"pin": "12ab"})
        wrong = client.post("/api/auth/login", json={"pin": "9999"})
        status_before = client.get("/api/auth/status")
        login(client)
        status_after = client.get("/api/auth/status")
    assert invalid_format.status_code == 422
    assert wrong.status_code == 401
    assert status_before.json() == {"authenticated": False}
    assert status_after.json() == {"authenticated": True}


def test_save_uses_validated_name_and_atomic_target() -> None:
    with TestClient(main.app) as client:
        login(client)
        response = client.post(
            "/api/save-script",
            json={"script": "print('hello')", "filename": "../unsafe name", "version": "2.1.0"},
        )
    assert response.status_code == 200
    assert response.headers["X-Posti-Filename"] == "unsafe-name_posti.py"
    assert (DATA_ROOT / "projects" / "unsafe-name_posti.py").is_file()


def test_project_list_and_load_use_server_storage() -> None:
    older = DATA_ROOT / "projects" / "legacy_posti_v1.0.py"
    older.write_text("print('legacy')", encoding="utf-8")
    os.utime(older, (1, 1))
    newer = DATA_ROOT / "projects" / "workstation_posti_2.1.py"
    newer.write_text("print('current')", encoding="utf-8")
    os.utime(newer, (2, 2))

    with TestClient(main.app) as client:
        login(client)
        listing = client.get("/api/projects")
        loaded = client.get("/api/projects/workstation_posti_2.1.py")

    assert listing.status_code == 200
    filenames = [project["filename"] for project in listing.json()]
    assert filenames.index("workstation_posti_2.1.py") < filenames.index("legacy_posti_v1.0.py")
    assert loaded.status_code == 200
    assert loaded.text == "print('current')"
    assert loaded.headers["cache-control"] == "no-store"


def test_project_endpoints_require_token_and_reject_unsafe_names() -> None:
    with TestClient(main.app) as client:
        unauthenticated = client.get("/api/projects")
        login(client)
        missing = client.get("/api/projects/not-a-project.txt")

    assert unauthenticated.status_code == 401
    assert missing.status_code == 404


def test_save_history_keeps_ten_entries_and_supports_undo() -> None:
    with TestClient(main.app) as client:
        login(client)
        for index in range(12):
            response = client.post(
                "/api/save-script",
                json={"script": f"print({index})", "filename": "history", "version": "1.0"},
            )
            assert response.status_code == 200

        history_dir = DATA_ROOT / "projects" / ".history" / "history_posti"
        assert len(list(history_dir.glob("*.py"))) == 10
        restored = client.post("/api/projects/history_posti.py/undo")

    assert restored.status_code == 200
    assert restored.text == "print(10)"
    assert len(list(history_dir.glob("*.py"))) == 9


def test_binary_uses_project_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_pyinstaller(source: Path, tmp_root: Path):
        binary = tmp_root / "dist" / "posti_cli"
        binary.parent.mkdir()
        binary.write_bytes(b"binary")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(main, "_run_pyinstaller", fake_pyinstaller)
    with TestClient(main.app) as client:
        login(client)
        response = client.post(
            "/api/build-binary",
            json={"script": "print('hello')", "filename": "workstation", "version": "2.1"},
        )

    assert response.status_code == 200
    assert response.headers["X-Posti-Filename"] == "workstation_posti"
    assert (DATA_ROOT / "generated_binary" / "workstation_posti").is_file()


def test_version_rejects_path_segments() -> None:
    with TestClient(main.app) as client:
        login(client)
        response = client.post(
            "/api/save-script",
            json={"script": "print('hello')", "filename": "posti", "version": "../../etc/passwd"},
        )
    assert response.status_code == 422


def test_static_path_cannot_escape_static_root() -> None:
    secret = TEST_ROOT / "secret.txt"
    secret.write_text("not public", encoding="utf-8")
    assert main._safe_static_candidate("../secret.txt") is None
    assert main._safe_static_candidate("index.html") == STATIC_ROOT / "index.html"


def test_oversized_script_is_rejected() -> None:
    with TestClient(main.app) as client:
        login(client)
        response = client.post(
            "/api/save-script",
            json={"script": "x" * 1025, "filename": "posti", "version": "1.0"},
        )
    assert response.status_code == 422


def test_oversized_request_body_is_rejected_before_validation() -> None:
    with TestClient(main.app) as client:
        login(client)
        response = client.post(
            "/api/save-script",
            content=b"x" * 6000,
        )
    assert response.status_code == 413
