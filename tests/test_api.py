import inspect
import os

os.environ["DATA_DIR"] = "/tmp/jobel-croquis-tests"

from fastapi.testclient import TestClient

from backend.main import analisar, app


def test_health():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["engine"] == "local-first"
    assert response.json()["modelo_oficial"] == "pronto"


def test_analysis_api_accepts_only_project_pdf():
    assert list(inspect.signature(analisar).parameters) == ["projeto"]


def test_job_id_cannot_escape_data_directory():
    response = TestClient(app).get("/api/jobs/not-a-job-id")
    assert response.status_code == 400
