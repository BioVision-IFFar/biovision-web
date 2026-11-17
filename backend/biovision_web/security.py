"""Proteções HTTP em memória para uma instância temporária da mostra."""

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from .config import (
    GLOBAL_POLLS_PER_MINUTE, GLOBAL_UPLOADS_PER_MINUTE, POLLS_PER_MINUTE,
    STATIC_DIR, TRUSTED_PROXY_IPS, UPLOADS_PER_MINUTE,
)


UPLOAD_ENDPOINTS = frozenset({
    "classificar", "classificar_video", "classificar_audio", "processar_contagem_animais",
})
PUBLIC_STATIC_FILES = frozenset({
    "css/style.css", "js/app.js", "manifest.json", "service-worker.js",
    "icons/logo_biovision.png", "icons/logo_biovision_air.png",
    "icons/icon-192.png", "icons/icon-512.png", "icons/favicon.ico",
    "icons/identify_scene.svg", "icons/biovision_home_background.png",
    "icons/biovision_hero_white.png", "icons/biovision_air_hero_definitivo.png",
    "icons/biovision_air_background_desktop_v2.png",
    "icons/biovision_air_background_mobile_v2.png", "species/homo_sapiens.jpg",
})
VISITOR_COOKIE = "biovision_visitor"
VISITOR_TTL = 21600


class VisitorTokens:
    def __init__(self):
        self.secret = secrets.token_bytes(32)

    def issue(self):
        value = f"{secrets.token_hex(16)}.{int(time.time())}"
        signature = hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()
        return f"{value}.{signature}"

    def identity(self, token):
        try:
            if not isinstance(token, str) or len(token) > 128:
                return None
            visitor, timestamp, signature = token.split(".")
            if len(visitor) != 32 or any(char not in "0123456789abcdef" for char in visitor):
                return None
            if not 0 <= time.time() - int(timestamp) <= VISITOR_TTL:
                return None
            expected = hmac.new(self.secret, f"{visitor}.{timestamp}".encode(), hashlib.sha256).hexdigest()
            return visitor if hmac.compare_digest(signature, expected) else None
        except (ValueError, TypeError):
            return None


class RequestLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows = OrderedDict()
        self.global_windows = {"upload": deque(), "poll": deque()}

    def check(self, visitor, *, polling=False):
        now = time.monotonic()
        category = "poll" if polling else "upload"
        visitor_limit = POLLS_PER_MINUTE if polling else UPLOADS_PER_MINUTE
        global_limit = GLOBAL_POLLS_PER_MINUTE if polling else GLOBAL_UPLOADS_PER_MINUTE
        with self.lock:
            # Limitar também a memória usada por cookies novos/rotacionados.
            while self.windows:
                key, events = next(iter(self.windows.items()))
                if events and events[-1] > now - 60 and len(self.windows) < 10000:
                    break
                self.windows.pop(key)
            key = (category, visitor)
            visitor_events = self.windows.setdefault(key, deque())
            self.windows.move_to_end(key)
            windows = [self.global_windows[category], visitor_events]
            for events in windows:
                while events and events[0] <= now - 60:
                    events.popleft()
            if len(windows[0]) >= global_limit:
                return 503
            if len(windows[1]) >= visitor_limit:
                return 429
            for events in windows:
                events.append(now)
        return None


def public_static_path(filename):
    if filename not in PUBLIC_STATIC_FILES:
        return None
    root = STATIC_DIR.resolve()
    target = (root / filename).resolve()
    return target if target.is_relative_to(root) and target.is_file() else None


def trusted_proxy(remote_addr):
    try:
        source = ipaddress.ip_address(remote_addr)
        return any(source == ipaddress.ip_address(value) for value in TRUSTED_PROXY_IPS)
    except ValueError:
        return False


def install_security(app):
    from flask import abort, g, jsonify, request, send_file
    from werkzeug.exceptions import HTTPException

    tokens = VisitorTokens()
    limits = RequestLimits()

    def serve_static(filename):
        path = public_static_path(filename)
        if path is None:
            abort(404)
        return send_file(path, conditional=True)

    app.add_url_rule("/static_biovision/<path:filename>", endpoint="static", view_func=serve_static)

    @app.before_request
    def protect_requests():
        if request.endpoint not in UPLOAD_ENDPOINTS | {"index", "identificar", "biovision_air", "consultar_job"}:
            return None
        token = request.cookies.get(VISITOR_COOKIE)
        visitor = tokens.identity(token)
        if visitor is None:
            token = tokens.issue()
            visitor = tokens.identity(token)
            g.new_visitor_token = token
        g.visitor_id = visitor
        is_upload = request.endpoint in UPLOAD_ENDPOINTS
        polling = request.endpoint == "consultar_job"
        if is_upload:
            # Não aceitar formulários disparados por outros sites no navegador.
            origin = request.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != request.host.lower():
                        return jsonify(erro="Origem do envio não permitida."), 403
                except ValueError:
                    return jsonify(erro="Origem do envio não permitida."), 403
            if request.mimetype != "multipart/form-data":
                return jsonify(erro="Envie um arquivo de mídia pelo formulário."), 400
        if is_upload or polling:
            status = limits.check(visitor, polling=polling)
            if status:
                message = ("Você fez muitos pedidos. Aguarde um minuto e tente novamente."
                           if status == 429 else "O servidor está ocupado. Tente novamente em um minuto.")
                response = jsonify(erro=message)
                response.status_code = status
                response.headers["Retry-After"] = "60"
                return response
        return None

    @app.after_request
    def protect_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' https: http: data: blob:; "
            "media-src 'self' blob:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        )
        if request.endpoint in UPLOAD_ENDPOINTS | {"consultar_job"}:
            response.headers["Cache-Control"] = "no-store"
        if request.endpoint == "service_worker" or request.path.endswith("/service-worker.js"):
            response.headers["Cache-Control"] = "no-cache"
        if getattr(g, "new_visitor_token", None):
            # Só a ponte explicitamente configurada pode informar o HTTPS externo.
            secure = request.is_secure or (
                trusted_proxy(request.remote_addr or "")
                and request.headers.get("X-Forwarded-Proto", "").lower() == "https"
            )
            response.set_cookie(VISITOR_COOKIE, g.new_visitor_token, max_age=VISITOR_TTL,
                                httponly=True, samesite="Strict", secure=secure, path="/biovision")
        return response

    @app.errorhandler(Exception)
    def safe_error(error):
        if isinstance(error, HTTPException):
            status = error.code or 500
            message = {400: "Requisição inválida.", 404: "Recurso não encontrado.",
                       405: "Método não permitido.", 413: "O envio ultrapassa o limite permitido.",
                       415: "Formato de mídia não suportado."}.get(status, "Não foi possível atender à requisição.")
        else:
            app.logger.exception("Falha interna ao atender uma requisição")
            status, message = 500, "Não foi possível concluir o pedido. Tente novamente."
        return jsonify(erro=message), status
