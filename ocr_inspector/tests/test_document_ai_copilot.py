from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_18_copilot import (
    PIPELINE_STAGE_ORDER,
    build_document_ai_copilot_markdown,
    build_document_ai_copilot_result,
    write_document_ai_copilot_json,
    write_document_ai_copilot_markdown,
)


def make_word(text: str, left: int, top: int, right: int, bottom: int, confidence: float = 94.0) -> dict:
    return {
        "text": text,
        "confidence": confidence,
        "block_num": 1,
        "par_num": 1,
        "line_num": 1,
        "word_num": 1,
        "source": "primary",
        "angle": 0.0,
        "bbox": {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
            "right": right,
            "bottom": bottom,
        },
    }


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
        "confidence": 94.0,
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


def create_invoice_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Invoice", fontsize=20)
    page.insert_text((72, 120), "Vendor: Acme Store", fontsize=12)
    page.insert_text((72, 150), "Date: 2026-04-29", fontsize=12)
    page.insert_text((72, 180), "Total: $65.50", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()


def make_copilot_ocr_result() -> dict:
    return {
        "source_file": "mixed_bundle.pdf",
        "source_kind": "pdf",
        "page_count": 3,
        "document_label": "invoice",
        "pages": [
            {
                "page_num": 1,
                "words": [
                    make_word("Invoice", 80, 60, 160, 90),
                    make_word("Total", 80, 160, 140, 190, confidence=72.0),
                ],
                "lines": [
                    make_line(text="Invoice", left=80, top=60, right=160, bottom=90),
                    make_line(text="Total: $65.50", left=80, top=160, right=260, bottom=190, line_num=2),
                ],
                "layout": {
                    "items": [
                        {"type": "heading", "level": 1, "text": "Invoice", "page_num": 1},
                        {"type": "paragraph", "text": "Total: $65.50", "page_num": 1},
                    ]
                },
            }
        ],
        "tables": [
            {
                "table_id": "page_001_table_01",
                "page_num": 1,
                "rows": [["Item", "Qty", "Amount"], ["Cable", "2", "$10.00"]],
            }
        ],
        "document_router_result": {
            "label": "invoice",
            "matched_signals": [{"label": "invoice", "signal": "invoice_keywords:1"}],
            "dispatch": {"stages": ["ocr_inspector", "layout_reader", "receipt_invoice_extractor"]},
            "analysis": {"confidence": 0.88},
        },
        "bundle_splitter_result": {
            "segments": [
                {"segment_id": "segment_01", "label": "invoice", "start_page": 1, "end_page": 2, "confidence": 0.9},
                {"segment_id": "segment_02", "label": "form", "start_page": 3, "end_page": 3, "confidence": 0.78},
            ],
            "analysis": {"segment_count": 2, "detected_bundle": True},
        },
        "form_result": {"normalized_form": {"name": "Ada", "selected_options": ["Yes"]}},
        "form_analysis": {"field_count": 1, "selected_option_count": 1},
        "receipt_invoice_result": {
            "normalized_receipt": {
                "vendor": "Acme Store",
                "date": "2026-04-29",
                "tax": 5.5,
                "total": 65.5,
                "items": [{"description": "Cable", "amount": 10.0}],
            }
        },
        "receipt_invoice_analysis": {"line_item_count": 1},
        "contract_schema_result": {"analysis": {"field_count": 0}, "normalized_contract": {}},
        "multi_page_consolidation_result": {
            "document_kind": "invoice_receipt",
            "consolidated": {"receipt_invoice": {}},
            "analysis": {"total_check_status": "matched", "balance_check_status": "not_available"},
        },
        "layout_chunk_result": {
            "chunks": [{"chunk_id": "chunk_0001_text", "type": "text", "page_nums": [1]}],
            "analysis": {"chunk_count": 2, "table_chunk_count": 1, "heading_context_chunk_count": 1},
        },
        "direct_pdf_structure_result": {
            "strict_schema": {"summary": {"short": "Invoice from Acme Store with one item.", "page_count": 3}},
            "analysis": {"native_text_page_count": 3},
        },
        "evidence_qa_result": {"analysis": {"unit_count": 4, "query_history_count": 0}},
        "complex_page_analysis_result": {"analysis": {"chart_candidate_count": 0, "qa_ready": False}},
        "signature_handwriting_review_result": {
            "pages": [{"page_num": 1}],
            "analysis": {
                "review_page_count": 1,
                "signature_region_count": 0,
                "handwriting_region_count": 0,
                "suspicious_field_count": 1,
            },
        },
        "robustness_lab_result": {
            "analysis": {
                "variant_count": 6,
                "generated_page_count": 6,
                "most_fragile_layer": "ocr",
                "evaluation_mode": "visual_proxy",
            }
        },
    }


class DocumentAiCopilotTests(unittest.TestCase):
    def test_builds_connected_product_pipeline(self) -> None:
        result = build_document_ai_copilot_result(make_copilot_ocr_result())

        self.assertEqual(result["schema_version"], "1.0")
        self.assertEqual(result["document_package"]["document_label"], "invoice")
        self.assertTrue(result["document_package"]["detected_bundle"])
        self.assertEqual(result["pipeline"]["stage_order"], list(PIPELINE_STAGE_ORDER))
        self.assertEqual(len(result["pipeline"]["stages"]), len(PIPELINE_STAGE_ORDER))
        self.assertEqual(result["readiness"]["status"], "ready_with_review")
        self.assertTrue(result["readiness"]["demo_ready"])
        self.assertIn("总金额是多少？", result["qa"]["suggested_questions"])

        stage_by_id = {stage["stage_id"]: stage for stage in result["pipeline"]["stages"]}
        self.assertEqual(stage_by_id["human_review"]["status"], "needs_review")
        self.assertEqual(stage_by_id["exports"]["metrics"]["markdown_export_count"], 2)

    def test_writes_json_and_markdown_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir)
            result = build_document_ai_copilot_result(make_copilot_ocr_result())
            json_path = output_dir / "document_ai_copilot.json"
            markdown_path = output_dir / "document_ai_copilot.md"

            write_document_ai_copilot_json(result, json_path)
            write_document_ai_copilot_markdown(result, markdown_path)

            saved = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertEqual(saved["status"], "ready_with_review")
            self.assertIn("# End-to-End Document AI Copilot", markdown)
            self.assertIn("Pipeline Stages", build_document_ai_copilot_markdown(result))

    def test_run_ocr_pipeline_writes_copilot_files(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "invoice.pdf"
            output_dir = Path(tempdir) / "outputs"
            create_invoice_pdf(pdf_path)

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
                    make_line(text="Invoice", left=80, top=60, right=180, bottom=100),
                    make_line(text="Vendor: Acme Store", left=80, top=120, right=320, bottom=155, line_num=2),
                    make_line(text="Date: 2026-04-29", left=80, top=165, right=320, bottom=200, line_num=3),
                    make_line(text="Tax: $5.50", left=80, top=610, right=260, bottom=645, line_num=4),
                    make_line(text="Total: $65.50", left=80, top=660, right=300, bottom=695, line_num=5),
                ]
                return {
                    "page_num": page_num,
                    "image_width": rendered_page.width,
                    "image_height": rendered_page.height,
                    "words": [
                        make_word("Invoice", 80, 60, 180, 100),
                        make_word("Total", 80, 660, 150, 695),
                    ],
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
                    "bbox": {"left": 80, "top": 220, "width": 420, "height": 140, "right": 500, "bottom": 360},
                    "rows": [
                        ["Item", "Qty", "Unit Price", "Amount"],
                        ["Cable", "2", "5.00", "10.00"],
                        ["Dock", "1", "50.00", "50.00"],
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

            copilot_json_path = output_dir / "document_ai_copilot.json"
            copilot_markdown_path = output_dir / "document_ai_copilot.md"
            self.assertTrue(copilot_json_path.exists())
            self.assertTrue(copilot_markdown_path.exists())
            payload = json.loads(copilot_json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["readiness"]["stage_count"], len(PIPELINE_STAGE_ORDER))
            self.assertIn("document_ai_copilot_result", result["ocr_result"])
            self.assertIn("End-to-End Document AI Copilot", copilot_markdown_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
