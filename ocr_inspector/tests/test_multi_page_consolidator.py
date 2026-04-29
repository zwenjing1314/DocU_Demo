from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_11_consolidator import build_multi_page_consolidation_result


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


def create_multi_page_pdf(pdf_path: Path, page_count: int) -> None:
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()


class MultiPageConsolidatorTests(unittest.TestCase):
    def test_consolidates_invoice_items_and_validates_total(self) -> None:
        ocr_result = {
            "source_file": "multi_page_invoice.pdf",
            "source_kind": "pdf",
            "pages": [{"page_num": 1, "lines": []}, {"page_num": 2, "lines": []}],
            "tables": [
                {
                    "table_id": "page_001_table_01",
                    "page_num": 1,
                    "rows": [
                        ["Item", "Qty", "Unit Price", "Amount"],
                        ["USB Cable", "2", "5.00", "10.00"],
                        ["Charger", "1", "20.00", "20.00"],
                    ],
                },
                {
                    "table_id": "page_002_table_01",
                    "page_num": 2,
                    "rows": [
                        ["Item", "Qty", "Unit Price", "Amount"],
                        ["Charger", "1", "20.00", "20.00"],
                        ["Dock", "1", "30.00", "30.00"],
                    ],
                },
            ],
            "receipt_invoice_result": {
                "normalized_receipt": {
                    "vendor": "Acme Store Ltd",
                    "date": "2026-04-28",
                    "invoice_number": "INV-001",
                    "currency": "USD",
                    "tax": 5.5,
                    "total": 65.5,
                    "items": [],
                },
            },
            "bundle_splitter_result": {"segments": [], "analysis": {"detected_bundle": False}},
            "form_result": {},
            "contract_schema_result": {},
        }

        result = build_multi_page_consolidation_result(ocr_result)
        receipt = result["consolidated"]["receipt_invoice"]

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["document_kind"], "invoice_receipt")
        self.assertEqual(len(receipt["items"]), 3)
        self.assertEqual(result["analysis"]["duplicate_item_count"], 1)
        self.assertEqual(receipt["item_sum"], 60.0)
        self.assertEqual(receipt["calculated_total"], 65.5)
        self.assertEqual(receipt["total_validation"]["status"], "matched")

    def test_consolidates_bank_transactions_and_validates_balance(self) -> None:
        ocr_result = {
            "source_file": "statement.pdf",
            "source_kind": "pdf",
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Bank Statement", left=80, top=60, right=300, bottom=95, page_num=1),
                        make_line(text="Opening Balance $1,000.00", left=80, top=110, right=340, bottom=145, page_num=1),
                    ],
                },
                {
                    "page_num": 2,
                    "lines": [
                        make_line(text="Closing Balance $1,250.00", left=80, top=700, right=360, bottom=735, page_num=2),
                    ],
                },
            ],
            "tables": [
                {
                    "table_id": "page_001_table_01",
                    "page_num": 1,
                    "rows": [
                        ["Date", "Description", "Debit", "Credit", "Balance"],
                        ["2026/04/01", "Salary", "", "300.00", "1,300.00"],
                        ["2026/04/02", "ATM", "50.00", "", "1,250.00"],
                    ],
                },
                {
                    "table_id": "page_002_table_01",
                    "page_num": 2,
                    "rows": [
                        ["Date", "Description", "Debit", "Credit", "Balance"],
                        ["2026/04/01", "Salary", "", "300.00", "1,300.00"],
                    ],
                },
            ],
            "receipt_invoice_result": {},
            "bundle_splitter_result": {"segments": [], "analysis": {"detected_bundle": False}},
            "form_result": {},
            "contract_schema_result": {},
        }

        result = build_multi_page_consolidation_result(ocr_result)
        statement = result["consolidated"]["bank_statement"]

        self.assertEqual(result["document_kind"], "bank_statement")
        self.assertEqual(len(statement["transactions"]), 2)
        self.assertEqual(result["analysis"]["duplicate_transaction_count"], 1)
        self.assertEqual(statement["net_change"], 250.0)
        self.assertEqual(statement["balance_validation"]["status"], "matched")

    def test_run_ocr_pipeline_writes_multi_page_consolidation_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "invoice.pdf"
            output_dir = Path(tempdir) / "outputs"
            create_multi_page_pdf(pdf_path, page_count=2)

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
                        make_line(text="Acme Store Ltd", left=80, top=60, right=360, bottom=95, page_num=1, line_num=1),
                        make_line(text="Invoice", left=80, top=105, right=220, bottom=140, page_num=1, line_num=2),
                        make_line(text="Date: 2026/04/28", left=80, top=150, right=340, bottom=185, page_num=1, line_num=3),
                    ]
                else:
                    lines = [
                        make_line(text="Items Continued", left=80, top=60, right=320, bottom=95, page_num=2, line_num=1),
                        make_line(text="Tax: $5.50", left=80, top=620, right=260, bottom=655, page_num=2, line_num=2),
                        make_line(text="Total: $65.50", left=80, top=665, right=300, bottom=700, page_num=2, line_num=3),
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

            def fake_extract_tables(pdf_page: pymupdf.Page, page_num: int) -> list[dict]:
                if page_num == 1:
                    return [
                        {
                            "page_num": 1,
                            "source": "pdf_text",
                            "row_count": 3,
                            "col_count": 4,
                            "bbox": {"left": 80, "top": 220, "width": 400, "height": 160, "right": 480, "bottom": 380},
                            "rows": [
                                ["Item", "Qty", "Unit Price", "Amount"],
                                ["USB Cable", "2", "5.00", "10.00"],
                                ["Charger", "1", "20.00", "20.00"],
                            ],
                        }
                    ]
                return [
                    {
                        "page_num": 2,
                        "source": "pdf_text",
                        "row_count": 3,
                        "col_count": 4,
                        "bbox": {"left": 80, "top": 120, "width": 400, "height": 160, "right": 480, "bottom": 280},
                        "rows": [
                            ["Item", "Qty", "Unit Price", "Amount"],
                            ["Charger", "1", "20.00", "20.00"],
                            ["Dock", "1", "30.00", "30.00"],
                        ],
                    }
                ]

            with (
                patch.object(ocr_engine, "_ocr_image", side_effect=fake_ocr_image),
                patch.object(ocr_engine, "_extract_tables_from_pdf_page", side_effect=fake_extract_tables),
            ):
                result = ocr_engine.run_ocr_pipeline(
                    source_path=pdf_path,
                    output_dir=output_dir,
                    source_kind="pdf",
                    lang="eng",
                )

            consolidation_json_path = output_dir / "multi_page_consolidation.json"
            self.assertTrue(consolidation_json_path.exists())

            payload = json.loads(consolidation_json_path.read_text(encoding="utf-8"))
            receipt = payload["consolidated"]["receipt_invoice"]
            self.assertEqual(payload["analysis"]["consolidated_item_count"], 3)
            self.assertEqual(payload["analysis"]["duplicate_item_count"], 1)
            self.assertEqual(receipt["total_validation"]["status"], "matched")
            self.assertEqual(result["ocr_result"]["multi_page_consolidation_result"]["analysis"]["consolidated_item_count"], 3)


if __name__ == "__main__":
    unittest.main()
