from fastapi.testclient import TestClient


def test_app_starts(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Quizzy" in response.text


def test_quiz_list_route_returns_200(client: TestClient) -> None:
    response = client.get("/quizzes")
    assert response.status_code == 200
    assert "Test quiz" in response.text
