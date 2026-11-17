"""Extrai fichas do dump confiável do projeto, sem executar comandos SQL."""

import gzip
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "database" / "biovision_especie-dump.sql"
TARGET = ROOT / "backend" / "data" / "species_catalog.json.gz"
TOKEN = re.compile(r"\s*(?:'((?:[^'\\]|\\.|'')*)'|(NULL)|([(),;]))", re.DOTALL)
ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def extract_species(sql):
    schema = re.search(r"CREATE TABLE `especie`\s*\((.*?)\) ENGINE", sql, re.DOTALL)
    if schema is None:
        raise ValueError("Tabela especie não encontrada no dump.")
    columns = re.findall(r"^\s*`([^`]+)`\s", schema[1], re.MULTILINE)
    records = []
    for insert in re.finditer(r"INSERT INTO `especie` VALUES\s*", sql):
        offset = insert.end()
        row = None
        while True:
            match = TOKEN.match(sql, offset)
            if match is None:
                raise ValueError(f"Valor SQL não suportado na posição {offset}.")
            offset = match.end()
            quoted, null, separator = match.groups()
            if separator == ";":
                if row is not None:
                    raise ValueError("Registro incompleto no dump.")
                break
            if separator == "(":
                if row is not None:
                    raise ValueError("Registro inválido no dump.")
                row = []
            elif separator == ")":
                if row is None or len(row) != len(columns):
                    raise ValueError("Quantidade inesperada de colunas no dump.")
                records.append(dict(zip(columns, row)))
                row = None
            elif separator == ",":
                continue
            elif row is None:
                raise ValueError("Valor fora de um registro no dump.")
            elif null:
                row.append(None)
            else:
                value = re.sub(r"\\(.)", lambda item: ESCAPES.get(item[1], item[1]), quoted)
                row.append(value.replace("''", "'"))
    if not records or any(not record.get("especie") for record in records):
        raise ValueError("Catálogo de espécies vazio ou inválido.")
    return records


def main():
    records = extract_species(SOURCE.read_text(encoding="utf-8"))
    names = [record["especie"].replace("_", " ").casefold() for record in records]
    if len(names) != len(set(names)):
        raise ValueError("Há nomes científicos duplicados no catálogo.")
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    TARGET.write_bytes(gzip.compress(payload, mtime=0))
    model_names = json.loads((ROOT / "backend" / "data" / "class_index_corrigido.json").read_text(encoding="utf-8"))
    missing = sorted(name for name in model_names.values() if name.replace("_", " ").casefold() not in set(names))
    print(f"Catálogo: {len(records)} fichas; {TARGET.stat().st_size} bytes comprimidos.")
    print("Classes do modelo sem ficha no dump: " + json.dumps(missing, ensure_ascii=True))


if __name__ == "__main__":
    main()
