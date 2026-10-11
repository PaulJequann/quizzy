from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.config import get_settings

PUBLIC_ASSETS = {"/sw.js", "/manifest.webmanifest", "/offline", "/favicon.ico"}


class CookieBoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _unsafe_cross_origin(request):
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        response = await call_next(request)
        if _private_path(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path in {"/auth/login", "/auth/logout"} and response.status_code == 204:
            response.delete_cookie("quizzy_workspace")
        if _login_redirect(request, response.status_code):
            return RedirectResponse(
                "/login", status_code=303, headers={"Cache-Control": "no-store"}
            )
        return response


def _unsafe_cross_origin(request: Request) -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return False
    origin = request.headers.get("origin")
    referer = urlsplit(request.headers.get("referer", ""))
    source = origin or (f"{referer.scheme}://{referer.netloc}" if referer.netloc else None)
    configured = urlsplit(get_settings().app_base_url)
    return source != f"{configured.scheme}://{configured.netloc}"


def _private_path(path: str) -> bool:
    return path not in PUBLIC_ASSETS and not path.startswith("/static/")


def _login_redirect(request: Request, status_code: int) -> bool:
    if status_code != 401 or request.method != "GET":
        return False
    return not request.url.path.startswith(("/api", "/auth"))
