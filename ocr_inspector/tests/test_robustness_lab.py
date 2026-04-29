from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
import pymupdf

import ocr_engine
from ocr_engine_17_robustness_lab import (
    DEGRADATION_VARIANTS,
    build_robustness_lab_result,
    write_degradation_report_json,
)


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
        "confidence": 92.0,
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


def make_word(text: str, left: int, top: int, right: int, bottom: int, confidence: float = 92.0) -> dict:
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


def create_page_image(image_path: Path) -> None:
    image = Image.new("RGB", (640, 440), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "Robustness Test Invoice", fill=(10, 10, 10))
    draw.text((40, 90), "Date: 2026-04-29", fill=(10, 10, 10))
    draw.rectangle((40, 150, 460, 270), outline=(20, 20, 20), width=2)
    for x in (180, 320):
        draw.line((x, 150, x, 270), fill=(20, 20, 20), width=2)
    for y in (190, 230):
        draw.line((40, y, 460, y), fill=(20, 20, 20), width=2)
    draw.text((55, 165), "Item", fill=(0, 0, 0))
    draw.text((200, 165), "Qty", fill=(0, 0, 0))
    draw.text((340, 165), "Amount", fill=(0, 0, 0))
    draw.text((55, 205), "Cable", fill=(0, 0, 0))
    draw.text((200, 205), "2", fill=(0, 0, 0))
    draw.text((340, 205), "$10.00", fill=(0, 0, 0))
    image.save(image_path)


def create_single_page_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Robustness Test Invoice", fontsize=18)
    page.insert_text((72, 120), "Total: $10.00", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()


def make_ocr_result() -> dict:
    return {
        "source_file": "invoice.pdf",
        "source_kind": "pdf",
        "page_count": 1,
        "pages": [
            {
                "page_num": 1,
                "words": [
                    make_word("Robustness", 40, 40, 160, 65),
                    make_word("Invoice", 170, 40, 240, 65, confidence=76.0),
                ],
                "rejected_words": [],
                "lines": [
                    make_line(text="Robustness Test Invoice", left=40, top=40, right=290, bottom=70),
                    make_line(text="Date: 2026-04-29", left=40, top=90, right=260, bottom=120, line_num=2),
                ],
                "layout": {
                    "items": [
                        {"type": "heading", "text": "Robustness Test Invoice", "page_num": 1, "level": 1},
                        {"type": "paragraph", "text": "Date: 2026-04-29", "page_num": 1},
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
        "form_analysis": {"field_count": 2, "selected_option_count": 0},
        "receipt_invoice_analysis": {"line_item_count": 1},
        "contract_schema_result": {"analysis": {"field_count": 0}},
        "multi_page_consolidation_result": {"analysis": {"consolidated_item_count": 1, "transaction_count": 0}},
        "layout_chunk_result": {"analysis": {"chunk_count": 2, "table_chunk_count": 1}},
        "direct_pdf_structure_result": {"analysis": {"native_text_page_count": 1}},
        "query_extractor_result": {"analysis": {"candidate_count": 2}},
        "evidence_qa_result": {"analysis": {"unit_count": 2, "query_history_count": 0}},
        "complex_page_analysis_result": {"analysis": {"chart_candidate_count": 0, "qa_ready": False}},
    }


class RobustnessLabTests(unittest.TestCase):
    def test_generates_degradation_variants_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir)
            page_path = output_dir / "page_001.png"
            create_page_image(page_path)

            result = build_robustness_lab_result(
                make_ocr_result(),
                page_image_paths_by_page={1: page_path},
                output_dir=output_dir,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["analysis"]["variant_count"], len(DEGRADATION_VARIANTS))
            self.assertGreaterEqual(result["analysis"]["variant_count"], 5)
            self.assertEqual(result["analysis"]["generated_page_count"], len(DEGRADATION_VARIANTS))
            self.assertIn(result["analysis"]["most_fragile_layer"], {"ocr", "layout", "extraction", "reasoning"})

            for variant in result["variants"]:
                self.assertIn(variant["likely_failure_layer"], {"ocr", "layout", "extraction", "reasoning"})
                generated_path = output_dir / variant["generated_pages"][0]["image_path"]
                self.assertTrue(generated_path.exists())

            report_path = output_dir / "degradation_report.json"
            write_degradation_report_json(result, report_path)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], "1.0")

    def test_supports_optional_ocr_probe_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir)
            page_path = output_dir / "page_001.png"
            create_page_image(page_path)

            def fake_probe(payload: dict) -> dict:
                self.assertTrue(Path(payload["image_path"]).exists())
                return {
                    "ocr": {"word_count": 1, "average_confidence": 48.0},
                    "layout": {"layout_item_count": 1, "table_count": 0},
                    "extraction": {"table_count": 0},
                    "reasoning": {"evidence_unit_count": 0},
                }

            result = build_robustness_lab_result(
                make_ocr_result(),
                page_image_paths_by_page={1: page_path},
                output_dir=output_dir,
                degraded_page_evaluator=fake_probe,
            )

            self.assertEqual(result["analysis"]["evaluation_mode"], "ocr_probe_plus_visual_proxy")
            self.assertEqual(result["variants"][0]["comparison_mode"], "ocr_probe_plus_visual_proxy")
            self.assertTrue(result["variants"][0]["comparison"]["ocr"]["probe"])

    def test_run_ocr_pipeline_writes_degradation_report_json(self) -> None:
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
                    make_line(text="Robustness Test Invoice", left=80, top=60, right=420, bottom=105),
                    make_line(text="Total: $10.00", left=80, top=140, right=280, bottom=175, line_num=2),
                ]
                return {
                    "page_num": page_num,
                    "image_width": rendered_page.width,
                    "image_height": rendered_page.height,
                    "words": [
                        make_word("Robustness", 80, 60, 190, 95),
                        make_word("Invoice", 200, 60, 280, 95),
                    ],
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

            report_path = output_dir / "degradation_report.json"
            self.assertTrue(report_path.exists())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(payload["analysis"]["variant_count"], 5)
            self.assertIn("robustness_lab_result", result["ocr_result"])
            self.assertEqual(
                result["ocr_result"]["robustness_lab_result"]["analysis"]["generated_page_count"],
                payload["analysis"]["generated_page_count"],
            )


if __name__ == "__main__":
    unittest.main()
