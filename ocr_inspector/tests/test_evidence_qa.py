from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from ocr_engine_15_evidence_qa import (
    answer_evidence_question,
    build_evidence_qa_result,
    write_evidence_qa_json,
)


def make_ocr_result() -> dict:
    return {
        "source_file": "long_contract.pdf",
        "source_kind": "pdf",
        "page_count": 32,
        "layout_chunk_result": {
            "chunks": [
                {
                    "chunk_id": "chunk_0001_text",
                    "type": "text",
                    "text": "Contract > Payment Terms\n\nTotal Contract Value: $120,000.00 payable in four installments.",
                    "title_context": "Contract > Payment Terms",
                    "page_nums": [18],
                    "page_range": {"start_page": 18, "end_page": 18},
                    "source_refs": [{"kind": "layout_item", "page_num": 18}],
                },
                {
                    "chunk_id": "chunk_0002_text",
                    "type": "text",
                    "text": "Contract > Term\n\nThe effective date is 2026/05/01 and the agreement ends on 2027/04/30.",
                    "title_context": "Contract > Term",
                    "page_nums": [6, 7],
                    "page_range": {"start_page": 6, "end_page": 7},
                    "source_refs": [{"kind": "layout_item", "page_num": 6}],
                },
            ],
            "analysis": {"chunk_count": 2},
        },
        "direct_pdf_structure_result": {
            "strict_schema": {
                "summary": {
                    "short": "This contract covers payment terms and service duration.",
                    "page_count": 32,
                    "detected_topics": ["Payment Terms", "Term"],
                },
                "outline_tree": [
                    {"title": "Payment Terms", "page_num": 18, "level": 1, "children": []},
                    {"title": "Term", "page_num": 6, "level": 1, "children": []},
                ],
                "fixed_json": {
                    "document_type": "contract",
                    "contract": {
                        "total_amount": "$120,000.00",
                        "effective_date": "2026-05-01",
                    },
                },
                "rag_context": {
                    "chunk_count": 2,
                    "table_chunk_count": 0,
                    "referenced_chunk_ids": ["chunk_0001_text", "chunk_0002_text"],
                },
            },
        },
    }


class EvidenceQaTests(unittest.TestCase):
    def test_answers_with_evidence_pages_and_chunks(self) -> None:
        qa_result = build_evidence_qa_result(make_ocr_result())
        answer = answer_evidence_question(qa_result, "总金额是多少")

        self.assertEqual(answer["status"], "ok")
        self.assertEqual(answer["answer"], "$120,000.00")
        self.assertIn(18, answer["evidence_pages"])
        self.assertGreaterEqual(len(answer["evidence_chunks"]), 1)
        self.assertEqual(answer["evidence_chunks"][0]["source_ref"]["chunk_id"], "chunk_0001_text")
        self.assertEqual(len(qa_result["query_history"]), 1)

    def test_returns_insufficient_evidence_when_no_chunk_matches(self) -> None:
        qa_result = build_evidence_qa_result(make_ocr_result())
        answer = answer_evidence_question(qa_result, "董事会成员的宠物名字是什么")

        self.assertEqual(answer["status"], "insufficient_evidence")
        self.assertEqual(answer["evidence_pages"], [])
        self.assertEqual(answer["evidence_chunks"], [])

    def test_evidence_qa_api_saves_query_history(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            outputs_root = Path(tempdir)
            job_id = "abcdef123456"
            output_dir = outputs_root / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            qa_result = build_evidence_qa_result(make_ocr_result())
            write_evidence_qa_json(qa_result, output_dir / "evidence_qa.json")

            client = TestClient(app_module.app)
            with patch.object(app_module, "OUTPUTS_DIR", outputs_root):
                response = client.post(
                    "/evidence-qa",
                    data={
                        "job_id": job_id,
                        "query": "effective date",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["result"]["status"], "ok")
            self.assertIn(6, payload["result"]["evidence_pages"])

            saved = json.loads((output_dir / "evidence_qa.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["query_history"]), 1)


if __name__ == "__main__":
    unittest.main()
