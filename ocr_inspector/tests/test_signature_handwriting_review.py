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
from ocr_engine_8_review import build_signature_handwriting_review_result


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


def make_word(
        *,
        text: str,
        confidence: float,
        left: int,
        top: int,
        right: int,
        bottom: int,
        page_num: int = 1,
        block_num: int = 1,
        par_num: int = 1,
        line_num: int = 1,
        word_num: int = 1,
) -> dict:
    return {
        "page_num": page_num,
        "text": text,
        "confidence": confidence,
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
        "word_num": word_num,
        "source": "primary",
        "angle": 0.0,
    }


def create_single_page_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.save(str(pdf_path))
    doc.close()


class SignatureHandwritingReviewTests(unittest.TestCase):
    def test_detects_signature_regions_handwriting_candidates_and_suspicious_fields(self) -> None:
        ocr_result = {
            "source_file": "application_form.pdf",
            "document_label": "form",
            "bundle_splitter_result": {
                "page_classifications": [
                    {"page_num": 1, "label": "form"},
                ]
            },
            "pages": [
                {
                    "page_num": 1,
                    "image_width": 1200,
                    "image_height": 1600,
                    "lines": [
                        make_line(text="Application Form", left=80, top=60, right=320, bottom=96, line_num=1),
                        make_line(text="Applicant Name: Alice Chen", left=80, top=130, right=520, bottom=166, line_num=2),
                        make_line(text="Address: ???", left=80, top=190, right=340, bottom=226, line_num=3),
                        make_line(text="Signature:", left=80, top=260, right=260, bottom=296, line_num=4),
                        make_line(text="A. Chen", left=320, top=262, right=520, bottom=298, line_num=4),
                    ],
                    "words": [
                        make_word(text="Application", confidence=96, left=80, top=60, right=190, bottom=96, line_num=1, word_num=1),
                        make_word(text="Form", confidence=95, left=198, top=60, right=260, bottom=96, line_num=1, word_num=2),
                        make_word(text="Applicant", confidence=96, left=80, top=130, right=200, bottom=166, line_num=2, word_num=1),
                        make_word(text="Name:", confidence=94, left=210, top=130, right=290, bottom=166, line_num=2, word_num=2),
                        make_word(text="Alice", confidence=63, left=310, top=130, right=380, bottom=166, line_num=2, word_num=3),
                        make_word(text="Chen", confidence=61, left=390, top=130, right=450, bottom=166, line_num=2, word_num=4),
                        make_word(text="Address:", confidence=95, left=80, top=190, right=190, bottom=226, line_num=3, word_num=1),
                        make_word(text="???", confidence=40, left=210, top=190, right=260, bottom=226, line_num=3, word_num=2),
                        make_word(text="Signature:", confidence=94, left=80, top=260, right=210, bottom=296, line_num=4, word_num=1),
                        make_word(text="A.", confidence=42, left=320, top=262, right=350, bottom=298, line_num=4, word_num=2),
                        make_word(text="Chen", confidence=39, left=360, top=262, right=430, bottom=298, line_num=4, word_num=3),
                    ],
                    "rejected_words": [],
                }
            ],
        }

        form_result = build_form_to_json_result(ocr_result)
        review_result = build_signature_handwriting_review_result(
            ocr_result,
            form_result=form_result,
            bundle_result=ocr_result["bundle_splitter_result"],
        )

        page_review = review_result["pages"][0]
        self.assertGreaterEqual(len(page_review["signature_regions"]), 1)
        self.assertGreaterEqual(len(page_review["handwriting_regions"]), 1)
        self.assertGreaterEqual(len(page_review["suspicious_fields"]), 2)
        self.assertEqual(review_result["analysis"]["signature_region_count"], 1)

        suspicious_field_names = {item["field_name"] for item in page_review["suspicious_fields"]}
        self.assertIn("address", suspicious_field_names)
        self.assertIn("phone", suspicious_field_names)

    def test_run_ocr_pipeline_writes_review_json_and_overlay(self) -> None:
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
                    make_line(text="Application Form", left=80, top=60, right=320, bottom=96, line_num=1),
                    make_line(text="Applicant Name: Alice Chen", left=80, top=130, right=520, bottom=166, line_num=2),
                    make_line(text="Address: ???", left=80, top=190, right=340, bottom=226, line_num=3),
                    make_line(text="Signature:", left=80, top=260, right=260, bottom=296, line_num=4),
                    make_line(text="A. Chen", left=320, top=262, right=520, bottom=298, line_num=4),
                ]
                words = [
                    make_word(text="Application", confidence=96, left=80, top=60, right=190, bottom=96, line_num=1, word_num=1),
                    make_word(text="Form", confidence=95, left=198, top=60, right=260, bottom=96, line_num=1, word_num=2),
                    make_word(text="Applicant", confidence=96, left=80, top=130, right=200, bottom=166, line_num=2, word_num=1),
                    make_word(text="Name:", confidence=94, left=210, top=130, right=290, bottom=166, line_num=2, word_num=2),
                    make_word(text="Alice", confidence=63, left=310, top=130, right=380, bottom=166, line_num=2, word_num=3),
                    make_word(text="Chen", confidence=61, left=390, top=130, right=450, bottom=166, line_num=2, word_num=4),
                    make_word(text="Address:", confidence=95, left=80, top=190, right=190, bottom=226, line_num=3, word_num=1),
                    make_word(text="???", confidence=40, left=210, top=190, right=260, bottom=226, line_num=3, word_num=2),
                    make_word(text="Signature:", confidence=94, left=80, top=260, right=210, bottom=296, line_num=4, word_num=1),
                    make_word(text="A.", confidence=42, left=320, top=262, right=350, bottom=298, line_num=4, word_num=2),
                    make_word(text="Chen", confidence=39, left=360, top=262, right=430, bottom=298, line_num=4, word_num=3),
                ]
                return {
                    "page_num": page_num,
                    "image_width": rendered_page.width,
                    "image_height": rendered_page.height,
                    "words": words,
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

            review_json_path = output_dir / "signature_handwriting_review.json"
            self.assertTrue(review_json_path.exists())

            payload = json.loads(review_json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["analysis"]["signature_region_count"], 1)
            self.assertGreaterEqual(payload["analysis"]["handwriting_region_count"], 1)
            self.assertGreaterEqual(payload["analysis"]["suspicious_field_count"], 1)

            page_review = payload["pages"][0]
            self.assertTrue(page_review["review_overlay_path"])
            self.assertTrue((output_dir / page_review["review_overlay_path"]).exists())
            self.assertIn("signature_handwriting_review_result", result["ocr_result"])


if __name__ == "__main__":
    unittest.main()
