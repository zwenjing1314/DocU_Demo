from __future__ import annotations

import unittest

import ocr_engine


def make_line(
    *,
    page_num: int,
    text: str,
    left: int,
    top: int,
    right: int,
    bottom: int,
    block_num: int,
    par_num: int,
    line_num: int,
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


class LayoutMarkdownTests(unittest.TestCase):
    def test_layout_reader_builds_hierarchical_markdown_and_filters_header_footer(self) -> None:
        pages = [
            {
                "page_num": 1,
                "image_width": 1000,
                "image_height": 1400,
                "lines": [
                    make_line(
                        page_num=1,
                        text="Acme Corp Confidential",
                        left=280,
                        top=20,
                        right=720,
                        bottom=48,
                        block_num=1,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="Quarterly Report",
                        left=250,
                        top=90,
                        right=750,
                        bottom=160,
                        block_num=2,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="1. Overview",
                        left=110,
                        top=240,
                        right=470,
                        bottom=290,
                        block_num=3,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="This report summarizes the quarter performance",
                        left=110,
                        top=340,
                        right=760,
                        bottom=378,
                        block_num=4,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="and highlights the main changes.",
                        left=110,
                        top=384,
                        right=620,
                        bottom=422,
                        block_num=4,
                        par_num=1,
                        line_num=2,
                    ),
                    make_line(
                        page_num=1,
                        text="• First bullet item",
                        left=140,
                        top=470,
                        right=450,
                        bottom=505,
                        block_num=5,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="• Second bullet item",
                        left=140,
                        top=515,
                        right=470,
                        bottom=550,
                        block_num=5,
                        par_num=2,
                        line_num=2,
                    ),
                    make_line(
                        page_num=1,
                        text="Page 1",
                        left=445,
                        top=1330,
                        right=555,
                        bottom=1360,
                        block_num=6,
                        par_num=1,
                        line_num=1,
                    ),
                ],
            },
            {
                "page_num": 2,
                "image_width": 1000,
                "image_height": 1400,
                "lines": [
                    make_line(
                        page_num=2,
                        text="Acme Corp Confidential",
                        left=280,
                        top=22,
                        right=720,
                        bottom=50,
                        block_num=1,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=2,
                        text="2. Details",
                        left=110,
                        top=210,
                        right=390,
                        bottom=258,
                        block_num=2,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=2,
                        text="The second page keeps the body reading order",
                        left=110,
                        top=300,
                        right=760,
                        bottom=338,
                        block_num=3,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=2,
                        text="without pulling headers or footers into the text.",
                        left=110,
                        top=344,
                        right=800,
                        bottom=382,
                        block_num=3,
                        par_num=1,
                        line_num=2,
                    ),
                    make_line(
                        page_num=2,
                        text="Page 2",
                        left=445,
                        top=1330,
                        right=555,
                        bottom=1360,
                        block_num=4,
                        par_num=1,
                        line_num=1,
                    ),
                ],
            },
        ]

        layout = ocr_engine._analyze_document_layout(pages)
        document_markdown = ocr_engine._build_document_markdown("quarterly_report.pdf", layout["pages"])

        self.assertIn("# Quarterly Report", document_markdown)
        self.assertIn("## 1. Overview", document_markdown)
        self.assertIn("## 2. Details", document_markdown)
        self.assertIn(
            "This report summarizes the quarter performance and highlights the main changes.",
            document_markdown,
        )
        self.assertIn("- First bullet item", document_markdown)
        self.assertIn("- Second bullet item", document_markdown)
        self.assertNotIn("Acme Corp Confidential", document_markdown)
        self.assertNotIn("Page 1", document_markdown)
        self.assertNotIn("Page 2", document_markdown)
        self.assertEqual(layout["pages"][0]["stats"]["filtered_margin_line_count"], 2)
        self.assertEqual(layout["pages"][1]["stats"]["filtered_margin_line_count"], 2)

    def test_layout_reader_preserves_basic_two_column_reading_order(self) -> None:
        pages = [
            {
                "page_num": 1,
                "image_width": 1000,
                "image_height": 1400,
                "lines": [
                    make_line(
                        page_num=1,
                        text="Dual Column Note",
                        left=260,
                        top=90,
                        right=740,
                        bottom=150,
                        block_num=1,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="Left column first.",
                        left=90,
                        top=230,
                        right=410,
                        bottom=266,
                        block_num=2,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="Left column continues.",
                        left=90,
                        top=272,
                        right=430,
                        bottom=308,
                        block_num=2,
                        par_num=1,
                        line_num=2,
                    ),
                    make_line(
                        page_num=1,
                        text="Right column second.",
                        left=580,
                        top=235,
                        right=900,
                        bottom=271,
                        block_num=3,
                        par_num=1,
                        line_num=1,
                    ),
                    make_line(
                        page_num=1,
                        text="Right column continues.",
                        left=580,
                        top=277,
                        right=920,
                        bottom=313,
                        block_num=3,
                        par_num=1,
                        line_num=2,
                    ),
                ],
            }
        ]

        layout = ocr_engine._analyze_document_layout(pages)
        document_markdown = ocr_engine._build_document_markdown("dual_column.pdf", layout["pages"])

        self.assertEqual(layout["pages"][0]["stats"]["column_count"], 2)
        self.assertLess(
            document_markdown.index("Left column first. Left column continues."),
            document_markdown.index("Right column second. Right column continues."),
        )


if __name__ == "__main__":
    unittest.main()
