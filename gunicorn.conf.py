import os


def env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


host = os.getenv("BIOVISION_APP_HOST", "0.0.0.0")
default_port = env_int("BIOVISION_APP_PORT", 5001, 1, 65535)
port = env_int("PORT", default_port, 1, 65535)

bind = f"{host}:{port}"
workers = 1
worker_class = "gthread"
threads = env_int("BIOVISION_HTTP_THREADS", 12, 4, 32)
timeout = env_int("BIOVISION_GUNICORN_TIMEOUT", 900, 60, 3600)
graceful_timeout = env_int("BIOVISION_GUNICORN_GRACEFUL_TIMEOUT", 120, 30, 600)
keepalive = 5
preload_app = False
accesslog = "-"
errorlog = "-"
capture_output = True
