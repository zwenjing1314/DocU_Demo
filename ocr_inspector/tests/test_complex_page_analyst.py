from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from ocr_engine_16_complex_page_analyst import (
    answer_chart_question,
    build_complex_page_analysis_result,
    write_complex_page_analysis_json,
)


def make_chart_ocr_result() -> dict:
    return {
        "source_file": "chart_report.pdf",
        "source_kind": "pdf",
        "page_count": 5,
        "tables": [
            {
                "table_id": "page_003_table_01",
                "page_num": 3,
                "csv_path": "page_003_table_01.csv",
                "html_path": "page_003_table_01.html",
                "row_count": 4,
                "col_count": 3,
                "rows": [
                    ["Region", "Revenue", "Growth"],
                    ["North", "$120", "12%"],
                    ["South", "$95", "8%"],
                    ["West", "$140", "15%"],
                ],
            }
        ],
        "layout_chunk_result": {
            "chunks": [
                {
                    "chunk_id": "chunk_0003_table",
                    "type": "table",
                    "title_context": "Revenue Chart",
                    "text": "Revenue Chart\n\n| Region | Revenue | Growth |",
                    "page_nums": [3],
                    "source_refs": [{"kind": "table", "table_id": "page_003_table_01"}],
                }
            ]
        },
    }


class ComplexPageAnalystTests(unittest.TestCase):
    def test_detects_chart_candidate_from_table_and_layout_context(self) -> None:
        result = build_complex_page_analysis_result(make_chart_ocr_result())

        self.assertEqual(result["selected_domain"], "chart_qa")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["analysis"]["chart_candidate_count"], 1)
        candidate = result["chart_candidates"][0]
        self.assertEqual(candidate["page_num"], 3)
        self.assertEqual(candidate["series"][0]["summary"]["max"]["label"], "West")

    def test_answers_chart_question_with_evidence_and_error_explanations(self) -> None:
        result = build_complex_page_analysis_result(make_chart_ocr_result())
        answer = answer_chart_question(result, "Which region has the highest revenue?")

        self.assertEqual(answer["status"], "ok")
        self.assertIn("West", answer["answer"])
        self.assertIn(3, answer["evidence_pages"])
        self.assertEqual(answer["evidence_items"][0]["table_id"], "page_003_table_01")
        self.assertTrue(answer["error_explanations"])
        self.assertEqual(len(result["query_history"]), 1)

    def test_returns_insufficient_evidence_without_chart_candidate(self) -> None:
        result = build_complex_page_analysis_result(
            {
                "source_file": "plain.pdf",
                "source_kind": "pdf",
                "page_count": 1,
                "tables": [],
                "layout_chunk_result": {"chunks": []},
            }
        )
        answer = answer_chart_question(result, "Which bar is highest?")

        self.assertEqual(answer["status"], "insufficient_evidence")
        self.assertEqual(answer["evidence_pages"], [])
        self.assertTrue(answer["error_explanations"])

    def test_complex_chart_qa_api_saves_query_history(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            outputs_root = Path(tempdir)
            job_id = "abcdef123456"
            output_dir = outputs_root / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_result = build_complex_page_analysis_result(make_chart_ocr_result())
            write_complex_page_analysis_json(analysis_result, output_dir / "complex_page_analysis.json")

            client = TestClient(app_module.app)
            with patch.object(app_module, "OUTPUTS_DIR", outputs_root):
                response = client.post(
                    "/complex-chart-qa",
                    data={
                        "job_id": job_id,
                        "query": "total revenue",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["result"]["status"], "ok")
            self.assertIn("total", payload["result"]["answer"].lower())

            saved = json.loads((output_dir / "complex_page_analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["query_history"]), 1)


if __name__ == "__main__":
    unittest.main()
