"""Validação de mídia local antes de carregar modelos ou decodificar quadros."""

import math
import tempfile
import time
import uuid
import warnings
from pathlib import Path

from .config import (
    AUDIO_MAX_SECONDS, IMAGE_MAX_DIMENSION, IMAGE_MAX_PIXELS,
    MEDIA_DECODE_TIMEOUT_SECONDS, VIDEO_MAX_DIMENSION, VIDEO_MAX_FPS,
    VIDEO_MAX_PIXELS,
)


class InvalidMedia(ValueError):
    """Mensagem de validação que pode ser apresentada ao visitante."""


class MediaTooLarge(InvalidMedia):
    pass


# Não permitir playlists, manifests, imagens remotas ou demuxers de scripts.
AV_FORMATS = frozenset({
    "mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm",
    "avi", "wav", "mp3", "ogg", "flac", "aac", "asf",
})
AV_OPTIONS = {
    "protocol_whitelist": "file",
    "format_whitelist": ",".join(sorted(AV_FORMATS)),
    "enable_drefs": "0",
    "use_absolute_path": "0",
    "probesize": "1048576",
    "analyzeduration": "2000000",
}


def local_media_path(path, root=None):
    """Aceitar somente arquivos reais dentro de uma pasta de trabalho privada."""
    candidate = Path(path).resolve(strict=True)
    allowed = Path(root).resolve(strict=True) if root else Path(tempfile.gettempdir()).resolve()
    if not candidate.is_relative_to(allowed) or candidate == allowed or not candidate.is_file():
        raise InvalidMedia("Arquivo de mídia inválido.")
    # Todos os diretórios de upload são criados pelo servidor, nunca pelo cliente.
    if root is None:
        relative = candidate.relative_to(allowed)
        if len(relative.parts) < 2 or not relative.parts[0].startswith("biovision_"):
            raise InvalidMedia("Arquivo de mídia inválido.")
    return candidate


def save_upload(upload, work_dir, max_bytes):
    root = Path(work_dir).resolve(strict=True)
    # O nome e a extensão fornecidos pelo visitante não são usados no disco.
    path = root / f"{uuid.uuid4().hex}.upload"
    size = 0
    try:
        with path.open("xb") as target:
            while chunk := upload.stream.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise MediaTooLarge(f"O arquivo ultrapassa o limite de {max_bytes // (1024 * 1024)} MB.")
                target.write(chunk)
        if size == 0:
            raise InvalidMedia("O arquivo enviado está vazio.")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def check_dimensions(width, height, *, video=False):
    dimension = VIDEO_MAX_DIMENSION if video else IMAGE_MAX_DIMENSION
    pixels = VIDEO_MAX_PIXELS if video else IMAGE_MAX_PIXELS
    if width <= 0 or height <= 0 or max(width, height) > dimension or width * height > pixels:
        raise InvalidMedia(
            f"Resolução acima do permitido: até {dimension} pixels por lado "
            f"e {pixels // 1000000} milhões de pixels."
        )


def validate_image(path):
    from PIL import Image, UnidentifiedImageError

    path = local_media_path(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=("JPEG", "PNG", "WEBP")) as image:
                check_dimensions(*image.size)
                if getattr(image, "n_frames", 1) != 1:
                    raise InvalidMedia("Envie uma imagem estática em JPG, PNG ou WEBP.")
                image_format = image.format
                image.verify()
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image_format]
        renamed = path.with_suffix(suffix)
        path.rename(renamed)
        return renamed
    except InvalidMedia:
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise InvalidMedia("Imagem inválida. Envie JPG, PNG ou WEBP.") from exc


def media_format(path):
    """Identificar o contêiner por assinatura, sem confiar no nome/MIME enviado."""
    with Path(path).open("rb") as source:
        header = source.read(32)
    if len(header) >= 12 and header[4:8] in {b"ftyp", b"moov", b"mdat", b"wide", b"free"}:
        return "mov", ".mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "matroska", ".mkv"
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return "avi", ".avi"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "wav", ".wav"
    if header.startswith(b"OggS"):
        return "ogg", ".ogg"
    if header.startswith(b"fLaC"):
        return "flac", ".flac"
    if header.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")):
        return "asf", ".wma"
    if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 255 and header[1] & 0xe0 == 0xe0):
        if len(header) >= 2 and header[0] == 255 and header[1] & 0xf6 == 0xf0:
            return "aac", ".aac"
        return "mp3", ".mp3"
    raise InvalidMedia("Formato de mídia inválido ou não suportado.")


def open_media(path):
    import av

    path = local_media_path(path)
    demuxer, _suffix = media_format(path)
    return av.open(
        str(path), format=demuxer, options=dict(AV_OPTIONS),
        timeout=(5.0, float(MEDIA_DECODE_TIMEOUT_SECONDS)),
    )


def _duration(container, stream):
    values = []
    if stream.duration is not None and stream.time_base is not None:
        values.append(float(stream.duration * stream.time_base))
    if container.duration is not None:
        values.append(float(container.duration) / 1000000.0)
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise InvalidMedia("Não foi possível validar a duração da mídia.")
    return max(values)


def validate_av(path, *, kind, max_seconds, rename=False):
    path = local_media_path(path)
    demuxer, suffix = media_format(path)
    if kind == "video" and demuxer not in {"mov", "matroska", "avi"}:
        raise InvalidMedia("Envie um vídeo MP4, MOV, AVI, MKV, M4V ou WEBM.")
    try:
        with open_media(path) as container:
            streams = [stream for stream in container.streams if stream.type == kind]
            if len(streams) != 1 or len(container.streams) > 8:
                raise InvalidMedia(f"Envie uma mídia com uma única faixa de {kind == 'video' and 'vídeo' or 'áudio'}.")
            stream = streams[0]
            duration = _duration(container, stream)
            if duration > max_seconds:
                raise InvalidMedia(f"A mídia ultrapassa o limite de {max_seconds} segundos.")
            result = {"duration": duration}
            if kind == "video":
                width, height = stream.codec_context.width, stream.codec_context.height
                check_dimensions(width, height, video=True)
                fps = float(stream.average_rate or stream.guessed_rate or 0)
                if not math.isfinite(fps) or not 0 < fps <= VIDEO_MAX_FPS:
                    raise InvalidMedia(f"O vídeo deve ter até {VIDEO_MAX_FPS} quadros por segundo.")
                result.update(width=width, height=height, fps=fps,
                              total_frames=max(1, math.ceil(duration * fps)))
            else:
                rate = stream.codec_context.sample_rate
                channels = stream.codec_context.channels
                if not 0 < rate <= 192000 or not 0 < channels <= 8:
                    raise InvalidMedia("Áudio com taxa de amostragem ou canais acima do permitido.")
            # Vídeo anexado ao áudio também precisa respeitar a resolução.
            for other in container.streams.video:
                check_dimensions(other.codec_context.width, other.codec_context.height, video=True)
    except InvalidMedia:
        raise
    except Exception as exc:
        raise InvalidMedia("Não foi possível validar a mídia enviada.") from exc
    if rename:
        renamed = path.with_suffix(suffix)
        path.rename(renamed)
        path = renamed
    return path, result


def audio_sample_limit(sample_rate):
    return int(sample_rate) * AUDIO_MAX_SECONDS


def decode_deadline():
    return time.monotonic() + MEDIA_DECODE_TIMEOUT_SECONDS
