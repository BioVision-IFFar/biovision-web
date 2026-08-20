import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import current_app, jsonify, render_template, request, url_for
from werkzeug.utils import secure_filename

from . import model_service


def salvar_upload_temporario(arquivo, temp_dir):
    filename = secure_filename(arquivo.filename or "upload.bin")
    if not filename:
        filename = "upload.bin"
    caminho = Path(temp_dir) / filename
    arquivo.save(caminho)
    return caminho, filename


def requisicao_assincrona():
    value = str(
        request.args.get("async")
        or request.headers.get("X-BioVision-Async")
        or ""
    ).lower()
    return value in {"1", "true", "yes"}


def resposta_job(job_id):
    return jsonify({
        "job_id": job_id,
        "status": "queued",
        "progress": 2,
        "stage": "Aguardando processamento",
        "status_url": url_for("consultar_job", job_id=job_id),
    }), 202


def iniciar_job_upload(kind, prefix, arquivo, task_factory):
    work_dir = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        upload_path, _filename = salvar_upload_temporario(arquivo, work_dir)
        task = task_factory(upload_path, work_dir)
        job_id = model_service.criar_job(kind, work_dir, task)
        return resposta_job(job_id)
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def consultar_job(job_id):
    payload = model_service.payload_job(job_id)
    if payload is None:
        return jsonify({"erro": "Analise nao encontrada ou expirada."}), 404
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


def index():
    return render_template("index.html")


def identificar():
    return render_template("identificar.html")


def biovision_air():
    return render_template("air.html")


def processar_contagem_animais():
    if "video" not in request.files:
        return jsonify({"erro": "Video nao enviado."}), 400

    arquivo_video = request.files["video"]
    if arquivo_video.filename == "":
        return jsonify({"erro": "Nenhum video selecionado."}), 400

    filename = secure_filename(arquivo_video.filename)
    if not model_service.video_permitido(filename):
        return jsonify({
            "erro": "Formato de video nao suportado. Use MP4, MOV, AVI, MKV, M4V ou WEBM."
        }), 400

    if requisicao_assincrona():
        def task_factory(video_path, _work_dir):
            video_info = model_service.metadados_video(video_path)
            if video_info["duration"] > model_service.AIR_MAX_VIDEO_SECONDS:
                raise ValueError(
                    f"O video tem {video_info['duration']:.2f} segundos e ultrapassa "
                    f"o limite de {model_service.AIR_MAX_VIDEO_SECONDS} segundos."
                )
            return lambda progress: model_service.montar_payload_contagem(
                video_path,
                video_info,
                progress,
            )

        try:
            return iniciar_job_upload(
                "air_count",
                "biovision_count_",
                arquivo_video,
                task_factory,
            )
        except ValueError as exc:
            return jsonify({"erro": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"erro": str(exc)}), 503
        except Exception as exc:
            return jsonify({"erro": f"Nao foi possivel receber o video: {exc}"}), 500

    with tempfile.TemporaryDirectory(prefix="biovision_count_") as temp_dir:
        video_path, _filename = salvar_upload_temporario(arquivo_video, temp_dir)
        try:
            video_info = model_service.metadados_video(video_path)
            if video_info["duration"] > model_service.AIR_MAX_VIDEO_SECONDS:
                return jsonify({
                    "erro": (
                        f"O video tem {video_info['duration']:.2f} segundos e ultrapassa "
                        f"o limite de {model_service.AIR_MAX_VIDEO_SECONDS} segundos."
                    )
                }), 400
            resultado = model_service.montar_payload_contagem(video_path, video_info)
        except ValueError as exc:
            return jsonify({"erro": str(exc)}), 400
        except (FileNotFoundError, RuntimeError) as exc:
            return jsonify({"erro": str(exc)}), 500
        except Exception as exc:
            return jsonify({"erro": f"Falha ao processar o video com YOLO: {exc}"}), 500

    return jsonify(resultado)


def classificar():
    if "imagem" not in request.files:
        return jsonify({"erro": "Imagem nao enviada."}), 400

    arquivo_imagem = request.files["imagem"]
    if arquivo_imagem.filename == "":
        return jsonify({"erro": "Nenhuma imagem selecionada."}), 400

    if requisicao_assincrona():
        try:
            return iniciar_job_upload(
                "image_species",
                "biovision_image_",
                arquivo_imagem,
                lambda image_path, _work_dir: (
                    lambda progress: model_service.montar_payload_imagem(
                        image_path,
                        progress,
                    )
                ),
            )
        except RuntimeError as exc:
            return jsonify({"erro": str(exc)}), 503
        except Exception as exc:
            return jsonify({"erro": f"Nao foi possivel receber a imagem: {exc}"}), 500

    imagem_bytes = arquivo_imagem.read()
    try:
        payload = model_service.montar_payload_imagem(imagem_bytes)
    except RuntimeError as exc:
        return jsonify({"erro": str(exc)}), 500
    except Exception as exc:
        return jsonify({"erro": f"Falha ao identificar a imagem: {exc}"}), 500
    return jsonify(payload)


