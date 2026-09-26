from starlette.testclient import TestClient

from backend.app.main import _ALLOWED_ORIGINS, app


def test_local_development_origins_remain_allowed():
    assert "http://127.0.0.1:8000" in _ALLOWED_ORIGINS
    assert "http://localhost:5173" in _ALLOWED_ORIGINS


def test_api_allows_deployed_same_origin_host():
    with TestClient(app, base_url="https://study.example") as client:
        response = client.get("/api/health", headers={"Origin": "https://study.example"})
    assert response.status_code == 200


def test_api_rejects_untrusted_cross_origin():
    with TestClient(app, base_url="https://study.example") as client:
        response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
