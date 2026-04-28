from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_6_router import build_mixed_document_router_result, route_documents_in_folder


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


def make_layout_item(item_type: str, text: str, page_num: int = 1) -> dict:
    return {
        "type": item_type,
        "text": text,
        "page_num": page_num,
    }


def create_single_page_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()


class MixedDocumentRouterTests(unittest.TestCase):
    def test_classifies_invoice_documents(self) -> None:
        ocr_result = {
            "source_file": "invoice.pdf",
            "tables": [
                {
                    "rows": [
                        ["Item", "Qty", "Unit Price", "Amount"],
                        ["USB Cable", "2", "5.00", "10.00"],
                    ]
                }
            ],
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Acme Store Ltd", left=80, top=60, right=360, bottom=95, line_num=1),
                        make_line(text="Invoice", left=80, top=105, right=220, bottom=140, line_num=2),
                        make_line(text="Invoice No: INV-001", left=80, top=145, right=320, bottom=180, line_num=3),
                        make_line(text="Tax: $2.50", left=80, top=620, right=260, bottom=655, line_num=4),
                        make_line(text="Total: $27.50", left=80, top=665, right=300, bottom=700, line_num=5),
                    ],
                    "layout": {"items": []},
                }
            ],
        }

        router_result = build_mixed_document_router_result(ocr_result)

        self.assertEqual(router_result["label"], "invoice")
        self.assertEqual(
            router_result["selected_pipeline"]["downstream_processor"],
            "receipt_invoice_extractor",
        )

    def test_classifies_receipt_documents(self) -> None:
        ocr_result = {
            "source_file": "receipt.png",
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
                        make_line(text="Total 8.40", left=80, top=405, right=240, bottom=440, line_num=6),
                    ],
                    "layout": {"items": []},
                }
            ],
        }

        router_result = build_mixed_document_router_result(ocr_result)

        self.assertEqual(router_result["label"], "receipt")
        self.assertEqual(
            router_result["selected_pipeline"]["downstream_processor"],
            "receipt_invoice_extractor",
        )

    def test_classifies_form_documents(self) -> None:
        ocr_result = {
            "source_file": "registration_form.pdf",
            "tables": [],
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Application Form", left=80, top=60, right=320, bottom=95, line_num=1),
                        make_line(text="Name: Alice Chen", left=80, top=120, right=360, bottom=155, line_num=2),
                        make_line(text="Date: 2026/04/28", left=80, top=165, right=320, bottom=200, line_num=3),
                        make_line(text="Address: 88 West Lake Road", left=80, top=210, right=460, bottom=245, line_num=4),
                        make_line(text="Phone: 13800138000", left=80, top=255, right=360, bottom=290, line_num=5),
                        make_line(text="☑ Newsletter ☐ SMS", left=80, top=300, right=380, bottom=335, line_num=6),
                    ],
                    "layout": {"items": []},
                }
            ],
        }

        router_result = build_mixed_document_router_result(ocr_result)

        self.assertEqual(router_result["label"], "form")
        self.assertEqual(
            router_result["selected_pipeline"]["downstream_processor"],
            "form_to_json",
        )

    def test_classifies_report_documents(self) -> None:
        ocr_result = {
            "source_file": "analysis_report.pdf",
            "tables": [],
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Quarterly Analysis Report", left=80, top=60, right=420, bottom=100, line_num=1),
                        make_line(text="This quarter shows steady revenue growth across three markets.", left=80, top=150, right=720, bottom=190, line_num=2),
                        make_line(text="The operations team also reduced turnaround time by 18 percent.", left=80, top=200, right=740, bottom=240, line_num=3),
                    ],
                    "layout": {
                        "items": [
                            make_layout_item("heading", "Quarterly Analysis Report"),
                            make_layout_item("paragraph", "This quarter shows steady revenue growth across three markets."),
                            make_layout_item("paragraph", "The operations team also reduced turnaround time by 18 percent."),
                        ]
                    },
                },
                {
                    "page_num": 2,
                    "lines": [
                        make_line(text="Conclusion", left=80, top=60, right=220, bottom=100, page_num=2, line_num=1),
                        make_line(text="The document recommends expanding the pilot program next quarter.", left=80, top=150, right=760, bottom=190, page_num=2, line_num=2),
                    ],
                    "layout": {
                        "items": [
                            make_layout_item("heading", "Conclusion", page_num=2),
                            make_layout_item("paragraph", "The document recommends expanding the pilot program next quarter.", page_num=2),
                        ]
                    },
                },
            ],
        }

        router_result = build_mixed_document_router_result(ocr_result)

        self.assertEqual(router_result["label"], "report")
        self.assertEqual(router_result["selected_pipeline"]["downstream_processor"], "")

    def test_classifies_id_documents(self) -> None:
        ocr_result = {
            "source_file": "id_card.jpg",
            "tables": [],
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Resident Identity Card", left=80, top=60, right=360, bottom=95, line_num=1),
                        make_line(text="Name: Zhang San", left=80, top=120, right=320, bottom=155, line_num=2),
                        make_line(text="Sex: Male", left=80, top=165, right=240, bottom=200, line_num=3),
                        make_line(text="Date of Birth: 1990/01/01", left=80, top=210, right=420, bottom=245, line_num=4),
                        make_line(text="Nationality: Chinese", left=80, top=255, right=360, bottom=290, line_num=5),
                    ],
                    "layout": {"items": []},
                }
            ],
        }

        router_result = build_mixed_document_router_result(ocr_result)

        self.assertEqual(router_result["label"], "id")
        self.assertEqual(
            router_result["selected_pipeline"]["downstream_processor"],
            "form_to_json",
        )

    def test_route_documents_in_folder_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source_dir = Path(tempdir) / "mixed_sources"
            output_dir = Path(tempdir) / "router_outputs"
            source_dir.mkdir(parents=True, exist_ok=True)

            (source_dir / "invoice.pdf").write_bytes(b"invoice")
            (source_dir / "form.png").write_bytes(b"form")
            (source_dir / "readme.txt").write_text("ignore", encoding="utf-8")

            def fake_pipeline_runner(*, source_path: Path, output_dir: Path, source_kind: str, **_: object) -> dict:
                label = "invoice" if source_path.stem == "invoice" else "form"
                return {
                    "ocr_result": {
                        "document_router_result": {
                            "label": label,
                            "selected_pipeline": {
                                "label": label,
                                "stages": ["ocr_inspector", "layout_reader", "table_to_csv"],
                            },
                        }
                    }
                }

            summary = route_documents_in_folder(
                source_dir,
                output_dir,
                pipeline_runner=fake_pipeline_runner,
            )

            self.assertEqual(summary["document_count"], 2)
            self.assertEqual(summary["label_counts"]["invoice"], 1)
            self.assertEqual(summary["label_counts"]["form"], 1)

            index_path = output_dir / "mixed_router_index.json"
            self.assertTrue(index_path.exists())
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["document_count"], 2)

    def test_run_ocr_pipeline_writes_document_router_json(self) -> None:
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
                    make_line(text="Invoice No: INV-001", left=80, top=145, right=320, bottom=180, line_num=3),
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

            router_json_path = output_dir / "document_router.json"
            self.assertTrue(router_json_path.exists())

            payload = json.loads(router_json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["label"], "invoice")
            self.assertEqual(
                payload["selected_pipeline"]["downstream_processor"],
                "receipt_invoice_extractor",
            )
            self.assertEqual(result["ocr_result"]["document_label"], "invoice")


if __name__ == "__main__":
    unittest.main()
