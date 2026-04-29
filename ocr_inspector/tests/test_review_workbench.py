from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from ocr_engine_12_review_workbench import (
    build_review_workbench_state,
    save_review_workbench_revisions,
)


def make_bbox(left: int = 80, top: int = 100, right: int = 260, bottom: int = 130) -> dict:
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def make_ocr_payload() -> dict:
    return {
        "source_file": "review_sample.pdf",
        "source_kind": "pdf",
        "page_count": 1,
        "pages": [
            {
                "page_num": 1,
                "image_path": "page_001.png",
                "overlay_path": "page_001_overlay.png",
                "text_path": "page_001.txt",
                "image_width": 1000,
                "image_height": 1400,
                "text": "Name: A1ice Chen\nSigning Date: 2026/04/28",
                "lines": [],
                "words": [
                    {
                        "text": "A1ice",
                        "confidence": 42.0,
                        "bbox": make_bbox(),
                        "block_num": 1,
                        "par_num": 1,
                        "line_num": 1,
                        "word_num": 2,
                    }
                ],
            }
        ],
        "form_result": {
            "fields": {
                "name": {
                    "value": "A1ice Chen",
                    "raw_value": "A1ice Chen",
                    "page_num": 1,
                    "source": "inline",
                    "label": "Name: A1ice Chen",
                    "bbox": make_bbox(),
                }
            },
            "normalized_form": {
                "name": "A1ice Chen",
                "selected_options": ["yes"],
            },
        },
        "contract_schema_result": {
            "status": "ok",
            "fields": {
                "signing_date": {
                    "value": "2026-04-28",
                    "page_num": 1,
                    "bbox": make_bbox(80, 160, 340, 195),
                    "confidence": 0.9,
                    "snippet": "Signing Date: 2026/04/28",
                }
            },
            "normalized_contract": {
                "signing_date": "2026-04-28",
            },
        },
        "receipt_invoice_result": {
            "normalized_receipt": {
                "vendor": "Acme Store Ltd",
                "date": "2026-04-28",
                "tax": 2.5,
                "total": 27.5,
            }
        },
        "multi_page_consolidation_result": {
            "consolidated": {
                "receipt_invoice": {
                    "item_sum": 25.0,
                    "tax": 2.5,
                    "reported_total": 27.5,
                    "calculated_total": 27.5,
                },
                "bank_statement": {},
            },
            "analysis": {},
        },
        "signature_handwriting_review_result": {
            "pages": [
                {
                    "page_num": 1,
                    "review_overlay_path": "review_overlays/page_001_review.png",
                    "suspicious_fields": [
                        {
                            "page_num": 1,
                            "field_name": "name",
                            "value": "A1ice Chen",
                            "avg_confidence": 42.0,
                            "bbox": make_bbox(),
                            "review_reason": "field value pattern looks suspicious",
                        }
                    ],
                    "handwriting_regions": [],
                    "signature_regions": [],
                    "low_confidence_regions": [
                        {
                            "page_num": 1,
                            "text": "A1ice Chen",
                            "avg_confidence": 42.0,
                            "bbox": make_bbox(),
                        }
                    ],
                }
            ],
            "analysis": {},
        },
    }


def write_job_output(output_dir: Path, payload: dict | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ocr.json").write_text(
        json.dumps(payload or make_ocr_payload(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class ReviewWorkbenchTests(unittest.TestCase):
    def test_builds_review_state_from_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir)
            write_job_output(output_dir)

            state = build_review_workbench_state(
                job_id="abcdef123456",
                output_dir=output_dir,
                output_base_url="/outputs/abcdef123456",
            )

            self.assertEqual(state["job_id"], "abcdef123456")
            self.assertEqual(state["pages"][0]["image_url"], "/outputs/abcdef123456/pages/page_001.png")
            self.assertGreaterEqual(state["analysis"]["predicted_field_count"], 5)
            self.assertGreaterEqual(state["analysis"]["review_queue_count"], 2)
            self.assertEqual(state["revisions"]["analysis"]["revision_count"], 0)

    def test_saves_revision_batches_and_latest_values(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir)
            write_job_output(output_dir)

            saved = save_review_workbench_revisions(
                job_id="abcdef123456",
                output_dir=output_dir,
                payload={
                    "reviewer": "tester",
                    "note": "fix OCR typo",
                    "revisions": [
                        {
                            "field_id": "form:form.fields.name",
                            "field_path": "form.fields.name",
                            "source": "form",
                            "page_num": 1,
                            "old_value": "A1ice Chen",
                            "new_value": "Alice Chen",
                        }
                    ],
                },
            )

            self.assertEqual(saved["analysis"]["revision_batch_count"], 1)
            self.assertEqual(saved["analysis"]["revision_count"], 1)
            self.assertEqual(saved["latest_revisions"]["form:form.fields.name"]["new_value"], "Alice Chen")
            self.assertTrue((output_dir / "review_workbench_revisions.json").exists())

    def test_review_workbench_api_state_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            outputs_root = Path(tempdir)
            job_id = "abcdef123456"
            output_dir = outputs_root / job_id
            write_job_output(output_dir)

            client = TestClient(app_module.app)
            with patch.object(app_module, "OUTPUTS_DIR", outputs_root):
                state_response = client.get(f"/api/review/{job_id}/state")
                self.assertEqual(state_response.status_code, 200)
                self.assertEqual(state_response.json()["job_id"], job_id)

                save_response = client.post(
                    f"/api/review/{job_id}/save",
                    json={
                        "reviewer": "tester",
                        "revisions": [
                            {
                                "field_id": "form:form.fields.name",
                                "field_path": "form.fields.name",
                                "source": "form",
                                "old_value": "A1ice Chen",
                                "new_value": "Alice Chen",
                            }
                        ],
                    },
                )

            self.assertEqual(save_response.status_code, 200)
            self.assertEqual(save_response.json()["revision_count"], 1)
            self.assertTrue((output_dir / "review_workbench_revisions.json").exists())


if __name__ == "__main__":
    unittest.main()
