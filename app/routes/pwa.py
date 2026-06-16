from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

STATIC_DIR = Path("app/static")
SERVICE_WORKER_HEADERS = {
    "Cache-Control": "no-cache",
    "Service-Worker-Allowed": "/",
}


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_app_manifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@router.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "js/sw.js",
        media_type="application/javascript",
        headers=SERVICE_WORKER_HEADERS,
    )


@router.get("/offline", include_in_schema=False)
def offline_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "offline.html", media_type="text/html")


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "icons/favicon.ico",
        media_type="image/x-icon",
    )
