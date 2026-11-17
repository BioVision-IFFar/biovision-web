"""Regressões do catálogo, da conexão MySQL e dos caminhos públicos no R2."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from backend.biovision_web import config, db
from scripts.export_species_catalog import extract_species


class ImageURLTests(unittest.TestCase):
    def test_default_and_old_environment_use_current_bucket(self):
        for value in ("", "https://biovision-images.pedrohenriquetoazza.workers.dev/"):
            with self.subTest(value=value), patch.dict(os.environ, {"BIOVISION_IMAGE_BASE_URL": value}):
                self.assertEqual(config.configured_image_base_url(), config.DEFAULT_IMAGE_BASE_URL)
        with patch.dict(os.environ, {"BIOVISION_IMAGE_BASE_URL": "https://example.test/images/"}):
            self.assertEqual(config.configured_image_base_url(), "https://example.test/images")

    def test_preserve_mixed_extensions_and_folder_case(self):
        for suffix in ("jfif", "jpg", "png"):
            path = f"especies886/Especie/Vanellus_chilensis.{suffix}"
            expected = f"{config.IMAGE_BASE_URL}/{path}"
            for stored in (path, "/" + path, path.replace("/", "\\"), ("/" + path).encode()):
                with self.subTest(path=stored):
                    self.assertEqual(db.montar_url_imagem(stored), expected)

    def test_absolute_legacy_urls_are_migrated(self):
        path = "especies886/Especie/Vanellus_chilensis.jfif"
        for scheme in ("http", "https"):
            old = f"{scheme}://biovision-images.pedrohenriquetoazza.workers.dev/{path}"
            self.assertEqual(db.montar_url_imagem(old), f"{config.IMAGE_BASE_URL}/{path}")

    def test_encoding_static_paths_and_other_hosts(self):
        self.assertEqual(db.montar_url_imagem("/especies886/Classe/Anfíbios foto.png"),
                         f"{config.IMAGE_BASE_URL}/especies886/Classe/Anf%C3%ADbios%20foto.png")
        self.assertEqual(db.montar_url_imagem("/especies886/Classe/Anf%C3%ADbios%20foto.png"),
                         f"{config.IMAGE_BASE_URL}/especies886/Classe/Anf%C3%ADbios%20foto.png")
        self.assertEqual(db.montar_url_imagem("/static_biovision/species/homo_sapiens.jpg"),
                         "/static_biovision/species/homo_sapiens.jpg")
        self.assertEqual(db.montar_url_imagem("https://example.test/photo.jpg"), "https://example.test/photo.jpg")

    def test_invalid_and_private_paths_are_rejected(self):
        for value in (None, "", "NULL", "None", "N/A", "../.env", "/%2e%2e/private.jpg",
                      "C:/private/photo.jpg", "file:///private/photo.jpg", "//example.test/image.jpg",
                      "javascript:example", "https://user:password@example.test/image.jpg"):
            with self.subTest(value=value):
                self.assertIsNone(db.montar_url_imagem(value))


class SpeciesCatalogTests(unittest.TestCase):
    def setUp(self):
        with db.taxon_lock:
            db.taxon_cache.clear()

    def tearDown(self):
        with db.taxon_lock:
            db.taxon_cache.clear()

    def test_catalog_covers_every_model_class(self):
        names = json.loads(config.CLASS_INDEX_PATH.read_text(encoding="utf-8")).values()
        catalog = db.carregar_catalogo_local()
        self.assertEqual(len(catalog), len(set(names)))
        for name in names:
            with self.subTest(name=name):
                record = db.informacoes_catalogo_local(name)
                self.assertIsNotNone(record)
                self.assertTrue(record.get("especie"))
                self.assertTrue(record.get("reino"))

    def test_missing_configuration_uses_local_data_without_connecting(self):
        with patch.object(db, "DB_CONFIG", {"host": "", "user": ""}), \
                patch.object(db.mysql.connector, "connect") as connect:
            record = db.buscar_informacoes_especie("Vanellus_chilensis")
        connect.assert_not_called()
        self.assertEqual(record["especie"], "Vanellus chilensis")
        self.assertTrue(record["habitat"])
        self.assertTrue(record["morfologia"])
        self.assertEqual(record["imagem"], f"{config.IMAGE_BASE_URL}/especies886/Especie/Vanellus_chilensis.jfif")
        self.assertEqual(record["im_morfologia"], f"{config.IMAGE_BASE_URL}/especies886/Especie/Vanellus_chilensis_morf.jpg")

    def test_database_error_falls_back_without_exposing_error(self):
        with patch.object(db, "DB_CONFIG", {"host": "localhost", "user": "fixture"}), \
                patch.object(db.mysql.connector, "connect", side_effect=RuntimeError("internal connection detail")), \
                self.assertLogs(db.logger, level="ERROR"):
            record = db.buscar_informacoes_especie("Vanellus_chilensis")
        self.assertTrue(record["habitat"])
        self.assertNotIn("internal connection detail", str(record))

    def test_mysql_values_win_and_empty_fields_are_completed(self):
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value = cursor
        connection.is_connected.return_value = True
        cursor.fetchone.side_effect = [None, {
            "especie": "Vanellus chilensis", "nomes": "Quero-quero atualizado",
            "habitat": "Habitat revisado no MySQL", "imagem": None,
        }]
        with patch.object(db, "DB_CONFIG", {"host": "localhost", "user": "fixture"}), \
                patch.object(db.mysql.connector, "connect", return_value=connection):
            record = db.buscar_informacoes_especie("Vanellus_chilensis")
        self.assertEqual(record["nomes"], "Quero-quero atualizado")
        self.assertEqual(record["habitat"], "Habitat revisado no MySQL")
        self.assertTrue(record["imagem"].endswith("/Vanellus_chilensis.jfif"))
        self.assertTrue(record["morfologia"])
        cursor.close.assert_called_once()
        connection.close.assert_called_once()
        self.assertEqual(cursor.execute.call_args.args[1], ("Vanellus chilensis",))

    def test_local_records_are_independent_and_unknown_name_is_not_guessed(self):
        record = db.informacoes_catalogo_local("Vanellus_chilensis")
        original = record["habitat"]
        record["habitat"] = "alterado no teste"
        self.assertEqual(db.informacoes_catalogo_local("Vanellus chilensis")["habitat"], original)
        self.assertIsNone(db.informacoes_catalogo_local("nome inexistente"))

    def test_mysql_dump_is_read_as_data_and_preserves_escapes(self):
        sql = r"""
CREATE TABLE `especie` (
  `especie` varchar(100),
  `imagem` varchar(500),
  `habitat` text
) ENGINE=InnoDB;
INSERT INTO `especie` VALUES ('Espécie teste','/especies886/Especie/teste.jfif','Linha 1\nD\'água'),('Outra espécie',NULL,'Texto, com vírgula (e parênteses)');
"""
        records = extract_species(sql)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["habitat"], "Linha 1\nD'água")
        self.assertIsNone(records[1]["imagem"])
        self.assertEqual(records[1]["habitat"], "Texto, com vírgula (e parênteses)")


if __name__ == "__main__":
    unittest.main()
