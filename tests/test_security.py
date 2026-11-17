"""Testes locais, sem servidor público, banco ou inferência dos modelos."""

import io
import logging
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
from PIL import Image

from backend.biovision_web import create_app
from backend.biovision_web import config, db, media_security as media, model_service as service, security


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(output, format="PNG")
    return output.getvalue()


def audio_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\0\0" * 48000 * seconds)
    return output.getvalue()


def video_bytes():
    output = io.BytesIO()
    with av.open(output, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = stream.height = 32
        stream.pix_fmt = "yuv420p"
        for _index in range(3):
            frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(preload_models=False)
        self.client = self.app.test_client()
        self.client.get("/biovision/")

    def wait_job(self, response, client=None):
        self.assertEqual(response.status_code, 202, response.get_json())
        client = client or self.client
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(response.get_json()["status_url"]).get_json()
            if state["status"] in {"completed", "failed"}:
                return state
            time.sleep(0.01)
        self.fail("O job de teste não terminou.")

    def test_public_pages_and_assets(self):
        for path in ("/biovision/", "/biovision/identificar", "/biovision/air", "/biovision/air/",
                     "/static_biovision/js/app.js", "/static_biovision/css/style.css",
                     "/static_biovision/species/homo_sapiens.jpg", "/biovision/service-worker.js"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                response.close()
        html = self.client.get("/biovision/air").get_data(as_text=True)
        self.assertIn("Envie o vídeo e faça a contagem", html)
        self.assertNotIn('class="air-showcase-features"', html)

    def test_private_files_are_not_served(self):
        for path in ("/.env", "/.git/config", "/app.py", "/models/yolo11x.pt", "/database/test.db",
                     "/static_biovision/../app.py", "/static_biovision/%2e%2e/.env",
                     "/static_biovision/js/.env", "/static_biovision/css/source.py"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(str(config.PROJECT_ROOT), response.get_data(as_text=True))

    def test_all_endpoints_always_use_queue(self):
        cases = (
            ("/biovision/classificar?async=0", "imagem", image_bytes(), "montar_payload_imagem", {}),
            ("/biovision/classificar-video", "video", video_bytes(), "montar_payload_video_especies", {}),
            ("/biovision/contar-animais", "video", video_bytes(), "montar_payload_contagem", {"target_class": "humano"}),
            ("/biovision/classificar-audio", "audio", audio_bytes(), "montar_payload_audio", {}),
        )
        for endpoint, field, content, method, extra in cases:
            with self.subTest(endpoint=endpoint):
                with patch.object(service, method, return_value={"ok": True}) as analysis:
                    response = self.client.post(endpoint, data={field: (io.BytesIO(content), "visitante.txt"), **extra})
                    self.assertEqual(self.wait_job(response)["status"], "completed")
                    analysis.assert_called_once()

    def test_owner_is_required_to_read_result(self):
        with patch.object(service, "montar_payload_imagem", return_value={"keyword": "Homo sapiens"}):
            response = self.client.post("/biovision/classificar", data={"imagem": (io.BytesIO(image_bytes()), "foto.png")})
            self.wait_job(response)
        stranger = self.app.test_client()
        result = stranger.get(response.get_json()["status_url"], headers={"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(result.status_code, 404)

    def test_client_paths_urls_and_extra_files_rejected(self):
        self.assertEqual(self.client.post("/biovision/classificar", json={"imagem": "C:/pessoal.jpg"}).status_code, 400)
        response = self.client.post("/biovision/classificar", data={
            "imagem": (io.BytesIO(image_bytes()), "foto.png"), "url": "https://example.test/arquivo",
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/biovision/classificar", data={
            "imagem": [(io.BytesIO(image_bytes()), "a.png"), (io.BytesIO(image_bytes()), "b.png")],
        })
        self.assertEqual(response.status_code, 400)

    def test_image_limit_is_enforced_and_reservation_released(self):
        with service.analysis_jobs_lock:
            before = len(service.analysis_jobs)
        with patch("backend.biovision_web.routes.IMAGE_MAX_BYTES", 16):
            response = self.client.post("/biovision/classificar", data={"imagem": (io.BytesIO(image_bytes()), "foto.png")})
        self.assertEqual(response.status_code, 413)
        with service.analysis_jobs_lock:
            self.assertEqual(len(service.analysis_jobs), before)

    def test_declared_request_limit_is_enforced(self):
        with patch("backend.biovision_web.routes.IMAGE_MAX_BYTES", 16):
            response = self.client.post("/biovision/classificar", data={"imagem": (io.BytesIO(b"x" * 70000), "foto.png")})
        self.assertEqual(response.status_code, 413)

    def test_cross_site_upload_rejected(self):
        response = self.client.post("/biovision/classificar", headers={"Origin": "https://example.test"},
                                    data={"imagem": (io.BytesIO(image_bytes()), "foto.png")})
        self.assertEqual(response.status_code, 403)

    def test_visitor_rate_does_not_block_progress(self):
        with patch.object(security, "UPLOADS_PER_MINUTE", 1):
            first = self.client.post("/biovision/classificar", data={}, content_type="multipart/form-data")
            second = self.client.post("/biovision/classificar", data={}, content_type="multipart/form-data")
        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 429)
        self.assertIn("Retry-After", second.headers)
        self.assertEqual(self.client.get("/biovision/jobs/inexistente").status_code, 404)

    def test_unexpected_http_errors_do_not_disclose_details(self):
        with patch("backend.biovision_web.routes.render_template", side_effect=RuntimeError("C:/segredo/senha")), \
                self.assertLogs(self.app.logger, level="ERROR") as local_logs:
            response = self.client.get("/biovision/identificar")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("segredo", response.get_data(as_text=True))
        self.assertIn("segredo", str(local_logs.output))


class MediaTests(unittest.TestCase):
    def test_format_dimensions_duration_and_bounded_audio(self):
        with tempfile.TemporaryDirectory(prefix="biovision_test_") as work:
            path = Path(work) / "servidor.upload"
            path.write_bytes(image_bytes())
            image = media.validate_image(path)
            self.assertEqual(image.suffix, ".png")
            path.write_bytes(video_bytes())
            video, info = media.validate_av(path, kind="video", max_seconds=1, rename=True)
            self.assertEqual(video.suffix, ".mp4")
            self.assertEqual((info["width"], info["height"]), (32, 32))
            path.write_bytes(audio_bytes())
            samples, rate, duration = service.carregar_audio_com_pyav(path)
            self.assertEqual(rate, 48000)
            self.assertAlmostEqual(duration, 1)
            self.assertEqual(len(samples), 48000)
            with patch.object(service, "audio_sample_limit", return_value=32):
                with self.assertRaises(media.InvalidMedia):
                    service.carregar_audio_com_pyav(path)
            with self.assertRaises(media.InvalidMedia):
                media.validate_av(path, kind="audio", max_seconds=0.5)

    def test_video_is_readable_by_existing_opencv_with_restrictions(self):
        import cv2
        with tempfile.TemporaryDirectory(prefix="biovision_test_") as work:
            path = Path(work) / "sample.mp4"
            path.write_bytes(video_bytes())
            capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
            try:
                self.assertTrue(capture.isOpened())
                ok, frame = capture.read()
                self.assertTrue(ok)
                self.assertEqual(frame.shape[:2], (32, 32))
            finally:
                capture.release()

    def test_invalid_plain_text_never_reaches_model(self):
        app = create_app(preload_models=False)
        client = app.test_client()
        with patch.object(service, "montar_payload_imagem") as analysis, self.assertLogs(service.logger, level="ERROR"):
            response = client.post("/biovision/classificar", data={"imagem": (io.BytesIO(b"texto comum"), "foto.png")})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = client.get(response.get_json()["status_url"]).get_json()
                if state["status"] == "failed":
                    break
                time.sleep(0.01)
            self.assertEqual(state["status"], "failed")
            analysis.assert_not_called()

    def test_dimensions_and_paths_rejected_before_decode(self):
        with self.assertRaises(media.InvalidMedia):
            media.check_dimensions(8193, 1)
        with self.assertRaises(media.InvalidMedia):
            media.check_dimensions(6000, 6000)
        with self.assertRaises(media.InvalidMedia):
            media.check_dimensions(4096, 4096, video=True)
        with self.assertRaises(media.InvalidMedia):
            media.local_media_path(__file__)
        self.assertEqual(media.AV_OPTIONS["protocol_whitelist"], "file")
        self.assertEqual(media.AV_OPTIONS["enable_drefs"], "0")
        self.assertNotIn("hls", media.AV_FORMATS)

    def test_server_generated_filename_and_rejection_cleanup(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(prefix="biovision_test_") as work:
            upload = SimpleNamespace(filename="C:/pessoal/foto.png", stream=io.BytesIO(image_bytes()))
            path = media.save_upload(upload, work, 1024)
            self.assertEqual(path.parent, Path(work))
            self.assertNotIn("foto", path.name)
            upload.stream = io.BytesIO(b"x" * 33)
            with self.assertRaises(media.MediaTooLarge):
                media.save_upload(upload, work, 32)
            self.assertEqual(len(list(Path(work).iterdir())), 1)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.saved = service.analysis_jobs
        service.analysis_jobs = {}

    def tearDown(self):
        service.analysis_jobs = self.saved

    def test_capacity_and_per_visitor_limit(self):
        reservations = []
        for index in range(config.ANALYSIS_WORKERS + config.MAX_QUEUED_JOBS):
            reservations.append(service.reservar_job("test", f"visitor-{index}"))
        with self.assertRaises(service.JobLimitError) as caught:
            service.reservar_job("test", "extra")
        self.assertEqual(caught.exception.status, 503)
        for job in reservations:
            service.descartar_reserva(job)
        for _index in range(config.MAX_VISITOR_JOBS):
            service.reservar_job("test", "same")
        with self.assertRaises(service.JobLimitError) as caught:
            service.reservar_job("test", "same")
        self.assertEqual(caught.exception.status, 429)

    def test_cleanup_success_error_and_queue_timeout(self):
        cases = ("success", "error", "expired")
        for case in cases:
            with self.subTest(case=case):
                work = Path(tempfile.mkdtemp(prefix="biovision_test_"))
                (work / "sample.txt").write_text("arquivo temporário", encoding="utf-8")
                job = service.reservar_job("test", case)
                if case == "expired":
                    service.analysis_jobs[job]["received_monotonic"] -= config.QUEUE_TIMEOUT_SECONDS + 1
                def task(_progress):
                    if case == "error":
                        raise RuntimeError("C:/segredo/senha")
                    return {"ok": True}
                if case == "success":
                    service.executar_job(job, work, task)
                else:
                    with self.assertLogs(service.logger, level="ERROR"):
                        service.executar_job(job, work, task)
                state = service.payload_job(job, case)
                self.assertEqual(state["status"], "completed" if case == "success" else "failed")
                self.assertNotIn("segredo", str(state))
                self.assertFalse(work.exists())

    def test_worker_count_with_lightweight_tasks(self):
        release = threading.Event()
        work_dirs = []
        jobs = []
        try:
            for index in range(config.ANALYSIS_WORKERS + 1):
                work = Path(tempfile.mkdtemp(prefix="biovision_test_"))
                work_dirs.append(work)
                job = service.reservar_job("test", f"parallel-{index}")
                jobs.append(job)
                def task(_progress):
                    release.wait(timeout=3)
                    return {"ok": True}
                service.enfileirar_job(job, work, task)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with service.analysis_jobs_lock:
                    states = [service.analysis_jobs[job]["status"] for job in jobs]
                if states.count("running") == config.ANALYSIS_WORKERS:
                    break
                time.sleep(0.005)
            self.assertEqual(states.count("running"), config.ANALYSIS_WORKERS)
            self.assertEqual(states.count("queued"), 1)
        finally:
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if all(not work.exists() for work in work_dirs):
                    break
                time.sleep(0.005)
            self.assertTrue(all(not work.exists() for work in work_dirs))

    def test_deadline_and_lock_timeout(self):
        service.analysis_context.deadline = time.monotonic() - 1
        try:
            with self.assertRaises(TimeoutError):
                service.emitir_progresso(None, 1, "teste")
        finally:
            service.analysis_context.__dict__.clear()
        lock = threading.Lock()
        lock.acquire()
        service.analysis_context.deadline = time.monotonic() + 0.02
        try:
            with self.assertRaises(TimeoutError):
                with service.inferencia_limitada(lock):
                    self.fail("Lock não deveria ser adquirido")
        finally:
            lock.release()
            service.analysis_context.__dict__.clear()

    def test_air_never_queries_taxonomy(self):
        counts = {"contagens": {"humano": 3}, "confianca_por_classe": {}, "quadros_confirmados": {}}
        with patch.object(service, "processar_video_contagem", return_value=counts), \
                patch.object(service, "buscar_informacoes_especie") as query:
            result = service.montar_payload_contagem("unused", {"duration": 1}, target_class="humano")
        query.assert_not_called()
        self.assertEqual(result["identificados"][0]["quantidade"], 3)
        self.assertNotIn("dados_taxon", result["identificados"][0])


class IdentityTests(unittest.TestCase):
    def test_signed_visitor_cookie(self):
        tokens = security.VisitorTokens()
        token = tokens.issue()
        self.assertIsNotNone(tokens.identity(token))
        self.assertIsNone(tokens.identity(token + "x"))
        self.assertIsNone(security.VisitorTokens().identity(token))

    def test_global_rate_is_independent_from_cookie_and_ip(self):
        limits = security.RequestLimits()
        with patch.object(security, "GLOBAL_UPLOADS_PER_MINUTE", 2):
            self.assertIsNone(limits.check("a"))
            self.assertIsNone(limits.check("b"))
            self.assertEqual(limits.check("c"), 503)
            self.assertIsNone(limits.check("a", polling=True))
        with patch.object(security, "TRUSTED_PROXY_IPS", ()):
            self.assertFalse(security.trusted_proxy("127.0.0.1"))

    def test_human_record_without_database(self):
        with patch.object(db.mysql.connector, "connect", side_effect=AssertionError("Não consultar MySQL")):
            for name in ("Homo_sapiens", "Homo sapiens", "Homo sapien"):
                record = db.buscar_informacoes_especie(name)
                self.assertEqual(record["familia"], "Hominidae")
                self.assertEqual(record["ordem"], "Primates")
                self.assertIsNotNone(security.public_static_path(record["imagem"].removeprefix("/static_biovision/")))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main()
