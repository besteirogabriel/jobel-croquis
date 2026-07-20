import os

os.environ["DATA_DIR"] = "/tmp/jobel-croquis-tests"

from fastapi.testclient import TestClient

from backend.main import app


def test_health():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["engine"] == "local-first"


def test_job_id_cannot_escape_data_directory():
    response = TestClient(app).get("/api/jobs/not-a-job-id")
    assert response.status_code == 400
