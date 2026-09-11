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
os.environ["POSTI_API_TOKEN"] = "test-token-with-at-least-32-characters"
os.environ["POSTI_MAX_SCRIPT_BYTES"] = "1024"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_healthcheck_reports_authentication() -> None:
    with TestClient(main.app) as client:
        response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_auth": "configured"}


def test_write_endpoint_requires_token() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/save-script",
            json={"script": "print('hello')", "filename": "posti", "version": "1.0"},
        )
    assert response.status_code == 401


def test_placeholder_token_is_rejected_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "POSTI_API_TOKEN", main.API_TOKEN_PLACEHOLDER)
    with pytest.raises(RuntimeError, match="non-placeholder secret"):
        with TestClient(main.app):
            pass


def test_save_uses_validated_name_and_atomic_target() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/save-script",
            headers={"X-Posti-Token": "test-token-with-at-least-32-characters"},
            json={"script": "print('hello')", "filename": "../unsafe name", "version": "2.1.0"},
        )
    assert response.status_code == 200
    assert response.headers["X-Posti-Filename"] == "unsafe-name_v2.1.0.py"
    assert (DATA_ROOT / "projects" / "unsafe-name_v2.1.0.py").is_file()


def test_version_rejects_path_segments() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/save-script",
            headers={"X-Posti-Token": "test-token-with-at-least-32-characters"},
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
        response = client.post(
            "/api/save-script",
            headers={"X-Posti-Token": "test-token-with-at-least-32-characters"},
            json={"script": "x" * 1025, "filename": "posti", "version": "1.0"},
        )
    assert response.status_code == 422


def test_oversized_request_body_is_rejected_before_validation() -> None:
    with TestClient(main.app) as client:
        response = client.post(
            "/api/save-script",
            headers={"X-Posti-Token": "test-token-with-at-least-32-characters"},
            content=b"x" * 6000,
        )
    assert response.status_code == 413
