import inspect
import os

os.environ["DATA_DIR"] = "/tmp/jobel-croquis-tests"

from fastapi.testclient import TestClient

from backend.main import analisar, app


def test_health():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["engine"] == "croqui"
    assert response.json()["modelo_oficial"] == "pronto"
    assert "ai" not in response.json()


def test_public_ui_is_never_served_from_stale_cache():
    client = TestClient(app)
    for path in ("/", "/assets/app.js"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, max-age=0"
        assert response.headers["pragma"] == "no-cache"


def test_analysis_api_accepts_only_project_pdf():
    assert list(inspect.signature(analisar).parameters) == ["projeto"]


def test_job_id_cannot_escape_data_directory():
    response = TestClient(app).get("/api/jobs/not-a-job-id")
    assert response.status_code == 400
