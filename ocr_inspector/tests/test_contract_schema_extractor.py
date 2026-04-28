from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine
from ocr_engine_10_contract_schema import build_contract_schema_result
from ocr_engine_9_query import build_query_extractor_result


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


class ContractSchemaExtractorTests(unittest.TestCase):
    def test_extracts_contract_eight_field_schema(self) -> None:
        ocr_result = {
            "source_file": "service_contract.pdf",
            "pages": [
                {
                    "page_num": 1,
                    "lines": [
                        make_line(text="Consulting Service Agreement", left=80, top=60, right=420, bottom=96, line_num=1),
                        make_line(text="Contract No: CTA-2026-001", left=80, top=120, right=420, bottom=156, line_num=2),
                        make_line(text="Party A: Acme Corporation", left=80, top=180, right=420, bottom=216, line_num=3),
                        make_line(text="Party B: Bright Future LLC", left=80, top=225, right=440, bottom=261, line_num=4),
                        make_line(text="Signing Date: 2026/04/20", left=80, top=270, right=420, bottom=306, line_num=5),
                        make_line(text="Effective Date: 2026/05/01", left=80, top=315, right=440, bottom=351, line_num=6),
                        make_line(text="End Date: 2027/04/30", left=80, top=360, right=420, bottom=396, line_num=7),
                        make_line(text="Total Contract Value: $120,000.00", left=80, top=405, right=520, bottom=441, line_num=8),
                    ],
                }
            ],
            "tables": [],
        }

        query_result = build_query_extractor_result(ocr_result)
        contract_result = build_contract_schema_result(ocr_result, query_result=query_result)
        normalized = contract_result["normalized_contract"]

        self.assertEqual(contract_result["status"], "ok")
        self.assertEqual(normalized["contract_title"], "Consulting Service Agreement")
        self.assertEqual(normalized["contract_number"], "CTA-2026-001")
        self.assertEqual(normalized["party_a"], "Acme Corporation")
        self.assertEqual(normalized["party_b"], "Bright Future LLC")
        self.assertEqual(normalized["signing_date"], "2026-04-20")
        self.assertEqual(normalized["effective_date"], "2026-05-01")
        self.assertEqual(normalized["end_date"], "2027-04-30")
        self.assertEqual(normalized["total_amount"], "$120,000.00")
        self.assertEqual(contract_result["analysis"]["field_count"], 8)

    def test_run_ocr_pipeline_writes_contract_schema_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "contract.pdf"
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
                    make_line(text="Consulting Service Agreement", left=80, top=60, right=420, bottom=96, line_num=1),
                    make_line(text="Contract No: CTA-2026-001", left=80, top=120, right=420, bottom=156, line_num=2),
                    make_line(text="Party A: Acme Corporation", left=80, top=180, right=420, bottom=216, line_num=3),
                    make_line(text="Party B: Bright Future LLC", left=80, top=225, right=440, bottom=261, line_num=4),
                    make_line(text="Signing Date: 2026/04/20", left=80, top=270, right=420, bottom=306, line_num=5),
                    make_line(text="Effective Date: 2026/05/01", left=80, top=315, right=440, bottom=351, line_num=6),
                    make_line(text="End Date: 2027/04/30", left=80, top=360, right=420, bottom=396, line_num=7),
                    make_line(text="Total Contract Value: $120,000.00", left=80, top=405, right=520, bottom=441, line_num=8),
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

            contract_schema_json_path = output_dir / "contract_schema.json"
            self.assertTrue(contract_schema_json_path.exists())

            payload = json.loads(contract_schema_json_path.read_text(encoding="utf-8"))
            normalized = payload["normalized_contract"]
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(normalized["contract_number"], "CTA-2026-001")
            self.assertEqual(normalized["effective_date"], "2026-05-01")
            self.assertEqual(normalized["total_amount"], "$120,000.00")
            self.assertEqual(result["ocr_result"]["contract_schema_result"]["analysis"]["field_count"], 8)


if __name__ == "__main__":
    unittest.main()
