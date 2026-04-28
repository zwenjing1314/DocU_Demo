from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_5_receipt import build_receipt_invoice_result


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


class ReceiptInvoiceExtractorTests(unittest.TestCase):
    def test_extracts_vendor_date_tax_total_and_items_from_invoice_table(self) -> None:
        ocr_result = {
            "source_file": "sample_invoice.pdf",
            "tables": [
                {
                    "table_id": "page_001_table_01",
                    "page_num": 1,
                    "source": "pdf_text",
                    "row_count": 4,
                    "col_count": 4,
                    "rows": [
                        ["Item", "Qty", "Unit Price", "Amount"],
                        ["USB Cable", "2", "5.00", "10.00"],
                        ["Charger", "1", "15.00", "15.00"],
                        ["Tax", "", "", "2.50"],
                    ],
                }
            ],
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Acme Store Ltd", left=80, top=60, right=360, bottom=95, line_num=1),
                        make_line(text="Invoice", left=80, top=105, right=220, bottom=140, line_num=2),
                        make_line(text="Date: 2026/04/28", left=80, top=150, right=340, bottom=185, line_num=3),
                        make_line(text="Tax: $2.50", left=80, top=620, right=260, bottom=655, line_num=4),
                        make_line(text="Total: $27.50", left=80, top=665, right=300, bottom=700, line_num=5),
                    ],
                }
            ],
        }

        result = build_receipt_invoice_result(ocr_result)
        normalized = result["normalized_receipt"]

        self.assertEqual(normalized["vendor"], "Acme Store Ltd")
        self.assertEqual(normalized["date"], "2026-04-28")
        self.assertEqual(normalized["tax"], 2.5)
        self.assertEqual(normalized["total"], 27.5)
        self.assertEqual(len(normalized["items"]), 2)
        self.assertEqual(normalized["items"][0]["description"], "USB Cable")
        self.assertEqual(normalized["items"][0]["quantity"], 2.0)
        self.assertEqual(normalized["items"][0]["unit_price"], 5.0)
        self.assertEqual(normalized["items"][0]["amount"], 10.0)

    def test_falls_back_to_line_based_items_for_simple_receipt(self) -> None:
        ocr_result = {
            "source_file": "receipt.pdf",
            "tables": [],
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Blue Cafe", left=80, top=60, right=260, bottom=95, line_num=1),
                        make_line(text="Receipt", left=80, top=100, right=220, bottom=135, line_num=2),
                        make_line(text="2026-04-28", left=80, top=140, right=220, bottom=175, line_num=3),
                        make_line(text="Latte 4.50", left=80, top=240, right=280, bottom=275, line_num=4),
                        make_line(text="Bagel 3.20", left=80, top=285, right=260, bottom=320, line_num=5),
                        make_line(text="Tax 0.70", left=80, top=360, right=220, bottom=395, line_num=6),
                        make_line(text="Total 8.40", left=80, top=405, right=240, bottom=440, line_num=7),
                    ],
                }
            ],
        }

        result = build_receipt_invoice_result(ocr_result)
        normalized = result["normalized_receipt"]

        self.assertEqual(normalized["vendor"], "Blue Cafe")
        self.assertEqual(normalized["date"], "2026-04-28")
        self.assertEqual(normalized["tax"], 0.7)
        self.assertEqual(normalized["total"], 8.4)
        self.assertEqual(len(normalized["items"]), 2)
        self.assertEqual(normalized["items"][0]["description"], "Latte")
        self.assertEqual(normalized["items"][0]["amount"], 4.5)

    def test_run_ocr_pipeline_writes_receipt_invoice_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "invoice.pdf"
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
                    make_line(text="Acme Store Ltd", left=80, top=60, right=360, bottom=95, line_num=1),
                    make_line(text="Invoice", left=80, top=105, right=220, bottom=140, line_num=2),
                    make_line(text="Date: 2026/04/28", left=80, top=150, right=340, bottom=185, line_num=3),
                    make_line(text="Tax: $2.50", left=80, top=620, right=260, bottom=655, line_num=4),
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

            fake_tables = [
                {
                    "page_num": 1,
                    "source": "pdf_text",
                    "row_count": 3,
                    "col_count": 4,
                    "bbox": {"left": 80, "top": 220, "width": 400, "height": 160, "right": 480, "bottom": 380},
                    "rows": [
                        ["Item", "Qty", "Unit Price", "Amount"],
                        ["USB Cable", "2", "5.00", "10.00"],
                        ["Charger", "1", "15.00", "15.00"],
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

            receipt_json_path = output_dir / "receipt_invoice.json"
            self.assertTrue(receipt_json_path.exists())

            payload = json.loads(receipt_json_path.read_text(encoding="utf-8"))
            normalized = payload["normalized_receipt"]
            self.assertEqual(normalized["vendor"], "Acme Store Ltd")
            self.assertEqual(normalized["date"], "2026-04-28")
            self.assertEqual(normalized["tax"], 2.5)
            self.assertEqual(normalized["total"], 27.5)
            self.assertEqual(len(normalized["items"]), 2)
            self.assertEqual(result["ocr_result"]["receipt_invoice_analysis"]["line_item_count"], 2)


if __name__ == "__main__":
    unittest.main()
