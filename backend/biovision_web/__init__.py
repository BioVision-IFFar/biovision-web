from flask import Flask, jsonify

from .config import MAX_CONTENT_LENGTH, PRELOAD_MODELS, STATIC_DIR, TEMPLATES_DIR


def create_app(preload_models=None):
    app = Flask(
        "biovision_web",
        static_url_path="/static_biovision",
        static_folder=str(STATIC_DIR),
        template_folder=str(TEMPLATES_DIR),
    )
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.json.ensure_ascii = False

    @app.errorhandler(413)
    def arquivo_muito_grande(_error):
        return jsonify({"erro": "O arquivo ultrapassa o limite de 250 MB."}), 413

    from .routes import register_routes

    register_routes(app)

    should_preload = PRELOAD_MODELS if preload_models is None else bool(preload_models)
    if should_preload:
        from .model_service import carregar_modelos_na_inicializacao

        carregar_modelos_na_inicializacao()

    return app
