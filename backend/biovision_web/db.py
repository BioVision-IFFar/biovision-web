import copy
import gzip
import json
import logging
import threading
from functools import lru_cache
from urllib.parse import quote, unquote, urlsplit

import mysql.connector

from .config import DB_CONFIG, IMAGE_BASE_URL, LEGACY_IMAGE_HOSTS, SPECIES_CATALOG_PATH


IMAGE_FIELDS = (
    "imagem",
    "im_habitat",
    "im_morfologia",
    "im_conservacao",
    "im_reino",
    "im_filo",
    "im_classe",
    "im_ordem",
)

AIR_CLASS_DB_ALIASES = {
    "boi": ("Bos_taurus", "Bos taurus", "boi", "bovino", "gado"),
    "humano": ("Homo_sapiens", "Homo sapiens", "humano"),
    "cachorro": (
        "Canis_lupus_familiaris",
        "Canis lupus familiaris",
        "Canis_familiaris",
        "cachorro",
        "cao",
    ),
    "gato": ("Felis_catus", "Felis catus", "gato"),
    "ave": (),
}

taxon_cache = {}
taxon_lock = threading.Lock()
logger = logging.getLogger(__name__)

# Complemento local: funciona em foto/vídeo mesmo sem registro no MySQL.
# Taxonomia: ITIS TSN 180092. Foto: NASA, retrato de Christina Koch (2023).
HOMO_SAPIENS = {
    "especie": "Homo sapiens", "nomes": "Ser humano, humano",
    "imagem": "/static_biovision/species/homo_sapiens.jpg",
    "title_imagem": "Ser humano (Homo sapiens): Christina Koch. Foto: NASA (2023), uso educativo.",
    "reino": "Animalia", "filo": "Chordata", "classe": "Mammalia",
    "ordem": "Primates", "familia": "Hominidae", "genero": "Homo",
    "habitat": "Espécie de distribuição mundial, presente em ambientes terrestres muito diversos, incluindo cidades, campos e florestas.",
    "morfologia": "Primata de postura ereta e locomoção bípede, com mãos capazes de movimentos precisos, polegares oponíveis e grande desenvolvimento cerebral.",
    "reproducao": "Reprodução sexuada, fecundação interna e desenvolvimento vivíparo. A gestação dura aproximadamente nove meses, seguida de cuidado parental prolongado.",
    "conservacao": "Espécie amplamente distribuída. Suas atividades têm grande influência na conservação dos ambientes e de outras espécies.",
    "carac_reino": "Animal multicelular e heterótrofo, que obtém energia consumindo outros organismos ou seus produtos.",
    "carac_filo": "Vertebrado do filo Chordata, com esqueleto interno e coluna vertebral.",
    "carac_classe": "Mamífero com pelos, glândulas mamárias e regulação da temperatura corporal.",
    "carac_ordem": "Primata com visão binocular e mãos adaptadas à manipulação de objetos.",
}


def nome_classe_legivel(nome):
    return str(nome or "").replace("_", " ").strip()


def nomes_consulta_banco(nome_especie):
    candidatos = [
        str(nome_especie or "").strip(),
        nome_classe_legivel(nome_especie),
        str(nome_especie or "").replace(" ", "_").strip(),
    ]
    unicos = []
    for item in candidatos:
        if item and item not in unicos:
            unicos.append(item)
    return unicos


def corrigir_texto_mojibake(valor):
    if not isinstance(valor, str):
        return valor
    if "Ãƒ" not in valor and "Ã‚" not in valor:
        return valor
    try:
        corrigido = valor.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return valor
    return corrigido if corrigido else valor


def montar_url_imagem(caminho):
    if caminho is None:
        return None
    if isinstance(caminho, (bytes, bytearray)):
        try:
            caminho = caminho.decode("utf-8")
        except UnicodeDecodeError:
            return None

    caminho = str(caminho).strip().replace("\\", "/")
    if not caminho or caminho.casefold() in {"null", "none", "n/a"}:
        return None
    try:
        parsed = urlsplit(caminho)
        if parsed.scheme:
            if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
                return None
            if parsed.hostname not in LEGACY_IMAGE_HOSTS:
                return caminho
            caminho = parsed.path
        elif parsed.netloc:
            return None
    except ValueError:
        return None
    caminho = unquote(caminho).lstrip("/")
    if not caminho or any(part in {".", ".."} for part in caminho.split("/")):
        return None
    caminho = quote(caminho, safe="/")
    if caminho.startswith("static_biovision/"):
        return f"/{caminho}"
    if not IMAGE_BASE_URL:
        return None
    return f"{IMAGE_BASE_URL}/{caminho}"


def limpar_resultado_banco(resultado):
    if not isinstance(resultado, dict):
        return resultado

    limpo = {}
    for chave, valor in resultado.items():
        if chave in IMAGE_FIELDS:
            limpo[chave] = montar_url_imagem(valor)
        elif isinstance(valor, (bytes, bytearray)):
            try:
                limpo[chave] = valor.decode("utf-8")
            except UnicodeDecodeError:
                limpo[chave] = None
        elif isinstance(valor, str):
            limpo[chave] = corrigir_texto_mojibake(valor)
        else:
            limpo[chave] = valor
    return limpo


