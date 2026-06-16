from fastapi.testclient import TestClient


def test_quiz_page_links_pwa_assets(client: TestClient) -> None:
    response = client.get("/quizzes")

    assert response.status_code == 200
    assert '<link rel="manifest" href="/manifest.webmanifest">' in response.text
    assert 'name="theme-color" content="#0f766e"' in response.text
    assert 'name="apple-mobile-web-app-capable" content="yes"' in response.text
    assert "/static/icons/apple-touch-icon.png" in response.text
    assert "/static/js/pwa-register.js?v=20260615-pwa" in response.text
    assert "/static/css/app.css?v=20260614-tailwind-v4-important-pages" in response.text
    assert "http://testserver/static/" not in response.text


def test_web_app_manifest_declares_installable_shell(client: TestClient) -> None:
    response = client.get("/manifest.webmanifest")
    manifest = response.json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    assert manifest["name"] == "Quizzy"
    assert manifest["start_url"] == "/quizzes/new"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == "#0f766e"
    assert {
        "src": "/static/icons/icon-maskable-512.png",
        "sizes": "512x512",
        "type": "image/png",
        "purpose": "maskable",
    } in manifest["icons"]


def test_service_worker_is_served_from_root_scope(client: TestClient) -> None:
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["service-worker-allowed"] == "/"
    assert 'const OFFLINE_URL = "/offline";' in response.text
    assert 'request.method !== "GET"' in response.text


def test_offline_page_is_self_contained(client: TestClient) -> None:
    response = client.get("/offline")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Quizzy needs a connection." in response.text
    assert "https://" not in response.text


def test_root_favicon_is_available(client: TestClient) -> None:
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/x-icon")
    assert response.content.startswith(b"\x00\x00\x01\x00")
