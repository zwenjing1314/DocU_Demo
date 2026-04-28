from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_4_json import build_form_to_json_result


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


class FormToJsonTests(unittest.TestCase):
    def test_extracts_inline_pairs_and_checkbox_markers(self) -> None:
        ocr_result = {
            "source_file": "registration_form.pdf",
            "pages": [
                {
                    "page_num": 1,
                    "image_width": 1200,
                    "image_height": 1600,
                    "lines": [
                        make_line(text="Applicant Name: Alice Chen", left=80, top=80, right=520, bottom=120, line_num=1),
                        make_line(text="Date: 2026/04/28", left=80, top=140, right=420, bottom=180, line_num=2),
                        make_line(text="Address: 88 West Lake Road, Hangzhou", left=80, top=200, right=720, bottom=240, line_num=3),
                        make_line(text="Phone: 13800138000", left=80, top=260, right=420, bottom=300, line_num=4),
                        make_line(text="Email: alice@example.com", left=80, top=320, right=520, bottom=360, line_num=5),
                        make_line(text="ID Number: 330102199001011234", left=80, top=380, right=600, bottom=420, line_num=6),
                        make_line(text="Gender: Female", left=80, top=440, right=360, bottom=480, line_num=7),
                        make_line(text="☑ Newsletter ☐ SMS", left=80, top=500, right=420, bottom=540, line_num=8),
                    ],
                    "words": [],
                    "rejected_words": [],
                }
            ],
        }

        form_result = build_form_to_json_result(ocr_result)
        normalized = form_result["normalized_form"]

        self.assertEqual(normalized["name"], "Alice Chen")
        self.assertEqual(normalized["date"], "2026-04-28")
        self.assertEqual(normalized["address"], "88 West Lake Road, Hangzhou")
        self.assertEqual(normalized["phone"], "13800138000")
        self.assertEqual(normalized["email"], "alice@example.com")
        self.assertEqual(normalized["id_number"], "330102199001011234")
        self.assertEqual(normalized["gender"], "female")
        self.assertEqual(normalized["selected_options"], ["Newsletter"])
        self.assertEqual(form_result["analysis"]["field_count"], 7)

    def test_extracts_spatial_values_for_label_and_value_split_layout(self) -> None:
        ocr_result = {
            "source_file": "split_layout_form.pdf",
            "pages": [
                {
                    "page_num": 1,
                    "image_width": 1200,
                    "image_height": 1600,
                    "lines": [
                        make_line(text="Name", left=80, top=100, right=180, bottom=135, line_num=1),
                        make_line(text="Alice Chen", left=320, top=102, right=520, bottom=136, line_num=2),
                        make_line(text="Date", left=80, top=170, right=160, bottom=205, line_num=3),
                        make_line(text="2026年4月28日", left=320, top=172, right=520, bottom=206, line_num=4),
                        make_line(text="Address", left=80, top=240, right=200, bottom=275, line_num=5),
                        make_line(text="Room 301, Building A", left=90, top=292, right=460, bottom=328, line_num=6),
                        make_line(text="88 West Lake Road", left=90, top=336, right=420, bottom=372, line_num=7),
                        make_line(text="Phone", left=80, top=410, right=180, bottom=445, line_num=8),
                        make_line(text="13800138000", left=320, top=412, right=500, bottom=446, line_num=9),
                        make_line(text="☑ Male ☐ Female", left=80, top=480, right=360, bottom=515, line_num=10),
                    ],
                    "words": [],
                    "rejected_words": [],
                }
            ],
        }

        form_result = build_form_to_json_result(ocr_result)
        normalized = form_result["normalized_form"]

        self.assertEqual(normalized["name"], "Alice Chen")
        self.assertEqual(normalized["date"], "2026-04-28")
        self.assertEqual(normalized["address"], "Room 301, Building A 88 West Lake Road")
        self.assertEqual(normalized["phone"], "13800138000")
        self.assertEqual(normalized["gender"], "male")
        self.assertEqual(normalized["selected_options"], ["male"])

    def test_run_ocr_pipeline_writes_form_json_export(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "form.pdf"
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
                    make_line(text="Applicant Name: Alice Chen", left=80, top=80, right=520, bottom=120, line_num=1),
                    make_line(text="Date: 2026/04/28", left=80, top=140, right=420, bottom=180, line_num=2),
                    make_line(text="Address: 88 West Lake Road, Hangzhou", left=80, top=200, right=720, bottom=240, line_num=3),
                    make_line(text="☑ Newsletter ☐ SMS", left=80, top=260, right=420, bottom=300, line_num=4),
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

            form_json_path = output_dir / "form.json"
            self.assertTrue(form_json_path.exists())

            payload = json.loads(form_json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["normalized_form"]["name"], "Alice Chen")
            self.assertEqual(payload["normalized_form"]["date"], "2026-04-28")
            self.assertEqual(payload["normalized_form"]["selected_options"], ["Newsletter"])
            self.assertEqual(result["ocr_result"]["form_analysis"]["field_count"], 3)


if __name__ == "__main__":
    unittest.main()
