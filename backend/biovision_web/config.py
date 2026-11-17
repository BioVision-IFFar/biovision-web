import os
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
WEB_DIR = PROJECT_ROOT / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static_biovision"
MODELS_DIR = PROJECT_ROOT / "models"


def load_project_env(env_path):
    try:
        from dotenv import load_dotenv
    except ImportError:
        if not env_path.exists():
            return
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name:
                os.environ.setdefault(name, value)
    else:
        load_dotenv(env_path)


load_project_env(PROJECT_ROOT / ".env")


def env_int(name, default, minimum=1, maximum=64):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_text(name, default=""):
    return str(os.getenv(name, default) or "").strip()


SPECIES_MODEL_PATH = MODELS_DIR / "biovision_species.ckpt"
CLASS_INDEX_PATH = DATA_DIR / "class_index_corrigido.json"
SPECIES_CATALOG_PATH = DATA_DIR / "species_catalog.json.gz"
BIRDNET_DATA_DIR = MODELS_DIR / "birdnet"
BIRDNET_MODEL_PATH = BIRDNET_DATA_DIR / "acoustic_model_v2.4_fp32.tflite"
BIRDNET_LABELS_PATH = BIRDNET_DATA_DIR / "species_labels.txt"
AIR_CATTLE_MODEL_PATH = MODELS_DIR / "biovision_air_cattle.pt"
AIR_GENERAL_MODEL_PATH = MODELS_DIR / "yolo11x.pt"
AIR_SPECIES_MODEL_PATH = MODELS_DIR / "yolo11x.pt"

ANALYSIS_WORKERS = env_int("BIOVISION_ANALYSIS_WORKERS", 2, 1, 4)
WAITRESS_THREADS = env_int("BIOVISION_HTTP_THREADS", 12, 4, 32)
MAX_QUEUED_JOBS = env_int("BIOVISION_MAX_QUEUED_JOBS", 10, 1, 50)
MAX_VISITOR_JOBS = env_int("BIOVISION_MAX_VISITOR_JOBS", 2, 1, 12)
MAX_RETAINED_JOBS = env_int("BIOVISION_MAX_RETAINED_JOBS", 200, 12, 1000)
JOB_TTL_SECONDS = env_int("BIOVISION_JOB_TTL_SECONDS", 3600, 300, 21600)
JOB_TIMEOUT_SECONDS = env_int("BIOVISION_JOB_TIMEOUT_SECONDS", 300, 30, 1800)
QUEUE_TIMEOUT_SECONDS = env_int("BIOVISION_QUEUE_TIMEOUT_SECONDS", 600, 30, 1800)
PRELOAD_MODELS = env_bool("BIOVISION_PRELOAD_MODELS", True)

IMAGE_MAX_BYTES = env_int("BIOVISION_IMAGE_MAX_MB", 10, 1, 20) * 1024 * 1024
VIDEO_MAX_BYTES = env_int("BIOVISION_VIDEO_MAX_MB", 40, 1, 100) * 1024 * 1024
AUDIO_MAX_BYTES = env_int("BIOVISION_AUDIO_MAX_MB", 15, 1, 50) * 1024 * 1024
AUDIO_MAX_SECONDS = env_int("BIOVISION_AUDIO_MAX_SECONDS", 60, 3, 300)
IMAGE_MAX_DIMENSION = env_int("BIOVISION_IMAGE_MAX_DIMENSION", 8192, 512, 16384)
IMAGE_MAX_PIXELS = env_int("BIOVISION_IMAGE_MAX_PIXELS", 24000000, 1000000, 48000000)
VIDEO_MAX_DIMENSION = env_int("BIOVISION_VIDEO_MAX_DIMENSION", 4096, 512, 8192)
VIDEO_MAX_PIXELS = env_int("BIOVISION_VIDEO_MAX_PIXELS", 8294400, 1000000, 16777216)
VIDEO_MAX_FPS = env_int("BIOVISION_VIDEO_MAX_FPS", 120, 24, 240)
MEDIA_DECODE_TIMEOUT_SECONDS = env_int("BIOVISION_MEDIA_DECODE_TIMEOUT_SECONDS", 30, 5, 120)
UPLOADS_PER_MINUTE = env_int("BIOVISION_UPLOADS_PER_MINUTE", 6, 1, 60)
GLOBAL_UPLOADS_PER_MINUTE = env_int("BIOVISION_GLOBAL_UPLOADS_PER_MINUTE", 60, 6, 600)
POLLS_PER_MINUTE = env_int("BIOVISION_POLLS_PER_MINUTE", 240, 60, 1200)
GLOBAL_POLLS_PER_MINUTE = env_int("BIOVISION_GLOBAL_POLLS_PER_MINUTE", 3600, 240, 12000)
TRUSTED_PROXY_IPS = tuple(filter(None, (
    value.strip() for value in env_text("BIOVISION_TRUSTED_PROXY_IPS").split(",")
)))

AIR_YOLO_IMGSZ = env_int("BIOVISION_AIR_IMGSZ", 640, 320, 1280)
SPECIES_YOLO_IMGSZ = env_int("BIOVISION_VIDEO_IMGSZ", 640, 320, 1280)
YOLO_CPU_TARGET_FPS = env_int("BIOVISION_YOLO_CPU_FPS", 4, 1, 12)
YOLO_GPU_TARGET_FPS = env_int("BIOVISION_YOLO_GPU_FPS", 10, 2, 30)

DEFAULT_IMAGE_BASE_URL = "https://pub-aad8385f455d485ba068da0a6bc8efe3.r2.dev"
LEGACY_IMAGE_HOSTS = frozenset({"biovision-images.pedrohenriquetoazza.workers.dev"})


def configured_image_base_url():
    value = env_text("BIOVISION_IMAGE_BASE_URL").rstrip("/")
    # Compatibilidade com o .env antigo, sem editar credenciais no servidor.
    if not value or urlsplit(value).hostname in LEGACY_IMAGE_HOSTS:
        return DEFAULT_IMAGE_BASE_URL
    return value


IMAGE_BASE_URL = configured_image_base_url()

DB_CONFIG = {
    "host": env_text("BIOVISION_DB_HOST"),
    "port": env_int("BIOVISION_DB_PORT", 3306, 1, 65535),
    "user": env_text("BIOVISION_DB_USER"),
    "password": os.getenv("BIOVISION_DB_PASSWORD", ""),
    "database": env_text("BIOVISION_DB_NAME", "biovision_especie"),
}

# Espaço para o envelope multipart; o arquivo tem seu próprio limite por mídia.
MAX_CONTENT_LENGTH = max(IMAGE_MAX_BYTES, VIDEO_MAX_BYTES, AUDIO_MAX_BYTES) + 64 * 1024
APP_HOST = env_text("BIOVISION_APP_HOST", "0.0.0.0")
APP_PORT = env_int("BIOVISION_APP_PORT", 5001, 1, 65535)
