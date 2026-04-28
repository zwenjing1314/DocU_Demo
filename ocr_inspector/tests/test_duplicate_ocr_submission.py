from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from fastapi import HTTPException

import app as app_module


class DummyUploadFile:
    def __init__(
        self,
        *,
        filename: str = "invoice.pdf",
        content: bytes = b"same-pdf-binary",
        content_type: str = "application/pdf",
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self.file = io.BytesIO(content)


def submit_request(upload: DummyUploadFile, **overrides) -> dict:
    return app_module.upload_document(
        file=upload,
        ocr_lang=overrides.get("ocr_lang", "eng"),
        dpi=overrides.get("dpi", 300),
        tesseract_config=overrides.get("tesseract_config", app_module.DEFAULT_TESSERACT_CONFIG),
        preprocess_mode=overrides.get("preprocess_mode", app_module.DEFAULT_PREPROCESS_MODE),
        ocr_padding=overrides.get("ocr_padding", app_module.DEFAULT_OCR_PADDING),
        enable_sparse_fallback=overrides.get("enable_sparse_fallback", True),
        enable_rotated_text=overrides.get("enable_rotated_text", app_module.DEFAULT_ENABLE_ROTATED_TEXT),
        suppress_graphic_artifacts=overrides.get("suppress_graphic_artifacts", True),
    )


def make_fake_ocr_result(source_path: Path, source_kind: str, output_dir: Path) -> dict:
    (output_dir / "ocr.json").write_text("{}", encoding="utf-8")
    (output_dir / "full_text.txt").write_text("hello", encoding="utf-8")

    return {
        "ocr_result": {
            "source_file": source_path.name,
            "source_kind": source_kind,
            "created_at": "2026-04-16T00:00:00+00:00",
            "config": {
                "dpi": 300,
                "lang": "eng",
                "tesseract_config": "--oem 3 --psm 3 -c preserve_interword_spaces=1",
                "tesseract_cmd": "tesseract",
                "preprocess_mode": "clean",
                "ocr_padding": 24,
                "sparse_fallback": True,
                "rotated_text": False,
                "graphic_artifact_filter": True,
            },
            "page_count": 1,
            "pages": [
                {
                    "page_num": 1,
                    "image_path": "page_001.png",
                    "overlay_path": "page_001_overlay.png",
                    "text_path": "page_001.txt",
                    "markdown_path": "page_001.md",
                    "image_width": 100,
                    "image_height": 80,
                    "text": "hello",
                    "words": [
                        {
                            "text": "hello",
                            "confidence": 99.0,
                            "block_num": 1,
                            "line_num": 1,
                            "word_num": 1,
                            "bbox": [0, 0, 10, 10],
                        }
                    ],
                    "lines": [{"text": "hello"}],
                    "rejected_words": [],
                    "diagnostics": {},
                }
            ],
        }
    }


class DuplicateOCRSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.uploads_dir = Path(self.tempdir.name) / "uploads"
        self.outputs_dir = Path(self.tempdir.name) / "outputs"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        app_module._active_ocr_job_id = None
        app_module._completed_response_cache.clear()
        app_module._inflight_request_keys.clear()

    def tearDown(self) -> None:
        app_module._active_ocr_job_id = None
        app_module._completed_response_cache.clear()
        app_module._inflight_request_keys.clear()
        self.tempdir.cleanup()

    def test_same_request_reuses_cached_result_and_does_not_create_extra_jobs(self) -> None:
        call_count = 0

        def fake_run_ocr_pipeline(**kwargs):
            nonlocal call_count
            call_count += 1
            return make_fake_ocr_result(
                kwargs["source_path"],
                kwargs["source_kind"],
                kwargs["output_dir"],
            )

        with (
            patch.object(app_module, "UPLOADS_DIR", self.uploads_dir),
            patch.object(app_module, "OUTPUTS_DIR", self.outputs_dir),
            patch.object(app_module, "run_ocr_pipeline", side_effect=fake_run_ocr_pipeline),
        ):
            first_response = submit_request(DummyUploadFile())
            second_response = submit_request(DummyUploadFile())

        self.assertEqual(call_count, 1)
        self.assertFalse(first_response["cached"])
        self.assertTrue(second_response["cached"])
        self.assertEqual(first_response["job_id"], second_response["job_id"])
        self.assertEqual(len(list(self.uploads_dir.iterdir())), 1)
        self.assertEqual(len(list(self.outputs_dir.iterdir())), 1)

    def test_new_request_with_changed_params_is_blocked_while_another_job_runs(self) -> None:
        started = Event()
        release = Event()
        first_response: dict | None = None
        first_error: Exception | None = None
        call_count = 0

        def fake_run_ocr_pipeline(**kwargs):
            nonlocal call_count
            call_count += 1
            started.set()
            release.wait(timeout=5)
            return make_fake_ocr_result(
                kwargs["source_path"],
                kwargs["source_kind"],
                kwargs["output_dir"],
            )

        def run_first_request() -> None:
            nonlocal first_response, first_error
            try:
                first_response = submit_request(DummyUploadFile())
            except Exception as exc:  # noqa: BLE001
                first_error = exc

        with (
            patch.object(app_module, "UPLOADS_DIR", self.uploads_dir),
            patch.object(app_module, "OUTPUTS_DIR", self.outputs_dir),
            patch.object(app_module, "run_ocr_pipeline", side_effect=fake_run_ocr_pipeline),
        ):
            worker = Thread(target=run_first_request)
            worker.start()
            self.assertTrue(started.wait(timeout=2))

            with self.assertRaises(HTTPException) as ctx:
                submit_request(
                    DummyUploadFile(),
                    dpi=200,
                    enable_sparse_fallback=False,
                )

            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("当前已有 OCR 任务在处理中", str(ctx.exception.detail))
            self.assertEqual(len(list(self.uploads_dir.iterdir())), 1)
            self.assertEqual(len(list(self.outputs_dir.iterdir())), 1)

            release.set()
            worker.join(timeout=5)

        self.assertIsNone(first_error)
        self.assertIsNotNone(first_response)
        self.assertFalse(first_response["cached"])
        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()
