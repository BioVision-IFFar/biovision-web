import os
from pathlib import Path

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
BIRDNET_DATA_DIR = MODELS_DIR / "birdnet"
BIRDNET_MODEL_PATH = BIRDNET_DATA_DIR / "acoustic_model_v2.4_fp32.tflite"
BIRDNET_LABELS_PATH = BIRDNET_DATA_DIR / "species_labels.txt"
AIR_CATTLE_MODEL_PATH = MODELS_DIR / "biovision_air_cattle.pt"
AIR_GENERAL_MODEL_PATH = MODELS_DIR / "yolo11x.pt"
AIR_SPECIES_MODEL_PATH = MODELS_DIR / "yolo11x.pt"

ANALYSIS_WORKERS = env_int("BIOVISION_ANALYSIS_WORKERS", 4, 1, 4)
WAITRESS_THREADS = env_int("BIOVISION_HTTP_THREADS", 12, 4, 32)
MAX_ACTIVE_JOBS = env_int("BIOVISION_MAX_ACTIVE_JOBS", 40, 10, 100)
JOB_TTL_SECONDS = env_int("BIOVISION_JOB_TTL_SECONDS", 3600, 300, 21600)
PRELOAD_MODELS = env_bool("BIOVISION_PRELOAD_MODELS", True)

AIR_YOLO_IMGSZ = env_int("BIOVISION_AIR_IMGSZ", 640, 320, 1280)
SPECIES_YOLO_IMGSZ = env_int("BIOVISION_VIDEO_IMGSZ", 640, 320, 1280)
YOLO_CPU_TARGET_FPS = env_int("BIOVISION_YOLO_CPU_FPS", 4, 1, 12)
YOLO_GPU_TARGET_FPS = env_int("BIOVISION_YOLO_GPU_FPS", 10, 2, 30)

IMAGE_BASE_URL = env_text("BIOVISION_IMAGE_BASE_URL").rstrip("/")

DB_CONFIG = {
    "host": env_text("BIOVISION_DB_HOST"),
    "port": env_int("BIOVISION_DB_PORT", 3306, 1, 65535),
    "user": env_text("BIOVISION_DB_USER"),
    "password": os.getenv("BIOVISION_DB_PASSWORD", ""),
    "database": env_text("BIOVISION_DB_NAME", "biovision_especie"),
}

MAX_CONTENT_LENGTH = 250 * 1024 * 1024
APP_HOST = env_text("BIOVISION_APP_HOST", "0.0.0.0")
APP_PORT = env_int("BIOVISION_APP_PORT", 5001, 1, 65535)
