import atexit
import copy
import io
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from .config import (
    AIR_CATTLE_MODEL_PATH,
    AIR_GENERAL_MODEL_PATH,
    AIR_SPECIES_MODEL_PATH,
    AIR_YOLO_IMGSZ,
    ANALYSIS_WORKERS,
    BIRDNET_DATA_DIR,
    BIRDNET_LABELS_PATH,
    BIRDNET_MODEL_PATH,
    CLASS_INDEX_PATH,
    JOB_TTL_SECONDS,
    MAX_ACTIVE_JOBS,
    SPECIES_MODEL_PATH,
    SPECIES_YOLO_IMGSZ,
    YOLO_CPU_TARGET_FPS,
    YOLO_GPU_TARGET_FPS,
)
from .db import (
    buscar_informacoes_classes_air,
    buscar_informacoes_especie,
    nome_classe_legivel,
    primeiro_nome_popular_ou_cientifico,
)

SPECIES_BACKBONE = "tf_efficientnetv2_m.in21k_ft_in1k"
SPECIES_IMG_SIZE = 384
SPECIES_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
SPECIES_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

BIRDNET_ACCEPT_CONFIDENCE = 0.68
BIRDNET_SECONDARY_CONFIDENCE = 0.58
BIRDNET_MIN_MARGIN = 0.08
BIRDNET_MODEL_VERSION = "2.4"
BIRDNET_BACKEND = "litert"
BIRDNET_PRECISION = "fp32"
BIRDNET_SAMPLE_RATE = 48000
BIRDNET_BATCH_SIZE = 4

species_model = None
species_device = None
species_error = None
species_lock = threading.Lock()
species_inference_lock = threading.Lock()
yolo_cache = {}
yolo_lock = threading.Lock()
yolo_inference_locks = {}
birdnet_model = None
birdnet_runtime = None
birdnet_species_filter = []
birdnet_error = None
birdnet_model_lock = threading.Lock()
birdnet_inference_lock = threading.Lock()

analysis_jobs = {}
analysis_jobs_lock = threading.Lock()
analysis_executor = ThreadPoolExecutor(
    max_workers=ANALYSIS_WORKERS,
    thread_name_prefix="biovision-analysis",
)


def emitir_progresso(callback, progress, stage):
    if callback is None:
        return
    callback(max(0, min(99, int(progress))), str(stage))


def limpar_jobs_expirados():
    now = time.time()
    with analysis_jobs_lock:
        expired = [
            job_id
            for job_id, job in analysis_jobs.items()
            if job["status"] in {"completed", "failed"}
            and now - float(job.get("updated_at", now)) > JOB_TTL_SECONDS
        ]
        for job_id in expired:
            analysis_jobs.pop(job_id, None)


def atualizar_job(job_id, *, status=None, progress=None, stage=None, result=None, error=None):
    with analysis_jobs_lock:
        job = analysis_jobs.get(job_id)
        if job is None:
            return
        if status is not None:
            job["status"] = status
            if status == "running" and job.get("started_at") is None:
                job["started_at"] = time.time()
        if progress is not None:
            job["progress"] = max(0, min(100, int(progress)))
        if stage is not None:
            job["stage"] = str(stage)
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = str(error)
        job["updated_at"] = time.time()


def executar_job(job_id, work_dir, task):
    atualizar_job(job_id, status="running", progress=5, stage="Preparando analise")

    def progress_callback(progress, stage):
        atualizar_job(job_id, progress=progress, stage=stage)

    try:
        result = task(progress_callback)
        atualizar_job(
            job_id,
            status="completed",
            progress=100,
            stage="Analise concluida",
            result=result,
        )
    except Exception as exc:
        atualizar_job(
            job_id,
            status="failed",
            progress=100,
            stage="Nao foi possivel concluir",
            error=str(exc),
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def criar_job(kind, work_dir, task):
    limpar_jobs_expirados()
    now = time.time()
    with analysis_jobs_lock:
        active = sum(
            1 for job in analysis_jobs.values()
            if job["status"] in {"queued", "running"}
        )
        if active >= MAX_ACTIVE_JOBS:
            raise RuntimeError("O servidor esta com muitas analises na fila. Tente novamente em alguns instantes.")

        job_id = uuid.uuid4().hex
        analysis_jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "progress": 2,
            "stage": "Aguardando processamento",
            "created_at": now,
            "started_at": None,
            "updated_at": now,
            "result": None,
            "error": None,
        }

    analysis_executor.submit(executar_job, job_id, str(work_dir), task)
    return job_id


def payload_job(job_id):
    limpar_jobs_expirados()
    with analysis_jobs_lock:
        job = analysis_jobs.get(job_id)
        if job is None:
            return None
        now = time.time()
        payload = {
            "job_id": job_id,
            "kind": job["kind"],
            "status": job["status"],
            "progress": job["progress"],
            "stage": job["stage"],
            "elapsed_seconds": round(now - float(job["created_at"]), 1),
        }
        if job["status"] == "queued":
            queued = sorted(
                [item for item in analysis_jobs.values() if item["status"] == "queued"],
                key=lambda item: item["created_at"],
            )
            payload["queue_position"] = next(
                (index + 1 for index, item in enumerate(queued) if item["id"] == job_id),
                1,
            )
        if job["status"] == "completed":
            payload["result"] = copy.deepcopy(job["result"])
        elif job["status"] == "failed":
            payload["error"] = job["error"] or "Falha ao processar a analise."
        return payload


def encerrar_executor():
    analysis_executor.shutdown(wait=False, cancel_futures=False)


atexit.register(encerrar_executor)


def carregar_nomes_classes(caminho_json):
    try:
        with open(caminho_json, "r", encoding="utf-8") as arquivo:
            idx_to_class = json.load(arquivo)
        return [idx_to_class[str(i)] for i in range(len(idx_to_class))]
    except FileNotFoundError:
        return None


class_names = carregar_nomes_classes(CLASS_INDEX_PATH)
if class_names is None:
    print("Arquivo de classes nao encontrado.")


