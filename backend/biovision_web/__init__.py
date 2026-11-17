from flask import Flask

from .config import (
    AUDIO_MAX_BYTES, AUDIO_MAX_SECONDS, IMAGE_MAX_BYTES, MAX_CONTENT_LENGTH,
    PRELOAD_MODELS, TEMPLATES_DIR, VIDEO_MAX_BYTES,
)


def create_app(preload_models=None):
    app = Flask(
        "biovision_web",
        static_folder=None,
        template_folder=str(TEMPLATES_DIR),
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.config["MAX_FORM_MEMORY_SIZE"] = 256 * 1024
    app.config["MAX_FORM_PARTS"] = 4
    app.json.ensure_ascii = False

    from .security import install_security

    install_security(app)

    @app.context_processor
    def media_limits():
        return {"media_limits": {"imageBytes": IMAGE_MAX_BYTES, "videoBytes": VIDEO_MAX_BYTES,
                                 "audioBytes": AUDIO_MAX_BYTES, "audioSeconds": AUDIO_MAX_SECONDS}}

    from .routes import register_routes

    register_routes(app)

    should_preload = PRELOAD_MODELS if preload_models is None else bool(preload_models)
    if should_preload:
        from .model_service import carregar_modelos_na_inicializacao

        carregar_modelos_na_inicializacao()

    return app
