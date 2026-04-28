from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pymupdf

import ocr_engine


def create_simple_table_pdf(pdf_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    x0, y0, cell_w, cell_h = 72, 100, 120, 40

    for row_index in range(4):
        y = y0 + row_index * cell_h
        page.draw_line((x0, y), (x0 + 3 * cell_w, y))

    for col_index in range(4):
        x = x0 + col_index * cell_w
        page.draw_line((x, y0), (x, y0 + 3 * cell_h))

    cells = [
        ["Name", "Qty", "Price"],
        ["Apple", "3", "$5"],
        ["Banana", "8", "$9"],
    ]
    for row_index, row in enumerate(cells):
        for col_index, text in enumerate(row):
            page.insert_text(
                (x0 + col_index * cell_w + 8, y0 + row_index * cell_h + 24),
                text,
                fontsize=12,
            )

    doc.save(str(pdf_path))
    doc.close()


def make_word(
        *,
        text: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        line_num: int,
        word_num: int,
) -> dict:
    return {
        "page_num": 1,
        "text": text,
        "confidence": 99.0,
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
        "word_num": word_num,
        "source": "primary",
        "angle": 0.0,
    }


class TableExportTests(unittest.TestCase):
    def test_pdf_native_table_extraction_detects_simple_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "table.pdf"
            create_simple_table_pdf(pdf_path)

            doc = pymupdf.open(pdf_path)
            try:
                tables = ocr_engine._extract_tables_from_pdf_page(doc[0], 1)
            finally:
                doc.close()

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["source"], "pdf_text")
        self.assertEqual(tables[0]["rows"][0], ["Name", "Qty", "Price"])
        self.assertEqual(tables[0]["rows"][2], ["Banana", "8", "$9"])

    def test_ocr_fallback_table_detection_restores_simple_rows_and_columns(self) -> None:
        page = {
            "page_num": 1,
            "image_width": 1000,
            "image_height": 1400,
            "words": [
                make_word(text="Name", left=100, top=100, right=180, bottom=130, line_num=1, word_num=1),
                make_word(text="Qty", left=340, top=100, right=390, bottom=130, line_num=1, word_num=2),
                make_word(text="Price", left=560, top=100, right=640, bottom=130, line_num=1, word_num=3),
                make_word(text="Apple", left=100, top=160, right=170, bottom=190, line_num=2, word_num=1),
                make_word(text="3", left=350, top=160, right=360, bottom=190, line_num=2, word_num=2),
                make_word(text="$5", left=570, top=160, right=600, bottom=190, line_num=2, word_num=3),
                make_word(text="Banana", left=100, top=220, right=190, bottom=250, line_num=3, word_num=1),
                make_word(text="8", left=350, top=220, right=360, bottom=250, line_num=3, word_num=2),
                make_word(text="$9", left=570, top=220, right=600, bottom=250, line_num=3, word_num=3),
            ],
        }

        tables = ocr_engine._detect_tables_from_ocr_page(page)

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["source"], "ocr_layout")
        self.assertEqual(tables[0]["rows"], [
            ["Name", "Qty", "Price"],
            ["Apple", "3", "$5"],
            ["Banana", "8", "$9"],
        ])

    def test_run_ocr_pipeline_writes_table_csv_and_html_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            pdf_path = Path(tempdir) / "table.pdf"
            output_dir = Path(tempdir) / "outputs"
            create_simple_table_pdf(pdf_path)

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

                return {
                    "page_num": page_num,
                    "image_width": rendered_page.width,
                    "image_height": rendered_page.height,
                    "words": [],
                    "rejected_words": [],
                    "lines": [],
                    "text": "",
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

            ocr_result = result["ocr_result"]
            self.assertEqual(ocr_result["table_analysis"]["table_count"], 1)
            self.assertEqual(ocr_result["table_analysis"]["pdf_text_table_count"], 1)
            self.assertEqual(ocr_result["table_analysis"]["ocr_layout_table_count"], 0)

            table = ocr_result["tables"][0]
            self.assertTrue((output_dir / "tables" / table["csv_path"]).exists())
            self.assertTrue((output_dir / "tables" / table["html_path"]).exists())
            self.assertTrue((output_dir / "tables" / "index.html").exists())
            self.assertEqual(ocr_result["pages"][0]["tables"][0]["table_id"], table["table_id"])
            self.assertEqual(ocr_result["pages"][0]["tables"][0]["source"], "pdf_text")


if __name__ == "__main__":
    unittest.main()