def construir_modelo_especies(num_classes):
    try:
        import torch.nn as nn
        import timm
    except ImportError as exc:
        raise RuntimeError("Instale torch e timm para usar o novo modelo PyTorch.") from exc

    class BioVisionModel(nn.Module):
        def __init__(self, total_classes):
            super().__init__()
            self.backbone = timm.create_model(
                SPECIES_BACKBONE,
                pretrained=False,
                num_classes=0,
                global_pool="avg",
            )
            feature_dim = self.backbone.num_features
            self.head = nn.Sequential(
                nn.Linear(feature_dim, 1024),
                nn.BatchNorm1d(1024),
                nn.ReLU(inplace=True),
                nn.Dropout(0.4),
                nn.Linear(1024, 512),
                nn.ReLU(inplace=True),
                nn.Dropout(0.3),
                nn.Linear(512, total_classes),
            )

        def forward(self, x):
            return self.head(self.backbone(x))

    return BioVisionModel(num_classes)


def carregar_checkpoint_pytorch(caminho, device):
    import torch

    try:
        return torch.load(caminho, map_location=device, weights_only=False)
    except TypeError:
        return torch.load(caminho, map_location=device)


def obter_modelo_especies():
    """Carrega e mantém o classificador de espécies residente em memória."""
    global species_model, species_device, species_error

    if species_model is not None:
        return species_model, species_device
    if species_error is not None:
        raise RuntimeError(species_error)

    with species_lock:
        if species_model is not None:
            return species_model, species_device

        if class_names is None:
            species_error = "Arquivo de classes nao encontrado."
            raise RuntimeError(species_error)
        if not SPECIES_MODEL_PATH.exists():
            species_error = f"Modelo PyTorch nao encontrado em: {SPECIES_MODEL_PATH}"
            raise RuntimeError(species_error)

        try:
            import torch

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            if device.type == "cuda":
                torch.backends.cudnn.benchmark = True
            modelo = construir_modelo_especies(len(class_names))
            checkpoint = carregar_checkpoint_pytorch(SPECIES_MODEL_PATH, device)
            state = checkpoint.get("state_dict", checkpoint) if isinstance(checkpoint, dict) else checkpoint
            if not isinstance(state, dict):
                raise RuntimeError("Checkpoint PyTorch invalido.")

            model_state = {
                key.replace("model.", "", 1): value
                for key, value in state.items()
                if key.startswith("model.")
            }
            if not model_state:
                model_state = state

            missing, unexpected = modelo.load_state_dict(model_state, strict=False)
            if len(missing) > 20:
                raise RuntimeError(
                    "Checkpoint nao combina com a arquitetura do app. "
                    f"Camadas ausentes: {missing[:5]}"
                )
            if unexpected:
                print(f"[BioVision] Camadas extras ignoradas no checkpoint: {unexpected[:5]}")

            modelo.to(device)
            modelo.eval()
            species_model = modelo
            species_device = device
            print(f"Modelo PyTorch carregado com sucesso em {device}.")
            return species_model, species_device
        except Exception as exc:
            species_error = f"Erro ao carregar o modelo PyTorch: {exc}"
            raise RuntimeError(species_error) from exc


def preparar_tensor_especies(source):
    import torch

    if isinstance(source, (bytes, bytearray)):
        image = Image.open(io.BytesIO(source)).convert("RGB")
    else:
        image = Image.open(source).convert("RGB")

    resampling = getattr(Image, "Resampling", Image).BICUBIC
    image = image.resize((SPECIES_IMG_SIZE, SPECIES_IMG_SIZE), resampling)
    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - SPECIES_MEAN) / SPECIES_STD
    tensor = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)
    return tensor


def prever_especie_por_imagem(source, top_k=5):
    import torch

    if class_names is None:
        raise RuntimeError("Arquivo de classes nao encontrado.")

    modelo, device = obter_modelo_especies()
    tensor = preparar_tensor_especies(source).to(device, non_blocking=device.type == "cuda")

    with species_inference_lock, torch.inference_mode():
        if device.type == "cuda":
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                logits = modelo(tensor)
        else:
            logits = modelo(tensor)
        probs = torch.softmax(logits, dim=1)[0]
        limite = min(top_k, len(class_names))
        valores, indices = torch.topk(probs, k=limite)

    topk = []
    for valor, indice in zip(valores.detach().cpu().tolist(), indices.detach().cpu().tolist()):
        classe = class_names[int(indice)]
        topk.append({
            "label": nome_classe_legivel(classe),
            "raw_label": classe,
            "confidence": round(float(valor) * 100.0, 2),
        })

    principal = topk[0]
    return {
        "keyword": principal["label"],
        "raw_keyword": principal["raw_label"],
        "confidence": principal["confidence"],
        "topk": topk,
    }


AIR_MAX_VIDEO_SECONDS = 30
IDENTIFY_VIDEO_MAX_SECONDS = 60
AIR_ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}

AIR_CATTLE_CONFIDENCE = 0.35

AIR_CLASS_IDS = {
    "boi": 19,
    "humano": 0,
    "cachorro": 16,
    "gato": 15,
    "ave": 14,
}
AIR_CLASS_BY_ID = {class_id: name for name, class_id in AIR_CLASS_IDS.items()}
AIR_GENERAL_CLASS_IDS = {
    class_name: class_id
    for class_name, class_id in AIR_CLASS_IDS.items()
    if class_name != "boi"
}
AIR_GENERAL_CLASS_BY_ID = {
    class_id: class_name
    for class_name, class_id in AIR_GENERAL_CLASS_IDS.items()
}
AIR_CLASS_LABELS = {
    "boi": "Boi",
    "humano": "Humano",
    "cachorro": "Cachorro",
    "gato": "Gato",
    "ave": "Ave",
}
AIR_CLASS_MIN_CONFIDENCE = {
    "boi": 0.30,
    "humano": 0.48,
    "cachorro": 0.45,
    "gato": 0.45,
    "ave": 0.52,
}
AIR_SPECIES_COCO_IDS = {
    "pessoa": 0,
    "ave": 14,
    "gato": 15,
    "cao": 16,
    "bovino": 19,
}
AIR_SPECIES_BY_ID = {class_id: name for name, class_id in AIR_SPECIES_COCO_IDS.items()}
AIR_SPECIES_LABELS = {
    "pessoa": "Humano",
    "ave": "Ave",
    "gato": "Gato",
    "cao": "Cachorro",
    "bovino": "Bovino",
}
AIR_SPECIES_FALLBACK = {
    "pessoa": "humano",
    "ave": "ave",
    "gato": "gato",
    "cao": "cachorro",
    "bovino": "boi",
}


