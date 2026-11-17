import shutil
import tempfile
from pathlib import Path

from flask import current_app, g, jsonify, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from . import model_service
from .config import AUDIO_MAX_BYTES, AUDIO_MAX_SECONDS, IMAGE_MAX_BYTES, VIDEO_MAX_BYTES
from .media_security import InvalidMedia, MediaTooLarge, save_upload, validate_av, validate_image


def resposta_job(job_id):
    return jsonify({
        "job_id": job_id, "status": "queued", "progress": 2,
        "stage": "Aguardando processamento",
        "status_url": url_for("consultar_job", job_id=job_id),
    }), 202


def iniciar_job_upload(kind, field, max_bytes, task_factory, *, target=False):
    # Não existe modo síncrono: todo upload reserva uma vaga antes de ser lido.
    job_id = None
    work_dir = None
    queued = False
    try:
        job_id = model_service.reservar_job(kind, g.visitor_id)
        request.max_content_length = max_bytes + 64 * 1024
        files = list(request.files.items(multi=True))
        if len(files) != 1 or files[0][0] != field or not files[0][1].filename:
            raise InvalidMedia("Selecione um único arquivo de mídia para enviar.")
        allowed_fields = {"target_class"} if target else set()
        if set(request.form) - allowed_fields or any(len(request.form.getlist(key)) != 1 for key in request.form):
            raise InvalidMedia("Envie apenas a mídia e as opções do formulário.")
        target_class = request.form.get("target_class", "boi").strip().lower() if target else None
        if target and target_class not in model_service.AIR_COUNT_TARGETS:
            raise InvalidMedia("Escolha Bovinos, Humanos ou Aves para a contagem.")
        work_dir = Path(tempfile.mkdtemp(prefix="biovision_upload_"))
        upload_path = save_upload(files[0][1], work_dir, max_bytes)

        def task(progress):
            return task_factory(upload_path, work_dir, progress, target_class)

        model_service.enfileirar_job(job_id, work_dir, task)
        queued = True
        return resposta_job(job_id)
    except model_service.JobLimitError as exc:
        response = jsonify(erro=str(exc))
        response.status_code = exc.status
        response.headers["Retry-After"] = "15"
        return response
    except MediaTooLarge as exc:
        return jsonify(erro=str(exc)), 413
    except InvalidMedia as exc:
        return jsonify(erro=str(exc)), 400
    except HTTPException:
        raise
    except Exception:
        current_app.logger.exception("Falha ao receber mídia")
        return jsonify(erro="Não foi possível receber o arquivo. Tente novamente."), 500
    finally:
        if not queued:
            if job_id:
                model_service.descartar_reserva(job_id)
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)


def consultar_job(job_id):
    payload = model_service.payload_job(job_id, g.visitor_id)
    if payload is None:
        return jsonify(erro="Análise não encontrada ou expirada."), 404
    return jsonify(payload)


def index():
    return render_template("index.html")


def identificar():
    return render_template("identificar.html")


def biovision_air():
    return render_template("air.html")


def processar_contagem_animais():
    def task(path, _work_dir, progress, target_class):
        path, info = validate_av(path, kind="video", max_seconds=model_service.AIR_MAX_VIDEO_SECONDS, rename=True)
        return model_service.montar_payload_contagem(path, info, progress, target_class=target_class)
    return iniciar_job_upload("air_count", "video", VIDEO_MAX_BYTES, task, target=True)


def classificar():
    def task(path, _work_dir, progress, _target):
        return model_service.montar_payload_imagem(validate_image(path), progress)
    return iniciar_job_upload("image_species", "imagem", IMAGE_MAX_BYTES, task)


def classificar_video():
    def task(path, work_dir, progress, _target):
        path, info = validate_av(path, kind="video", max_seconds=model_service.IDENTIFY_VIDEO_MAX_SECONDS, rename=True)
        return model_service.montar_payload_video_especies(path, work_dir, info, progress)
    return iniciar_job_upload("video_species", "video", VIDEO_MAX_BYTES, task)


def classificar_audio():
    def task(path, _work_dir, progress, _target):
        path, _info = validate_av(path, kind="audio", max_seconds=AUDIO_MAX_SECONDS, rename=True)
        return model_service.montar_payload_audio(path, progress)
    return iniciar_job_upload("sound_species", "audio", AUDIO_MAX_BYTES, task)


def service_worker():
    return current_app.view_functions["static"]("service-worker.js")


def health():
    return jsonify(status="ready")


def register_routes(app):
    app.add_url_rule("/biovision/jobs/<job_id>", endpoint="consultar_job", view_func=consultar_job)
    app.add_url_rule("/biovision/", endpoint="index", view_func=index)
    app.add_url_rule("/biovision/identificar", endpoint="identificar", view_func=identificar)
    app.add_url_rule("/biovision/air", endpoint="biovision_air", view_func=biovision_air)
    app.add_url_rule("/biovision/air/", endpoint="biovision_air", view_func=biovision_air)
    app.add_url_rule("/biovision/contar-animais", endpoint="processar_contagem_animais",
                     view_func=processar_contagem_animais, methods=["POST"])
    app.add_url_rule("/biovision/classificar", endpoint="classificar", view_func=classificar, methods=["POST"])
    app.add_url_rule("/biovision/classificar-video", endpoint="classificar_video", view_func=classificar_video, methods=["POST"])
    app.add_url_rule("/biovision/classificar-audio", endpoint="classificar_audio", view_func=classificar_audio, methods=["POST"])
    app.add_url_rule("/biovision/service-worker.js", endpoint="service_worker", view_func=service_worker)
    app.add_url_rule("/biovision/health", endpoint="health", view_func=health)
