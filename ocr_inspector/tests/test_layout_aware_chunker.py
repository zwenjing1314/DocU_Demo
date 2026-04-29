from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_13_chunker import build_layout_aware_chunk_result


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


def create_single_page_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()


class LayoutAwareChunkerTests(unittest.TestCase):
    def test_builds_heading_context_and_atomic_table_chunk(self) -> None:
        ocr_result = {
            "source_file": "long_report.pdf",
            "source_kind": "pdf",
            "page_count": 2,
            "pages": [
                {
                    "page_num": 1,
                    "layout": {
                        "items": [
                            {"type": "heading", "level": 1, "text": "Annual Report", "page_num": 1},
                            {"type": "heading", "level": 2, "text": "Revenue Overview", "page_num": 1},
                            {"type": "paragraph", "text": "Revenue increased because enterprise demand improved.", "page_num": 1},
                        ]
                    },
                },
                {
                    "page_num": 2,
                    "layout": {
                        "items": [
                            {"type": "paragraph", "text": "The same section continues on the second page.", "page_num": 2},
                        ]
                    },
                },
            ],
            "tables": [
                {
                    "table_id": "page_002_table_01",
                    "page_num": 2,
                    "row_count": 3,
                    "col_count": 3,
                    "csv_path": "page_002_table_01.csv",
                    "html_path": "page_002_table_01.html",
                    "rows": [
                        ["Region", "Revenue", "Growth"],
                        ["North", "$120", "12%"],
                        ["South", "$95", "8%"],
                    ],
                }
            ],
            "multi_page_consolidation_result": {
                "status": "ok",
                "document_kind": "report",
                "consolidated": {},
                "analysis": {
                    "consolidated_item_count": 0,
                    "duplicate_item_count": 0,
                    "transaction_count": 0,
                    "total_check_status": "not_available",
                    "balance_check_status": "not_available",
                },
            },
        }

        result = build_layout_aware_chunk_result(ocr_result, max_chars=220)
        text_chunks = [chunk for chunk in result["chunks"] if chunk["type"] == "text"]
        table_chunks = [chunk for chunk in result["chunks"] if chunk["type"] == "table"]

        self.assertGreaterEqual(len(text_chunks), 1)
        self.assertEqual(len(table_chunks), 1)
        self.assertEqual(text_chunks[0]["title_context"], "Annual Report > Revenue Overview")
        self.assertIn("Annual Report > Revenue Overview", text_chunks[0]["text"])
        self.assertEqual(table_chunks[0]["table_header_context"], ["Region", "Revenue", "Growth"])
        self.assertIn("| Region | Revenue | Growth |", table_chunks[0]["text"])
        self.assertEqual(table_chunks[0]["page_range"], {"start_page": 2, "end_page": 2})
        self.assertEqual(result["analysis"]["table_chunk_count"], 1)

    def test_run_ocr_pipeline_writes_layout_chunks_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "report.pdf"
            output_dir = Path(tempdir) / "outputs"
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
                    make_line(text="Annual Report", left=100, top=80, right=420, bottom=135, line_num=1),
                    make_line(text="1. Revenue Overview", left=100, top=180, right=460, bottom=225, line_num=2),
                    make_line(text="Revenue increased during the quarter.", left=100, top=260, right=620, bottom=300, line_num=3),
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

            fake_tables = [
                {
                    "page_num": 1,
                    "source": "pdf_text",
                    "row_count": 2,
                    "col_count": 3,
                    "bbox": {"left": 80, "top": 340, "width": 400, "height": 120, "right": 480, "bottom": 460},
                    "rows": [
                        ["Region", "Revenue", "Growth"],
                        ["North", "$120", "12%"],
                    ],
                }
            ]

            with (
                patch.object(ocr_engine, "_ocr_image", side_effect=fake_ocr_image),
                patch.object(ocr_engine, "_extract_tables_from_pdf_page", return_value=fake_tables),
            ):
                result = ocr_engine.run_ocr_pipeline(
                    source_path=pdf_path,
                    output_dir=output_dir,
                    source_kind="pdf",
                    lang="eng",
                )

            chunks_json_path = output_dir / "layout_chunks.json"
            self.assertTrue(chunks_json_path.exists())
            payload = json.loads(chunks_json_path.read_text(encoding="utf-8"))

            self.assertGreaterEqual(payload["analysis"]["chunk_count"], 2)
            self.assertEqual(payload["analysis"]["table_chunk_count"], 1)
            self.assertIn("layout_chunk_result", result["ocr_result"])
            self.assertEqual(result["ocr_result"]["layout_chunk_result"]["analysis"]["table_chunk_count"], 1)


if __name__ == "__main__":
    unittest.main()