def video_permitido(filename):
    return Path(filename).suffix.lower() in AIR_ALLOWED_VIDEO_EXTENSIONS


def carregar_yolo(cache_key, model_path):
    if not model_path.exists():
        raise FileNotFoundError(f"Modelo YOLO nao encontrado em: {model_path}")

    with yolo_lock:
        if cache_key in yolo_cache:
            return yolo_cache[cache_key], yolo_inference_locks[cache_key]

        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("Instale a dependencia ultralytics para usar os modelos de video.") from exc

        yolo_cache[cache_key] = YOLO(str(model_path))
        yolo_inference_locks[cache_key] = threading.Lock()
        return yolo_cache[cache_key], yolo_inference_locks[cache_key]


def configuracao_yolo(video_info):
    try:
        import torch

        has_cuda = bool(torch.cuda.is_available())
    except ImportError:
        has_cuda = False

    target_fps = YOLO_GPU_TARGET_FPS if has_cuda else YOLO_CPU_TARGET_FPS
    fps = max(float(video_info.get("fps") or target_fps), 1.0)
    vid_stride = max(1, int(round(fps / float(target_fps))))
    return {
        "device": 0 if has_cuda else "cpu",
        "half": has_cuda,
        "batch": 2 if has_cuda else 1,
        "vid_stride": vid_stride,
    }