def classificar_video():
    if "video" not in request.files:
        return jsonify({"erro": "Video nao enviado."}), 400

    arquivo_video = request.files["video"]
    if arquivo_video.filename == "":
        return jsonify({"erro": "Nenhum video selecionado."}), 400

    filename = secure_filename(arquivo_video.filename)
    if not model_service.video_permitido(filename):
        return jsonify({
            "erro": "Formato de video nao suportado. Use MP4, MOV, AVI, MKV, M4V ou WEBM."
        }), 400

    if requisicao_assincrona():
        def task_factory(video_path, work_dir):
            video_info = model_service.metadados_video(video_path)
            if video_info["duration"] > model_service.IDENTIFY_VIDEO_MAX_SECONDS:
                raise ValueError(
                    f"O video tem {video_info['duration']:.2f} segundos e ultrapassa "
                    f"o limite de {model_service.IDENTIFY_VIDEO_MAX_SECONDS} segundos."
                )
            return lambda progress: model_service.montar_payload_video_especies(
                video_path,
                work_dir,
                video_info,
                progress,
            )

        try:
            return iniciar_job_upload(
                "video_species",
                "biovision_video_species_",
                arquivo_video,
                task_factory,
            )
        except ValueError as exc:
            return jsonify({"erro": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"erro": str(exc)}), 503
        except Exception as exc:
            return jsonify({"erro": f"Nao foi possivel receber o video: {exc}"}), 500

    with tempfile.TemporaryDirectory(prefix="biovision_video_species_") as temp_dir:
        video_path, _filename = salvar_upload_temporario(arquivo_video, temp_dir)
        try:
            video_info = model_service.metadados_video(video_path)
            if video_info["duration"] > model_service.IDENTIFY_VIDEO_MAX_SECONDS:
                return jsonify({
                    "erro": (
                        f"O video tem {video_info['duration']:.2f} segundos e ultrapassa "
                        f"o limite de {model_service.IDENTIFY_VIDEO_MAX_SECONDS} segundos."
                    )
                }), 400
            resultado = model_service.montar_payload_video_especies(
                video_path,
                temp_dir,
                video_info,
            )
        except ValueError as exc:
            return jsonify({"erro": str(exc)}), 400
        except (FileNotFoundError, RuntimeError) as exc:
            return jsonify({"erro": str(exc)}), 500
        except Exception as exc:
            return jsonify({"erro": f"Falha ao identificar especies no video: {exc}"}), 500

    return jsonify(resultado)


def classificar_audio():
    if "audio" not in request.files:
        return jsonify({"erro": "Audio nao enviado."}), 400

    arquivo_audio = request.files["audio"]
    if arquivo_audio.filename == "":
        return jsonify({"erro": "Nenhum audio selecionado."}), 400

    filename = secure_filename(arquivo_audio.filename)
    if not model_service.audio_permitido(filename):
        return jsonify({
            "erro": "Formato de audio nao suportado. Use MP3, WAV, M4A, OGG, WEBM, MP4 ou MOV."
        }), 400

    if requisicao_assincrona():
        try:
            return iniciar_job_upload(
                "sound_species",
                "biovision_sound_",
                arquivo_audio,
                lambda audio_path, _work_dir: (
                    lambda progress: model_service.montar_payload_audio(
                        audio_path,
                        progress,
                    )
                ),
            )
        except RuntimeError as exc:
            return jsonify({"erro": str(exc)}), 503
        except Exception as exc:
            return jsonify({"erro": f"Nao foi possivel receber o audio: {exc}"}), 500

    with tempfile.TemporaryDirectory(prefix="biovision_sound_") as temp_dir:
        audio_path, _filename = salvar_upload_temporario(arquivo_audio, temp_dir)
        try:
            payload = model_service.montar_payload_audio(audio_path)
        except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
            return jsonify({"erro": str(exc)}), 500
        except Exception as exc:
            return jsonify({"erro": f"Falha ao identificar audio: {exc}"}), 500
    return jsonify(payload)


def service_worker():
    return current_app.send_static_file("service-worker.js")


def health():
    return jsonify({"status": "ready"})


def register_routes(app):
    app.add_url_rule(
        "/biovision/jobs/<job_id>",
        endpoint="consultar_job",
        view_func=consultar_job,
        methods=["GET"],
    )
    app.add_url_rule("/biovision/", endpoint="index", view_func=index)
    app.add_url_rule(
        "/biovision/identificar",
        endpoint="identificar",
        view_func=identificar,
    )
    app.add_url_rule(
        "/biovision/air",
        endpoint="biovision_air",
        view_func=biovision_air,
    )
    app.add_url_rule(
        "/biovision/air/",
        endpoint="biovision_air",
        view_func=biovision_air,
    )
    app.add_url_rule(
        "/biovision/contar-animais",
        endpoint="processar_contagem_animais",
        view_func=processar_contagem_animais,
        methods=["POST"],
    )
    app.add_url_rule(
        "/biovision/classificar",
        endpoint="classificar",
        view_func=classificar,
        methods=["POST"],
    )
    app.add_url_rule(
        "/biovision/classificar-video",
        endpoint="classificar_video",
        view_func=classificar_video,
        methods=["POST"],
    )
    app.add_url_rule(
        "/biovision/classificar-audio",
        endpoint="classificar_audio",
        view_func=classificar_audio,
        methods=["POST"],
    )
    app.add_url_rule(
        "/biovision/service-worker.js",
        endpoint="service_worker",
        view_func=service_worker,
    )
    app.add_url_rule("/biovision/health", endpoint="health", view_func=health)
