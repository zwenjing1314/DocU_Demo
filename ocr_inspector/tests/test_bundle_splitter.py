from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_7_bundle_splitter import build_bundle_splitter_result


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


def create_multi_page_pdf(pdf_path: Path, page_count: int) -> None:
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()


class BundleSplitterTests(unittest.TestCase):
    def test_splits_same_type_invoices_into_multiple_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "bundle.pdf"
            output_dir = Path(tempdir) / "outputs"
            output_dir.mkdir(parents=True, exist_ok=True)
            create_multi_page_pdf(pdf_path, page_count=2)

            ocr_result = {
                "source_file": "bundle.pdf",
                "source_kind": "pdf",
                "tables": [],
                "pages": [
                    {
                        "page_num": 1,
                        "lines": [
                            make_line(text="Northwind Ltd", left=80, top=60, right=320, bottom=95, page_num=1, line_num=1),
                            make_line(text="Invoice", left=80, top=105, right=220, bottom=140, page_num=1, line_num=2),
                            make_line(text="Total: $15.00", left=80, top=640, right=260, bottom=675, page_num=1, line_num=3),
                        ],
                        "layout": {"items": []},
                    },
                    {
                        "page_num": 2,
                        "lines": [
                            make_line(text="Blue Ocean LLC", left=80, top=60, right=340, bottom=95, page_num=2, line_num=1),
                            make_line(text="Invoice", left=80, top=105, right=220, bottom=140, page_num=2, line_num=2),
                            make_line(text="Total: $42.00", left=80, top=640, right=260, bottom=675, page_num=2, line_num=3),
                        ],
                        "layout": {"items": []},
                    },
                ],
            }

            bundle_result = build_bundle_splitter_result(
                ocr_result,
                source_path=pdf_path,
                output_dir=output_dir,
                source_kind="pdf",
            )

            self.assertEqual(bundle_result["analysis"]["segment_count"], 2)
            self.assertEqual(bundle_result["segments"][0]["start_page"], 1)
            self.assertEqual(bundle_result["segments"][0]["end_page"], 1)
            self.assertEqual(bundle_result["segments"][1]["start_page"], 2)
            self.assertEqual(bundle_result["segments"][1]["end_page"], 2)

            for segment in bundle_result["segments"]:
                self.assertEqual(segment["label"], "invoice")
                self.assertTrue((output_dir / segment["pdf_path"]).exists())
                self.assertTrue((output_dir / segment["json_path"]).exists())

    def test_run_ocr_pipeline_writes_bundle_splitter_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "mixed_bundle.pdf"
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
                        make_line(text="Northwind Ltd", left=80, top=60, right=320, bottom=95, page_num=1, line_num=1),
                        make_line(text="Invoice", left=80, top=105, right=220, bottom=140, page_num=1, line_num=2),
                        make_line(text="Total: $15.00", left=80, top=640, right=260, bottom=675, page_num=1, line_num=3),
                    ]
                else:
                    lines = [
                        make_line(text="Application Form", left=80, top=60, right=320, bottom=95, page_num=2, line_num=1),
                        make_line(text="Name: Alice Chen", left=80, top=120, right=360, bottom=155, page_num=2, line_num=2),
                        make_line(text="Phone: 13800138000", left=80, top=165, right=340, bottom=200, page_num=2, line_num=3),
                        make_line(text="Address: 88 West Lake Road", left=80, top=210, right=460, bottom=245, page_num=2, line_num=4),
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

            bundle_json_path = output_dir / "bundle_splitter.json"
            self.assertTrue(bundle_json_path.exists())

            payload = json.loads(bundle_json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["analysis"]["segment_count"], 2)
            self.assertTrue(payload["analysis"]["detected_bundle"])
            self.assertEqual(payload["segments"][0]["start_page"], 1)
            self.assertEqual(payload["segments"][1]["start_page"], 2)

            for segment in payload["segments"]:
                self.assertTrue((output_dir / segment["json_path"]).exists())
                self.assertTrue((output_dir / segment["pdf_path"]).exists())

            self.assertEqual(result["ocr_result"]["form_result"]["status"], "skipped")
            self.assertEqual(result["ocr_result"]["receipt_invoice_result"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
