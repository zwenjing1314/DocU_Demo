from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_14_direct_pdf_structurer import build_direct_pdf_structure_result


def make_line(
        *,
        text: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        page_num: int = 1,
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
        "block_num": 1,
        "par_num": 1,
        "line_num": line_num,
        "source": "primary",
        "angle": 0.0,
        "words": text.split(),
    }


def create_structured_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    first_page = doc.new_page(width=595, height=842)
    first_page.insert_text((72, 72), "Service Agreement", fontsize=20)
    first_page.insert_text((72, 120), "1. Overview", fontsize=15)
    first_page.insert_text((72, 160), "This agreement summarizes the service scope and payment terms.", fontsize=11)
    second_page = doc.new_page(width=595, height=842)
    second_page.insert_text((72, 72), "2. Payment Terms", fontsize=15)
    second_page.insert_text((72, 120), "Total Contract Value: $120,000.00", fontsize=11)
    doc.set_toc([
        [1, "Service Agreement", 1],
        [2, "Overview", 1],
        [2, "Payment Terms", 2],
    ])
    doc.save(str(pdf_path))
    doc.close()


class DirectPdfStructurerTests(unittest.TestCase):
    def test_builds_strict_schema_from_direct_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "agreement.pdf"
            create_structured_pdf(pdf_path)
            ocr_result = {
                "document_label": "report",
                "contract_schema_result": {
                    "status": "ok",
                    "normalized_contract": {
                        "contract_title": "Service Agreement",
                        "total_amount": "$120,000.00",
                    },
                },
                "layout_chunk_result": {
                    "analysis": {
                        "chunk_count": 3,
                        "table_chunk_count": 0,
                    },
                    "chunks": [
                        {"chunk_id": "chunk_0001_text"},
                        {"chunk_id": "chunk_0002_text"},
                    ],
                },
            }

            result = build_direct_pdf_structure_result(
                source_path=pdf_path,
                source_kind="pdf",
                ocr_result=ocr_result,
            )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["validation"]["schema_valid"])
        self.assertEqual(result["strict_schema"]["summary"]["page_count"], 2)
        self.assertEqual(result["strict_schema"]["outline_tree"][0]["title"], "Service Agreement")
        self.assertEqual(result["strict_schema"]["fixed_json"]["contract"]["total_amount"], "$120,000.00")
        self.assertEqual(result["strict_schema"]["rag_context"]["chunk_count"], 3)
        self.assertIn("json_schema", result["model_contract"])

    def test_run_ocr_pipeline_writes_direct_pdf_structure_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "agreement.pdf"
            output_dir = Path(tempdir) / "outputs"
            create_structured_pdf(pdf_path)

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

                if page_num == 1:
                    lines = [
                        make_line(text="Service Agreement", left=80, top=60, right=420, bottom=105, page_num=1, line_num=1),
                        make_line(text="1. Overview", left=80, top=140, right=300, bottom=180, page_num=1, line_num=2),
                        make_line(text="This agreement summarizes the service scope.", left=80, top=220, right=620, bottom=260, page_num=1, line_num=3),
                    ]
                else:
                    lines = [
                        make_line(text="2. Payment Terms", left=80, top=80, right=360, bottom=120, page_num=2, line_num=1),
                        make_line(text="Total Contract Value: $120,000.00", left=80, top=170, right=520, bottom=210, page_num=2, line_num=2),
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

            with (
                patch.object(ocr_engine, "_ocr_image", side_effect=fake_ocr_image),
                patch.object(ocr_engine, "_extract_tables_from_pdf_page", return_value=[]),
            ):
                result = ocr_engine.run_ocr_pipeline(
                    source_path=pdf_path,
                    output_dir=output_dir,
                    source_kind="pdf",
                    lang="eng",
                )

            structure_json_path = output_dir / "direct_pdf_structure.json"
            self.assertTrue(structure_json_path.exists())
            payload = json.loads(structure_json_path.read_text(encoding="utf-8"))

            self.assertTrue(payload["validation"]["schema_valid"])
            self.assertEqual(payload["analysis"]["native_text_page_count"], 2)
            self.assertIn("direct_pdf_structure_result", result["ocr_result"])
            self.assertEqual(result["ocr_result"]["direct_pdf_structure_result"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