def metadados_video(video_path):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Instale opencv-python para validar a duracao do video.") from exc

    captura = cv2.VideoCapture(str(video_path))
    try:
        if not captura.isOpened():
            raise ValueError("Nao foi possivel ler o video enviado.")

        fps = float(captura.get(cv2.CAP_PROP_FPS) or 0)
        total_frames = int(captura.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(captura.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(captura.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if fps <= 0 or total_frames <= 0:
            raise ValueError("Nao foi possivel identificar a duracao do video.")

        return {
            "fps": fps,
            "total_frames": total_frames,
            "duration": total_frames / fps,
            "width": width,
            "height": height,
        }
    finally:
        captura.release()


def duracao_video(video_path):
    return float(metadados_video(video_path)["duration"])


def air_box_iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    return inter_area / union if union > 0 else 0.0


def contar_caixas_sem_duplicar(boxes, iou_threshold=0.72):
    mantidas = []
    ordenadas = sorted(boxes, key=lambda item: item["confidence"], reverse=True)
    for item in ordenadas:
        candidata = item["xyxy"]
        if all(air_box_iou(candidata, caixa) < iou_threshold for caixa in mantidas):
            mantidas.append(candidata)
    return len(mantidas)


def area_caixa(box):
    x1, y1, x2, y2 = box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def caixa_corresponde_a_bovino(candidate_box, cattle_boxes):
    """Evita que o detector COCO renomeie um bovino como ave ou humano."""
    candidate_area = area_caixa(candidate_box)
    if candidate_area <= 0:
        return False

    cx = (candidate_box[0] + candidate_box[2]) / 2.0
    cy = (candidate_box[1] + candidate_box[3]) / 2.0
    for cattle in cattle_boxes:
        cattle_area = area_caixa(cattle)
        if cattle_area <= 0:
            continue

        intersection_x1 = max(candidate_box[0], cattle[0])
        intersection_y1 = max(candidate_box[1], cattle[1])
        intersection_x2 = min(candidate_box[2], cattle[2])
        intersection_y2 = min(candidate_box[3], cattle[3])
        intersection = (
            max(0.0, intersection_x2 - intersection_x1)
            * max(0.0, intersection_y2 - intersection_y1)
        )
        overlap_smaller = intersection / min(candidate_area, cattle_area)
        cattle_width = cattle[2] - cattle[0]
        cattle_height = cattle[3] - cattle[1]
        margin_x = cattle_width * 0.12
        margin_y = cattle_height * 0.12
        center_inside = (
            cattle[0] - margin_x <= cx <= cattle[2] + margin_x
            and cattle[1] - margin_y <= cy <= cattle[3] + margin_y
        )
        if air_box_iou(candidate_box, cattle) >= 0.18 or overlap_smaller >= 0.52 or center_inside:
            return True

    return False


def confianca_minima_auxiliar_air(class_name, cattle_boxes):
    minimum = AIR_CLASS_MIN_CONFIDENCE[class_name]
    if len(cattle_boxes) >= 5:
        minimum = max(minimum, 0.70 if class_name == "humano" else 0.72)
    return minimum


def maior_sequencia_positiva(values):
    longest = 0
    current = 0
    for value in values:
        if int(value) > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def consolidar_deteccoes_air(frame_counts, frame_confidences, frames_analisados):
    """Remove classes esporadicas e calcula uma contagem alta, mas estavel."""
    minimum_frames = max(2, min(4, math.ceil(max(frames_analisados, 1) * 0.04)))
    consolidated = {}

    for class_name in AIR_CLASS_IDS:
        counts = [int(value) for value in frame_counts.get(class_name, [])]
        positive_counts = [value for value in counts if value > 0]
        support_frames = len(positive_counts)
        longest_streak = maior_sequencia_positiva(counts)
        accepted = support_frames >= minimum_frames and longest_streak >= 2

        count = 0
        confidence = None
        if accepted:
            sample_size = max(minimum_frames, math.ceil(support_frames * 0.25))
            stable_high_counts = sorted(positive_counts, reverse=True)[:sample_size]
            count = max(1, int(math.floor(float(np.median(stable_high_counts)) + 0.5)))

            confidence_values = frame_confidences.get(class_name, [])
            if confidence_values:
                confidence = float(np.percentile(confidence_values, 75))

        consolidated[class_name] = {
            "accepted": accepted,
            "count": count,
            "confidence": confidence,
            "support_frames": support_frames,
            "longest_streak": longest_streak,
            "support_percent": round(
                support_frames / max(frames_analisados, 1) * 100.0,
                1,
            ),
        }

    return consolidated, minimum_frames


def processar_video_contagem(video_path, video_info=None, progress_callback=None):
    emitir_progresso(progress_callback, 8, "Carregando detector aéreo de bovinos")
    cattle_model, cattle_inference_lock = carregar_yolo(
        "air_cattle",
        AIR_CATTLE_MODEL_PATH,
    )
    general_model, general_inference_lock = carregar_yolo(
        "air_species",
        AIR_GENERAL_MODEL_PATH,
    )
    video_info = video_info or metadados_video(video_path)
    runtime = configuracao_yolo(video_info)
    expected_frames = max(1, math.ceil(video_info["total_frames"] / runtime["vid_stride"]))
    frame_counts = {name: [] for name in AIR_CLASS_IDS}
    frame_confidences = {name: [] for name in AIR_CLASS_IDS}
    cattle_boxes_by_frame = []
    total_detections = 0
    frames_analisados = 0

    emitir_progresso(progress_callback, 12, "Localizando bovinos nos quadros")
    with cattle_inference_lock:
        resultados = cattle_model.predict(
            source=str(video_path),
            classes=[0],
            conf=AIR_CATTLE_CONFIDENCE,
            iou=0.5,
            imgsz=AIR_YOLO_IMGSZ,
            stream=True,
            save=False,
            verbose=False,
            rect=True,
            max_det=300,
            device=runtime["device"],
            half=runtime["half"],
            batch=runtime["batch"],
            vid_stride=runtime["vid_stride"],
        )

        for resultado in resultados:
            frames_analisados += 1
            emitir_progresso(
                progress_callback,
                12 + int(45 * min(frames_analisados / expected_frames, 1.0)),
                f"Detectando bovinos: {frames_analisados} de aproximadamente {expected_frames} quadros",
            )
            boxes = getattr(resultado, "boxes", None)
            cattle_boxes = []

            if boxes is not None and boxes.cls is not None:
                confidences = boxes.conf.cpu().numpy().tolist()
                coordinates = boxes.xyxy.cpu().numpy().tolist()

                for confidence, xyxy in zip(confidences, coordinates):
                    confidence = float(confidence)
                    if confidence < AIR_CATTLE_CONFIDENCE:
                        continue

                    total_detections += 1
                    cattle_boxes.append({
                        "confidence": confidence,
                        "xyxy": tuple(float(value) for value in xyxy),
                    })

            count = contar_caixas_sem_duplicar(cattle_boxes)
            frame_counts["boi"].append(count)
            cattle_boxes_by_frame.append([item["xyxy"] for item in cattle_boxes])
            if cattle_boxes:
                frame_confidences["boi"].append(
                    max(item["confidence"] for item in cattle_boxes)
                )

    general_frames = 0
    emitir_progresso(progress_callback, 58, "Verificando pessoas, aves e outros animais")
    with general_inference_lock:
        resultados = general_model.predict(
            source=str(video_path),
            classes=list(AIR_GENERAL_CLASS_IDS.values()),
            conf=min(AIR_CLASS_MIN_CONFIDENCE.values()),
            iou=0.55,
            imgsz=AIR_YOLO_IMGSZ,
            stream=True,
            save=False,
            verbose=False,
            rect=True,
            max_det=300,
            device=runtime["device"],
            half=runtime["half"],
            batch=runtime["batch"],
            vid_stride=runtime["vid_stride"],
        )

        for frame_index, resultado in enumerate(resultados):
            general_frames += 1
            emitir_progresso(
                progress_callback,
                58 + int(32 * min(general_frames / expected_frames, 1.0)),
                f"Verificando outras classes: {general_frames} de aproximadamente {expected_frames} quadros",
            )
            boxes = getattr(resultado, "boxes", None)
            frame_boxes = {name: [] for name in AIR_GENERAL_CLASS_IDS}
            cattle_boxes = (
                cattle_boxes_by_frame[frame_index]
                if frame_index < len(cattle_boxes_by_frame)
                else []
            )

            if boxes is not None and boxes.cls is not None:
                class_ids = boxes.cls.cpu().numpy().astype(int).tolist()
                confidences = boxes.conf.cpu().numpy().tolist()
                coordinates = boxes.xyxy.cpu().numpy().tolist()

                for class_id, confidence, xyxy in zip(class_ids, confidences, coordinates):
                    class_name = AIR_GENERAL_CLASS_BY_ID.get(int(class_id))
                    if class_name is None:
                        continue

                    confidence = float(confidence)
                    candidate_box = tuple(float(value) for value in xyxy)
                    if confidence < confianca_minima_auxiliar_air(class_name, cattle_boxes):
                        continue
                    if caixa_corresponde_a_bovino(candidate_box, cattle_boxes):
                        continue

                    total_detections += 1
                    frame_boxes[class_name].append({
                        "confidence": confidence,
                        "xyxy": candidate_box,
                    })

            for class_name, class_boxes in frame_boxes.items():
                frame_counts[class_name].append(
                    contar_caixas_sem_duplicar(class_boxes)
                )
                if class_boxes:
                    frame_confidences[class_name].append(
                        max(item["confidence"] for item in class_boxes)
                    )

    frames_analisados = max(frames_analisados, general_frames)
    for class_name, counts in frame_counts.items():
        if len(counts) < frames_analisados:
            counts.extend([0] * (frames_analisados - len(counts)))

    emitir_progresso(progress_callback, 92, "Confirmando deteccoes consistentes")
    consolidated, minimum_frames = consolidar_deteccoes_air(
        frame_counts,
        frame_confidences,
        frames_analisados,
    )
    contagens = {
        class_name: int(details["count"])
        for class_name, details in consolidated.items()
    }
    confidence_by_class = {
        class_name: round(float(details["confidence"]) * 100.0, 2)
        for class_name, details in consolidated.items()
        if details["accepted"] and details["confidence"] is not None
    }
    confirmed_frames = {
        class_name: int(details["support_frames"])
        for class_name, details in consolidated.items()
        if details["accepted"]
    }
    max_confidence = max(confidence_by_class.values(), default=None)

    emitir_progresso(progress_callback, 94, "Organizando a contagem")
    return {
        "modelo": "YOLO11 aéreo para bovinos + YOLO11x auxiliar",
        "contagens": contagens,
        "total_estimado": sum(contagens.values()),
        "total_deteccoes": total_detections,
        "frames_analisados": frames_analisados,
        "vid_stride": runtime["vid_stride"],
        "maior_confianca": max_confidence,
        "confianca_por_classe": confidence_by_class,
        "quadros_confirmados": confirmed_frames,
        "quadros_minimos": minimum_frames,
    }


def montar_log_contagem(resultado, duracao):
    contagens = resultado["contagens"]
    identificados = [
        (AIR_CLASS_LABELS[class_name], int(contagens.get(class_name, 0)))
        for class_name in ("boi", "humano", "cachorro", "gato", "ave")
        if int(contagens.get(class_name, 0)) > 0
    ]

    linhas = [
        f"Tempo do video: {duracao:.2f}s",
        f"Quantidade identificada: {resultado['total_estimado']}",
        f"Precisao: {resultado['maior_confianca']:.2f}%" if resultado["maior_confianca"] is not None else "Precisao: N/A",
    ]

    if identificados:
        linhas.append("")
        for label, quantidade in identificados:
            linhas.append(f"{label}: {quantidade}")
    else:
        linhas.append("")
        linhas.append("Nenhuma classe alvo identificada.")

    return "\n".join(linhas)


def salvar_crop(frame_image, xyxy, crop_path, crop_margin=0.12):
    import cv2

    height, width = frame_image.shape[:2]
    x1, y1, x2, y2 = [float(value) for value in xyxy]
    box_width = max(x2 - x1, 1.0)
    box_height = max(y2 - y1, 1.0)
    left = max(int(x1 - box_width * crop_margin), 0)
    top = max(int(y1 - box_height * crop_margin), 0)
    right = min(int(x2 + box_width * crop_margin), width)
    bottom = min(int(y2 + box_height * crop_margin), height)
    crop = frame_image[top:bottom, left:right]
    cv2.imwrite(str(crop_path), crop)
    return {"x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2)}


def processar_video_especies(video_path, work_dir, video_info=None, progress_callback=None):
    emitir_progresso(progress_callback, 8, "Carregando detector de video")
    model, model_inference_lock = carregar_yolo("air_species", AIR_SPECIES_MODEL_PATH)
    video_info = video_info or metadados_video(video_path)
    runtime = configuracao_yolo(video_info)
    expected_frames = max(1, math.ceil(video_info["total_frames"] / runtime["vid_stride"]))
    crops_dir = Path(work_dir) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    candidates = {}
    class_stats = {
        name: {"frames_detected": 0, "detections": 0, "max_confidence": None}
        for name in AIR_SPECIES_COCO_IDS
    }
    frames_analyzed = 0

    emitir_progresso(progress_callback, 12, "Localizando animais no video")
    with model_inference_lock:
        resultados = model.predict(
            source=str(video_path),
            classes=list(AIR_SPECIES_COCO_IDS.values()),
            conf=0.35,
            imgsz=SPECIES_YOLO_IMGSZ,
            stream=True,
            save=False,
            verbose=False,
            rect=True,
            max_det=300,
            device=runtime["device"],
            half=runtime["half"],
            batch=runtime["batch"],
            vid_stride=runtime["vid_stride"],
        )

        for frame_index, resultado in enumerate(resultados):
            frames_analyzed += 1
            emitir_progresso(
                progress_callback,
                12 + int(66 * min(frames_analyzed / expected_frames, 1.0)),
                f"Analisando video: {frames_analyzed} de aproximadamente {expected_frames} quadros",
            )
            boxes = getattr(resultado, "boxes", None)
            frame_image = getattr(resultado, "orig_img", None)
            if boxes is None or boxes.cls is None or frame_image is None:
                continue

            seen_in_frame = set()
            class_ids = boxes.cls.cpu().numpy().astype(int).tolist()
            confidences = boxes.conf.cpu().numpy().tolist()
            coordinates = boxes.xyxy.cpu().numpy().tolist()

            for class_id, confidence, xyxy in zip(class_ids, confidences, coordinates):
                class_name = AIR_SPECIES_BY_ID.get(int(class_id))
                if class_name is None:
                    continue

                stats = class_stats[class_name]
                stats["detections"] = int(stats["detections"]) + 1
                confidence = float(confidence)
                atual = stats["max_confidence"]
                stats["max_confidence"] = round(confidence, 4) if atual is None else max(float(atual), round(confidence, 4))
                seen_in_frame.add(class_name)

                current = candidates.get(class_name)
                if current is None or confidence > float(current["confidence"]):
                    crop_path = crops_dir / f"{class_name}_best.png"
                    bbox = salvar_crop(frame_image, xyxy, crop_path)
                    candidates[class_name] = {
                        "class_name": class_name,
                        "frame_index": frame_index,
                        "confidence": confidence,
                        "bbox": bbox,
                        "crop_path": crop_path,
                    }

            for class_name in seen_in_frame:
                class_stats[class_name]["frames_detected"] = int(class_stats[class_name]["frames_detected"]) + 1

    species_found = []
    rejected_classes = []
    candidate_items = [item for item in class_stats.items() if item[0] in candidates]
    for candidate_index, (class_name, stats) in enumerate(candidate_items):
        candidate = candidates.get(class_name)
        if candidate is None:
            continue

        emitir_progresso(
            progress_callback,
            80 + int(14 * (candidate_index + 1) / max(len(candidate_items), 1)),
            f"Identificando especie {candidate_index + 1} de {len(candidate_items)}",
        )

        if class_name == "pessoa":
            classificacao = {
                "label": AIR_SPECIES_FALLBACK[class_name],
                "raw_label": class_name,
                "confidence": round(float(candidate["confidence"]) * 100.0, 2),
                "source": "yolo",
            }
        else:
            try:
                classificacao = prever_especie_por_imagem(candidate["crop_path"], top_k=1)
                classificacao["source"] = "biovision_pytorch"
            except Exception as exc:
                classificacao = {
                    "label": AIR_SPECIES_FALLBACK.get(class_name, class_name),
                    "raw_label": class_name,
                    "confidence": round(float(candidate["confidence"]) * 100.0, 2),
                    "source": "yolo",
                    "note": str(exc),
                }

        label_identificado = classificacao["keyword"] if "keyword" in classificacao else classificacao["label"]
        raw_identificado = classificacao.get("raw_keyword") or classificacao.get("raw_label")
        dados_taxon = buscar_informacoes_especie(raw_identificado or label_identificado)
        selector_label = primeiro_nome_popular_ou_cientifico(dados_taxon, label_identificado)

        species_found.append({
            "label": label_identificado,
            "raw_label": raw_identificado,
            "selector_label": selector_label,
            "dados_taxon": dados_taxon,
            "source": classificacao.get("source", "biovision_pytorch"),
            "confidence": classificacao["confidence"],
            "yolo_class": class_name,
            "yolo_label": AIR_SPECIES_LABELS.get(class_name, class_name),
            "frames_detected": int(stats["frames_detected"]),
            "detections": int(stats["detections"]),
            "best_yolo_confidence": round(float(candidate["confidence"]) * 100.0, 2),
            "best_frame": int(candidate["frame_index"]),
            "bbox": candidate["bbox"],
        })

    for class_name, stats in class_stats.items():
        if int(stats["detections"]) == 0:
            continue
        if not any(item["yolo_class"] == class_name for item in species_found):
            rejected_classes.append({
                "class_name": class_name,
                "class_label": AIR_SPECIES_LABELS.get(class_name, class_name),
                "frames_detected": int(stats["frames_detected"]),
                "detections": int(stats["detections"]),
                "max_confidence": stats["max_confidence"],
            })

    species_found.sort(key=lambda item: float(item["confidence"]), reverse=True)
    labels = [str(item["selector_label"]) for item in species_found]
    summary_text = "Nenhuma especie identificada no video."
    if labels:
        summary_text = "Foi identificado: " + juntar_pt(labels) + "."

    return {
        "ok": True,
        "mode": "video_species",
        "summary_text": summary_text,
        "species_found": species_found,
        "rejected_classes": rejected_classes,
        "summary": {
            "classes_detected": len([stats for stats in class_stats.values() if int(stats["detections"]) > 0]),
            "by_class": class_stats,
            "frames_sampled": frames_analyzed,
            "vid_stride": runtime["vid_stride"],
        },
    }


def juntar_pt(valores):
    if len(valores) == 1:
        return valores[0]
    if len(valores) == 2:
        return f"{valores[0]} e {valores[1]}"
    return f"{', '.join(valores[:-1])} e {valores[-1]}"


SOUND_ALLOWED_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
    ".ogg", ".opus", ".wav", ".webm", ".3gp", ".wma",
}


def audio_permitido(filename):
    return Path(filename).suffix.lower() in SOUND_ALLOWED_EXTENSIONS


def ffmpeg_path():
    return shutil.which("ffmpeg")


def carregar_audio_com_pyav(source, sample_rate=48000):
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "Para ler MP3, M4A, MP4, MOV e outros formatos sem ffmpeg instalado, "
            "instale a dependencia av do requirements.txt."
        ) from exc

    source = Path(source).resolve()
    chunks = []
    container = av.open(str(source))
    try:
        audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if audio_stream is None:
            raise RuntimeError("Nenhuma faixa de audio foi encontrada no arquivo enviado.")

        resampler = av.audio.resampler.AudioResampler(
            format="s16",
            layout="mono",
            rate=sample_rate,
        )

        def collect(frame):
            array = frame.to_ndarray()
            chunks.append(array.reshape(-1).astype(np.float32) / 32768.0)

        def iter_resampled(value):
            if value is None:
                return []
            if isinstance(value, list):
                return value
            return [value]

        for packet in container.demux(audio_stream):
            for frame in packet.decode():
                for resampled in iter_resampled(resampler.resample(frame)):
                    collect(resampled)

        for resampled in iter_resampled(resampler.resample(None)):
            collect(resampled)
    finally:
        container.close()

    if chunks:
        samples = np.concatenate(chunks)
    else:
        samples = np.zeros(0, dtype=np.float32)
    duration = float(len(samples)) / float(sample_rate) if sample_rate else 0.0
    return samples, sample_rate, duration


def preparar_audio_birdnet(source):
    import wave

    source = Path(source).resolve()
    output = source.parent / f"{source.stem}_birdnet.wav"
    ffmpeg = ffmpeg_path()
    if ffmpeg is not None:
        command = [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "wav",
            str(output),
        ]
        subprocess.run(command, check=True)
        with wave.open(str(output), "rb") as wav_file:
            duration = wav_file.getnframes() / max(float(wav_file.getframerate()), 1.0)
        return output, duration

    samples, sample_rate, duration = carregar_audio_com_pyav(source, sample_rate=48000)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return output, duration


def obter_modelo_birdnet():
    global birdnet_model, birdnet_runtime, birdnet_species_filter, birdnet_error

    if birdnet_model is not None:
        return birdnet_model
    if birdnet_error is not None:
        raise RuntimeError(birdnet_error)

    with birdnet_model_lock:
        if birdnet_model is not None:
            return birdnet_model
        if birdnet_error is not None:
            raise RuntimeError(birdnet_error)

        try:
            from ai_edge_litert.interpreter import Interpreter

            if not BIRDNET_MODEL_PATH.is_file():
                raise FileNotFoundError(
                    f"Modelo acústico não encontrado em: {BIRDNET_MODEL_PATH}"
                )
            if not BIRDNET_LABELS_PATH.is_file():
                raise FileNotFoundError(
                    f"Índice acústico não encontrado em: {BIRDNET_LABELS_PATH}"
                )

            labels = [
                line.strip()
                for line in BIRDNET_LABELS_PATH.read_text(
                    encoding="utf-8-sig"
                ).splitlines()
                if line.strip()
            ]
            interpreter = Interpreter(
                model_path=str(BIRDNET_MODEL_PATH),
                num_threads=2,
            )
            input_details = interpreter.get_input_details()[0]
            output_details = interpreter.get_output_details()[0]
            input_shape = np.asarray(input_details["shape"], dtype=np.int64)
            if input_shape.size != 2 or int(input_shape[-1]) <= 0:
                raise RuntimeError(
                    f"Formato de entrada acústica inesperado: {input_shape.tolist()}"
                )

            segment_samples = int(input_shape[-1])
            interpreter.resize_tensor_input(
                int(input_details["index"]),
                [BIRDNET_BATCH_SIZE, segment_samples],
                strict=False,
            )
            interpreter.allocate_tensors()
            input_details = interpreter.get_input_details()[0]
            output_details = interpreter.get_output_details()[0]
            output_shape = np.asarray(output_details["shape"], dtype=np.int64)
            if output_shape.size != 2 or int(output_shape[-1]) != len(labels):
                raise RuntimeError(
                    "O índice acústico não combina com a saída do modelo "
                    f"({len(labels)} rótulos para {output_shape.tolist()})."
                )

            supported = {
                str(name).replace(" ", "_").strip().lower(): str(name).strip()
                for name in (class_names or [])
            }
            birdnet_species_filter = []
            for index, label in enumerate(labels):
                scientific_name, _, common_name = str(label).partition("_")
                normalized = scientific_name.replace(" ", "_").strip().lower()
                if normalized in supported:
                    birdnet_species_filter.append({
                        "index": index,
                        "raw_label": supported[normalized],
                        "common_name": common_name.strip(),
                    })

            if not birdnet_species_filter:
                raise RuntimeError(
                    "Nenhuma espécie do índice do BioVision foi encontrada no modelo acústico."
                )

            birdnet_model = {
                "interpreter": interpreter,
                "input_index": int(input_details["index"]),
                "output_index": int(output_details["index"]),
                "segment_samples": segment_samples,
                "batch_size": BIRDNET_BATCH_SIZE,
            }
            birdnet_runtime = {
                "version": BIRDNET_MODEL_VERSION,
                "backend": BIRDNET_BACKEND,
                "precision": BIRDNET_PRECISION,
                "supported_species": len(birdnet_species_filter),
                "source": "bundled",
            }
            print(
                "Modelo acústico BirdNET carregado "
                f"({BIRDNET_MODEL_VERSION}/{BIRDNET_BACKEND}/{BIRDNET_PRECISION})."
            )
            return birdnet_model
        except ImportError as exc:
            birdnet_error = "LiteRT não está instalado para executar o modelo acústico."
            raise RuntimeError(birdnet_error) from exc
        except Exception as exc:
            birdnet_error = f"Erro ao carregar o modelo acústico BirdNET: {exc}"
            raise RuntimeError(birdnet_error) from exc


def identificar_audio_birdnet(source, progress_callback=None):
    emitir_progresso(progress_callback, 12, "Preparando o áudio")
    prepared_audio, duration = preparar_audio_birdnet(source)
    emitir_progresso(progress_callback, 24, "Classificador acústico pronto")
    model = obter_modelo_birdnet()
    samples, sample_rate, decoded_duration = carregar_audio_com_pyav(
        prepared_audio,
        sample_rate=BIRDNET_SAMPLE_RATE,
    )
    if sample_rate != BIRDNET_SAMPLE_RATE:
        raise RuntimeError(
            f"Taxa de amostragem acústica inválida: {sample_rate} Hz."
        )
    duration = decoded_duration or duration

    emitir_progresso(progress_callback, 38, "Comparando vocalizações em segmentos de áudio")
    grouped = defaultdict(list)
    with birdnet_inference_lock:
        interpreter = model["interpreter"]
        segment_samples = int(model["segment_samples"])
        batch_size = int(model["batch_size"])
        segment_count = max(1, math.ceil(len(samples) / segment_samples))
        filter_indexes = np.asarray(
            [item["index"] for item in birdnet_species_filter],
            dtype=np.int64,
        )

        for batch_start in range(0, segment_count, batch_size):
            actual_batch_size = min(batch_size, segment_count - batch_start)
            batch = np.zeros((batch_size, segment_samples), dtype=np.float32)
            for offset in range(actual_batch_size):
                segment_index = batch_start + offset
                start = segment_index * segment_samples
                end = min(start + segment_samples, len(samples))
                if end > start:
                    batch[offset, :end - start] = samples[start:end]

            interpreter.set_tensor(model["input_index"], batch)
            interpreter.invoke()
            logits = np.asarray(
                interpreter.get_tensor(model["output_index"]),
                dtype=np.float32,
            )[:actual_batch_size]
            probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -40.0, 40.0)))
            supported_probabilities = probabilities[:, filter_indexes]

            for offset in range(actual_batch_size):
                scores = supported_probabilities[offset]
                top_count = min(5, len(scores))
                top_positions = np.argpartition(scores, -top_count)[-top_count:]
                top_positions = top_positions[np.argsort(scores[top_positions])[::-1]]
                segment_index = batch_start + offset
                start_seconds = segment_index * segment_samples / BIRDNET_SAMPLE_RATE
                end_seconds = min(
                    (segment_index + 1) * segment_samples / BIRDNET_SAMPLE_RATE,
                    duration,
                )
                for position in top_positions:
                    confidence = float(scores[position])
                    if confidence < BIRDNET_SECONDARY_CONFIDENCE:
                        continue
                    species = birdnet_species_filter[int(position)]
                    grouped[species["raw_label"]].append({
                        "confidence": confidence,
                        "common_name": species["common_name"],
                        "start_time": f"{start_seconds:.2f}s",
                        "end_time": f"{end_seconds:.2f}s",
                    })

            completed = batch_start + actual_batch_size
            progress = 38 + round(42 * completed / segment_count)
            emitir_progresso(
                progress_callback,
                progress,
                "Comparando vocalizações em segmentos de áudio",
            )

    emitir_progresso(progress_callback, 84, "Validando a confiança da identificação")
    ranked = []
    for species, detections in grouped.items():
        ordered = sorted(detections, key=lambda item: item["confidence"], reverse=True)
        top_confidences = [float(item["confidence"]) for item in ordered[:3]]
        dados_taxon = buscar_informacoes_especie(species)
        ranked.append({
            "species": species,
            "label": nome_classe_legivel(species),
            "selector_label": primeiro_nome_popular_ou_cientifico(dados_taxon, species),
            "dados_taxon": dados_taxon,
            "confidence": round(max(top_confidences), 4),
            "mean_top_confidence": round(sum(top_confidences) / len(top_confidences), 4),
            "detections": len(detections),
            "common_name_birdnet": ordered[0]["common_name"],
            "segments": ordered[:8],
        })

    ranked.sort(
        key=lambda item: (
            float(item["confidence"]),
            float(item["mean_top_confidence"]),
            int(item["detections"]),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    ok = best is not None
    reason = "Nenhuma vocalização compatível com as espécies do BioVision."

    if best is not None:
        reason = "Identificação acústica forte."
        best_confidence = float(best["confidence"])
        if best_confidence < BIRDNET_ACCEPT_CONFIDENCE:
            ok = False
            reason = "Confiança abaixo do mínimo para uma identificação segura."
        elif int(best["detections"]) < 2 and best_confidence < 0.78:
            ok = False
            reason = "A vocalização apareceu poucas vezes para confirmar a espécie."
        elif second is not None and best_confidence < 0.85:
            margin = best_confidence - float(second["confidence"])
            if margin < BIRDNET_MIN_MARGIN:
                ok = False
                reason = "As espécies candidatas ficaram muito próximas; o resultado foi omitido."

    visible_species = ranked[:1] if ok else []
    return {
        "ok": ok,
        "species": best["species"] if ok and best else None,
        "label": best["selector_label"] if best else "",
        "confidence": float(best["confidence"]) if best else 0.0,
        "aligned_hashes": 0,
        "query_hashes": 0,
        "duration_seconds": round(float(duration), 2),
        "reason": reason,
        "top_species": visible_species,
        "engine": "birdnet",
        "runtime": dict(birdnet_runtime or {}),
    }


def identificar_audio_especie(source, progress_callback=None):
    return identificar_audio_birdnet(
        source,
        progress_callback=progress_callback,
    )


def montar_payload_contagem(video_path, video_info, progress_callback=None):
    resultado = processar_video_contagem(
        video_path,
        video_info=video_info,
        progress_callback=progress_callback,
    )
    duracao = float(video_info["duration"])
    detected_classes = [
        class_name
        for class_name, count in resultado["contagens"].items()
        if int(count) > 0
    ]

    emitir_progresso(progress_callback, 96, "Consultando informacoes dos animais")
    taxon_details, database_available = buscar_informacoes_classes_air(detected_classes)
    resultado["identificados"] = [
        {
            "classe": class_name,
            "nome": AIR_CLASS_LABELS[class_name],
            "quantidade": int(resultado["contagens"][class_name]),
            "confianca": resultado["confianca_por_classe"].get(class_name),
            "quadros_confirmados": resultado["quadros_confirmados"].get(class_name, 0),
            "dados_taxon": taxon_details.get(class_name),
        }
        for class_name in detected_classes
    ]
    resultado["banco_disponivel"] = database_available
    resultado["duracao"] = round(duracao, 2)
    emitir_progresso(progress_callback, 98, "Montando resultado")
    return resultado


def montar_payload_imagem(source, progress_callback=None):
    emitir_progresso(progress_callback, 14, "Carregando modelo de especies")
    resultado = prever_especie_por_imagem(source, top_k=1)
    emitir_progresso(progress_callback, 82, "Consultando dados da especie")
    dados_mysql = buscar_informacoes_especie(resultado["raw_keyword"])
    payload = {
        "keyword": resultado["keyword"],
        "raw_keyword": resultado["raw_keyword"],
        "confidence": resultado["confidence"],
        "dados_taxon": dados_mysql,
    }
    if not dados_mysql:
        payload["aviso"] = "Especie identificada, mas nao encontrada no banco de dados local."
    emitir_progresso(progress_callback, 96, "Montando resultado")
    return payload


def montar_payload_video_especies(video_path, work_dir, video_info, progress_callback=None):
    resultado = processar_video_especies(
        video_path,
        work_dir,
        video_info=video_info,
        progress_callback=progress_callback,
    )
    resultado["duracao"] = round(float(video_info["duration"]), 2)
    emitir_progresso(progress_callback, 97, "Montando resultado")
    return resultado


def montar_payload_audio(audio_path, progress_callback=None):
    resultado = identificar_audio_especie(audio_path, progress_callback=progress_callback)
    top_species = (resultado.get("top_species") or [])[:1]
    melhor = top_species[0] if top_species else {}
    dados_mysql = melhor.get("dados_taxon") if resultado["ok"] else None
    if resultado["ok"] and not dados_mysql:
        dados_mysql = buscar_informacoes_especie(resultado.get("species") or resultado.get("label"))

    selector_label = ""
    if resultado["ok"]:
        selector_label = melhor.get("selector_label") or primeiro_nome_popular_ou_cientifico(
            dados_mysql,
            resultado.get("species") or resultado.get("label"),
        )
    summary_text = (
        f"Foi identificado: {selector_label}."
        if resultado["ok"]
        else "Nao foi possivel identificar o audio com seguranca."
    )
    payload = {
        "ok": resultado["ok"],
        "mode": "sound",
        "keyword": selector_label,
        "raw_keyword": resultado.get("species"),
        "selector_label": selector_label,
        "summary_text": summary_text,
        "confidence": round(float(resultado["confidence"]) * 100.0, 2),
        "dados_taxon": dados_mysql,
        "reason": resultado["reason"],
        "aligned_hashes": resultado["aligned_hashes"],
        "query_hashes": resultado["query_hashes"],
        "duration_seconds": resultado["duration_seconds"],
        "top_species": top_species if resultado["ok"] else [],
        "engine": resultado.get("engine", "birdnet"),
    }
    if not resultado["ok"]:
        payload["erro"] = resultado["reason"]
    emitir_progresso(progress_callback, 97, "Montando resultado")
    return payload


def _carregar_modelos_na_inicializacao():
    modelos = (
        ("classificador de imagens", obter_modelo_especies),
        (
            "identificador de vídeos",
            lambda: carregar_yolo("air_species", AIR_SPECIES_MODEL_PATH),
        ),
        (
            "detector aéreo de bovinos",
            lambda: carregar_yolo("air_cattle", AIR_CATTLE_MODEL_PATH),
        ),
        ("classificador de áudio", obter_modelo_birdnet),
    )
    inicio = time.perf_counter()
    print("Inicializando os modelos do BioVision antes de abrir o servidor...")

    for nome, carregar in modelos:
        etapa = time.perf_counter()
        try:
            carregar()
        except Exception as exc:
            raise RuntimeError(
                f"Não foi possível inicializar o {nome}: {exc}"
            ) from exc
        nome_exibicao = nome[:1].upper() + nome[1:]
        print(f"[BioVision] {nome_exibicao} pronto em {time.perf_counter() - etapa:.1f}s.")

    print(
        "[BioVision] Todos os modelos estão prontos "
        f"em {time.perf_counter() - inicio:.1f}s."
    )


_preload_lock = threading.Lock()
_models_preloaded = False


def carregar_modelos_na_inicializacao():
    global _models_preloaded
    with _preload_lock:
        if _models_preloaded:
            return
        _carregar_modelos_na_inicializacao()
        _models_preloaded = True
