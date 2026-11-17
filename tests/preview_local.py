"""Prévia opcional em loopback e porta livre, sem carregar os modelos."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import render_template, request
from werkzeug.serving import make_server

from backend.biovision_web import create_app
from backend.biovision_web.db import HOMO_SAPIENS, buscar_informacoes_especie, primeiro_nome_popular_ou_cientifico

app = create_app(preload_models=False)


def sample_page(template, function, payload):
    html = render_template(template)
    data = json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    script = "<script>document.addEventListener('DOMContentLoaded',()=>" + function + "(" + data + "));</script>"
    return html.replace("</body>", script + "</body>")


@app.get("/preview/human")
def human():
    return sample_page("identificar.html", "exibirResultado", {"keyword": "Homo sapiens", "confidence": 98.7, "dados_taxon": HOMO_SAPIENS})


@app.get("/preview/air")
def air():
    count = max(0, min(99999, request.args.get("count", default=5, type=int)))
    return sample_page("air.html", "renderAirResult", {"alvo_contagem": "humano", "contagens": {"humano": count}})


@app.get("/preview/species")
def species():
    name = request.args.get("name", "Vanellus_chilensis")
    record = buscar_informacoes_especie(name)
    if not record:
        return {"erro": "Espécie não encontrada no acervo."}, 404
    mode = request.args.get("mode", "photo")
    item = {"label": record["especie"], "selector_label": primeiro_nome_popular_ou_cientifico(record, name),
            "species": record["especie"], "dados_taxon": record, "confidence": 98.7,
            "yolo_label": "Ave", "frames_detected": 3}
    if mode == "video":
        return sample_page("identificar.html", "exibirResultadoMidia", {
            "mode": "video_species", "duracao": 1, "species_found": [item],
            "summary_text": "Prévia de uma identificação por vídeo.",
        })
    if mode == "sound":
        return sample_page("identificar.html", "exibirResultadoMidia", {
            "mode": "sound", "ok": True, "keyword": record["especie"],
            "confidence": 98.7, "duration_seconds": 1, "dados_taxon": record,
            "top_species": [item], "summary_text": "Prévia de uma identificação por áudio.",
        })
    return sample_page("identificar.html", "exibirResultado", {
        "keyword": record["especie"], "confidence": 98.7, "dados_taxon": record,
    })


if __name__ == "__main__":
    with make_server("127.0.0.1", 0, app, threaded=True) as server:
        print(f"PREVIEW_URL=http://127.0.0.1:{server.server_port}/biovision/air", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