@lru_cache(maxsize=1)
def carregar_catalogo_local():
    try:
        with gzip.open(SPECIES_CATALOG_PATH, "rt", encoding="utf-8") as source:
            records = json.load(source)
        return {
            nome_classe_legivel(record["especie"]).casefold(): record
            for record in records
        }
    except (OSError, EOFError, ValueError, TypeError, KeyError):
        logger.exception("Não foi possível carregar o catálogo local de espécies")
        return {}


def informacoes_catalogo_local(nome_especie):
    key = nome_classe_legivel(nome_especie).casefold()
    record = carregar_catalogo_local().get(key)
    return limpar_resultado_banco(copy.deepcopy(record)) if record else None


def buscar_informacoes_especie(nome_especie):
    normalized = nome_classe_legivel(nome_especie).casefold()
    if normalized in {"homo sapiens", "homo sapien", "humano", "pessoa"}:
        return copy.deepcopy(HOMO_SAPIENS)
    cache_key = str(nome_especie or "").strip().lower()
    if cache_key:
        with taxon_lock:
            if cache_key in taxon_cache:
                return copy.deepcopy(taxon_cache[cache_key])

    if not DB_CONFIG.get("host") or not DB_CONFIG.get("user"):
        return informacoes_catalogo_local(nome_especie)

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG, connection_timeout=5)
        cursor = conn.cursor(dictionary=True)

        resultado = None
        query = "SELECT * FROM especie WHERE especie = %s"
        for candidato in nomes_consulta_banco(nome_especie):
            cursor.execute(query, (candidato,))
            resultado = cursor.fetchone()
            if resultado:
                break

        if resultado is None:
            return informacoes_catalogo_local(nome_especie)
        # Completar campos vazios com a ficha existente, sem sobrescrever os
        # dados preenchidos/atualizados no MySQL.
        fallback = informacoes_catalogo_local(nome_especie) or {}
        resultado = {**fallback, **{
            key: value for key, value in resultado.items()
            if value is not None and (not isinstance(value, str) or value.strip())
        }}
        resultado = limpar_resultado_banco(resultado)
        if cache_key and resultado is not None:
            with taxon_lock:
                taxon_cache[cache_key] = copy.deepcopy(resultado)
        return copy.deepcopy(resultado)
    except Exception:
        logger.exception("Falha ao consultar MySQL; usando a ficha do catálogo local")
        return informacoes_catalogo_local(nome_especie)
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None and conn.is_connected():
            try:
                conn.close()
            except Exception:
                pass


def buscar_informacoes_classes_air(class_names_detected):
    requested = [
        class_name
        for class_name in dict.fromkeys(class_names_detected)
        if AIR_CLASS_DB_ALIASES.get(class_name)
    ]
    if not requested:
        return {}, True

    details = {}
    pending = []
    with taxon_lock:
        for class_name in requested:
            cached = taxon_cache.get(f"air:{class_name}")
            if cached is not None:
                details[class_name] = copy.deepcopy(cached)
            else:
                pending.append(class_name)

    if not pending:
        return details, True

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG, connection_timeout=5)
        cursor = conn.cursor(dictionary=True)
        query = """
            SELECT *
            FROM especie
            WHERE LOWER(REPLACE(especie, '_', ' ')) = LOWER(REPLACE(%s, '_', ' '))
               OR LOWER(nomes) LIKE LOWER(%s)
            LIMIT 1
        """

        for class_name in pending:
            found = None
            for alias in AIR_CLASS_DB_ALIASES[class_name]:
                popular_term = alias.replace("_", " ")
                cursor.execute(query, (alias, f"%{popular_term}%"))
                found = cursor.fetchone()
                if found:
                    break

            found = limpar_resultado_banco(found)
            if found is not None:
                details[class_name] = found
                with taxon_lock:
                    taxon_cache[f"air:{class_name}"] = copy.deepcopy(found)

        return details, True
    except Exception as exc:
        print(f"Erro no MySQL durante a consulta do BioVision Air: {exc}")
        return details, False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None and conn.is_connected():
            try:
                conn.close()
            except Exception:
                pass


def nome_popular_ou_cientifico(dados_taxon, fallback):
    if isinstance(dados_taxon, dict):
        for chave in ("nomes", "nome_popular", "nome_comum"):
            valor = dados_taxon.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
    return nome_classe_legivel(fallback)


def primeiro_nome_popular_ou_cientifico(dados_taxon, fallback):
    nome = nome_popular_ou_cientifico(dados_taxon, fallback)
    for separador in (",", ";", "/", "|"):
        if separador in nome:
            nome = nome.split(separador, 1)[0]
            break
    return nome.strip() or nome_classe_legivel(fallback)
