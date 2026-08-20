import multiprocessing

from backend.biovision_web import create_app
from backend.biovision_web.config import (
    ANALYSIS_WORKERS,
    APP_HOST,
    APP_PORT,
    WAITRESS_THREADS,
)


app = create_app()


def main():
    multiprocessing.freeze_support()
    public_host = "127.0.0.1" if APP_HOST in {"0.0.0.0", "::"} else APP_HOST

    try:
        from waitress import serve
    except ImportError:
        print("Waitress não encontrado; iniciando o servidor Flask local.")
        app.run(
            host=APP_HOST,
            port=APP_PORT,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    else:
        print(
            f"BioVision disponível em http://{public_host}:{APP_PORT}/biovision/ "
            f"({WAITRESS_THREADS} conexões HTTP, "
            f"{ANALYSIS_WORKERS} análises simultâneas)."
        )
        serve(
            app,
            host=APP_HOST,
            port=APP_PORT,
            threads=WAITRESS_THREADS,
            channel_timeout=300,
        )


if __name__ == "__main__":
    main()
