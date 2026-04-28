from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import app as app_module
import ocr_engine
from ocr_engine_5_receipt import build_receipt_invoice_result
from ocr_engine_9_query import answer_document_query, build_query_extractor_result


def make_line(
        *,
        text: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        page_num: int = 1,
        block_num: int = 1,
        par_num: int = 1,
        line_num: int = 1,
) -> dict:
    return {
        "page_num": page_num,
        "text": text,
        "confidence": 95.0,
        "bbox": {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
            "right": right,
            "bottom": bottom,
        },
        "block_num": block_num,
        "par_num": par_num,
        "line_num": line_num,
        "source": "primary",
        "angle": 0.0,
        "words": text.split(),
    }


def create_single_page_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()


class QueryExtractorTests(unittest.TestCase):
    def test_answers_total_amount_query_with_page_and_bbox(self) -> None:
        ocr_result = {
            "source_file": "invoice.pdf",
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Acme Store Ltd", left=80, top=60, right=320, bottom=95, line_num=1),
                        make_line(text="Invoice", left=80, top=100, right=220, bottom=135, line_num=2),
                        make_line(text="Total: $27.50", left=80, top=665, right=300, bottom=700, line_num=3),
                    ],
                }
            ],
            "tables": [],
        }
        ocr_result["receipt_invoice_result"] = build_receipt_invoice_result(ocr_result)

        query_result = build_query_extractor_result(ocr_result)
        answer = answer_document_query(query_result, "总金额是多少")

        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["answer"], "$27.50")
        self.assertEqual(answer["page_num"], 1)
        self.assertEqual(answer["matched_field"], "total_amount")
        self.assertEqual(answer["bbox"]["top"], 665)
        self.assertIn("Total", answer["snippet"])

    def test_answers_contract_start_date_query_from_line_pattern(self) -> None:
        ocr_result = {
            "source_file": "contract.pdf",
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Service Agreement", left=80, top=60, right=320, bottom=95, line_num=1),
                        make_line(text="Start Date: 2026/05/01", left=80, top=180, right=420, bottom=215, line_num=2),
                        make_line(text="End Date: 2027/04/30", left=80, top=225, right=420, bottom=260, line_num=3),
                    ],
                }
            ],
            "tables": [],
        }

        query_result = build_query_extractor_result(ocr_result)
        answer = answer_document_query(query_result, "合同起始日是哪天")

        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["answer"], "2026-05-01")
        self.assertEqual(answer["page_num"], 1)
        self.assertEqual(answer["matched_field"], "contract_start_date")
        self.assertIn("Start Date", answer["snippet"])

    def test_run_ocr_pipeline_writes_query_json_and_query_endpoint_works(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            pdf_path = temp_path / "invoice.pdf"
            output_dir = temp_path / "outputs"
            create_single_page_pdf(pdf_path)

            def fake_ocr_image(
                    image_path: Path,
                    page_num: int,
                    lang: str,
                    tesseract_config: str,
                    preprocess_mode: str,
                    ocr_padding: int,
                    enable_sparse_fallback: bool,
                    enable_rotated_text: bool,
                    suppress_graphic_artifacts: bool,
            ) -> dict:
                with Image.open(image_path) as image:
                    rendered_page = image.convert("RGB")

                lines = [
                    make_line(text="Acme Store Ltd", left=80, top=60, right=320, bottom=95, line_num=1),
                    make_line(text="Invoice", left=80, top=100, right=220, bottom=135, line_num=2),
                    make_line(text="Invoice No: INV-001", left=80, top=145, right=340, bottom=180, line_num=3),
                    make_line(text="Date: 2026/04/28", left=80, top=190, right=320, bottom=225, line_num=4),
                    make_line(text="Total: $27.50", left=80, top=665, right=300, bottom=700, line_num=5),
                ]
                return {
                    "page_num": page_num,
                    "image_width": rendered_page.width,
                    "image_height": rendered_page.height,
                    "words": [],
                    "rejected_words": [],
                    "lines": lines,
                    "text": "\n".join(line["text"] for line in lines),
                    "diagnostics": {},
                    "_image": rendered_page,
                }

            with patch.object(ocr_engine, "_ocr_image", side_effect=fake_ocr_image):
                result = ocr_engine.run_ocr_pipeline(
                    source_path=pdf_path,
                    output_dir=output_dir,
                    source_kind="pdf",
                    lang="eng",
                )

            query_json_path = output_dir / "query_extractor.json"
            self.assertTrue(query_json_path.exists())
            payload = json.loads(query_json_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(payload["analysis"]["candidate_count"], 1)
            self.assertIn("query_extractor_result", result["ocr_result"])

            outputs_root = temp_path / "outputs_root"
            job_id = "job_query_001"
            task_output_dir = outputs_root / job_id
            task_output_dir.mkdir(parents=True, exist_ok=True)
            query_json_path.replace(task_output_dir / "query_extractor.json")

            with patch.object(app_module, "OUTPUTS_DIR", outputs_root):
                response = app_module.query_document(job_id=job_id, query="总金额是多少")

            self.assertEqual(response["result"]["status"], "ok")
            self.assertEqual(response["result"]["answer"], "$27.50")
            self.assertEqual(response["result"]["page_num"], 1)
            self.assertEqual(response["query_history_count"], 1)


if __name__ == "__main__":
    unittest.main()
