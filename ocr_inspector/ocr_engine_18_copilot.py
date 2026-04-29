from __future__ import annotations

"""End-to-end Document AI Copilot helpers.

第 18 步不是新增一个单点识别算法，而是把 1-17 的结果串成一条能演示的产品链路：
分类 -> 切分 -> OCR/layout/extraction -> chunking -> QA/摘要 -> 人工复核 -> JSON/Markdown 导出。

这个模块只做“编排和汇总”，不重复实现 OCR 或抽取逻辑，避免继续膨胀 ocr_engine.py。
"""

from pathlib import Path
import json
import re
from typing import Any

COPILOT_SCHEMA_VERSION = "1.0"
PIPELINE_STAGE_ORDER = (
    "classification",
    "splitting",
    "ocr_layout_extraction",
    "chunking",
    "qa_summary",
    "human_review",
    "robustness",
    "exports",
)
JSON_EXPORTS = (
    "ocr.json",
    "document_router.json",
    "bundle_splitter.json",
    "form.json",
    "receipt_invoice.json",
    "contract_schema.json",
    "multi_page_consolidation.json",
    "layout_chunks.json",
    "direct_pdf_structure.json",
    "evidence_qa.json",
    "complex_page_analysis.json",
    "degradation_report.json",
    "signature_handwriting_review.json",
    "review_workbench_revisions.json",
    "document_ai_copilot.json",
)
MARKDOWN_EXPORTS = (
    "document.md",
    "document_ai_copilot.md",
)


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_text(text: Any, max_chars: int = 480) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _artifact(name: str, kind: str, ready: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "ready": ready,
    }


def _status(done: bool, *, needs_review: bool = False, empty: bool = False) -> str:
    if empty:
        return "empty"
    if needs_review:
        return "needs_review"
    return "completed" if done else "pending"


def _count_low_confidence_words(ocr_result: dict[str, Any], threshold: float = 85.0) -> int:
    return sum(
        1
        for page in ocr_result.get("pages", [])
        for word in page.get("words", [])
        if 0 <= _safe_float(word.get("confidence"), -1.0) < threshold
    )


def _word_count(ocr_result: dict[str, Any]) -> int:
    return sum(len(page.get("words", [])) for page in ocr_result.get("pages", []))


def _line_count(ocr_result: dict[str, Any]) -> int:
    return sum(len(page.get("lines", [])) for page in ocr_result.get("pages", []))


def _extract_summary(ocr_result: dict[str, Any]) -> str:
    strict_summary = (
        ocr_result
        .get("direct_pdf_structure_result", {})
        .get("strict_schema", {})
        .get("summary", {})
        .get("short", "")
    )
    if strict_summary:
        return _compact_text(strict_summary)

    first_lines: list[str] = []
    for page in ocr_result.get("pages", [])[:3]:
        for line in page.get("lines", [])[:6]:
            text = _normalize_text(line.get("text", ""))
            if text:
                first_lines.append(text)
    return _compact_text(" ".join(first_lines), max_chars=520)


def _non_empty_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in ("", None, [], {})
    }


def _build_key_facts(ocr_result: dict[str, Any]) -> dict[str, Any]:
    form = _non_empty_fields(ocr_result.get("form_result", {}).get("normalized_form", {}))
    receipt = _non_empty_fields(ocr_result.get("receipt_invoice_result", {}).get("normalized_receipt", {}))
    contract = _non_empty_fields(ocr_result.get("contract_schema_result", {}).get("normalized_contract", {}))
    consolidation = ocr_result.get("multi_page_consolidation_result", {}).get("consolidated", {})

    return {
        "form": form,
        "receipt_invoice": {
            key: value
            for key, value in receipt.items()
            if key != "items"
        },
        "receipt_item_count": len(receipt.get("items", [])) if isinstance(receipt.get("items"), list) else 0,
        "contract": contract,
        "consolidation": {
            "document_kind": ocr_result.get("multi_page_consolidation_result", {}).get("document_kind", ""),
            "total_check_status": ocr_result.get("multi_page_consolidation_result", {}).get("analysis", {}).get("total_check_status", "not_available"),
            "balance_check_status": ocr_result.get("multi_page_consolidation_result", {}).get("analysis", {}).get("balance_check_status", "not_available"),
            "available_sections": sorted(consolidation.keys()),
        },
    }


def _suggest_questions(ocr_result: dict[str, Any]) -> list[str]:
    questions = [
        "这份文档属于什么类型？",
        "有哪些页面或子文档需要人工复核？",
        "这份文档的摘要是什么？",
    ]
    receipt = ocr_result.get("receipt_invoice_result", {}).get("normalized_receipt", {})
    contract = ocr_result.get("contract_schema_result", {}).get("normalized_contract", {})
    if receipt.get("total") is not None:
        questions.append("总金额是多少？")
    if receipt.get("tax") is not None:
        questions.append("税额是多少？")
    if contract.get("effective_date") or contract.get("end_date"):
        questions.append("合同起止日期是哪天？")
    if ocr_result.get("bundle_splitter_result", {}).get("analysis", {}).get("detected_bundle"):
        questions.append("这个混合包被切成了哪些子文档？")
    if ocr_result.get("robustness_lab_result", {}).get("analysis", {}).get("most_fragile_layer"):
        questions.append("这个文档链路最脆弱的是哪一层？")
    return questions[:8]


def _router_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    router = ocr_result.get("document_router_result", {})
    analysis = router.get("analysis", {})
    return {
        "stage_id": "classification",
        "name": "Document classification and routing",
        "status": _status(bool(router.get("label"))),
        "inherits": ["1", "2", "6"],
        "inputs": ["ocr pages", "layout items", "tables"],
        "outputs": [_artifact("document_router.json", "json")],
        "metrics": {
            "label": router.get("label", ocr_result.get("document_label", "unknown")),
            "confidence": analysis.get("confidence", 0.0),
            "matched_signal_count": len(router.get("matched_signals", [])),
            "dispatch_chain": router.get("dispatch", {}).get("stages", []),
        },
        "handoff": "Route label decides whether form, receipt/invoice, report, or ID extractors should be trusted first.",
    }


def _split_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    bundle = ocr_result.get("bundle_splitter_result", {})
    analysis = bundle.get("analysis", {})
    segments = bundle.get("segments", [])
    return {
        "stage_id": "splitting",
        "name": "Bundle splitting and page ranges",
        "status": _status(bool(bundle), empty=not segments),
        "inherits": ["6", "7"],
        "inputs": ["page-level classifications"],
        "outputs": [_artifact("bundle_splitter.json", "json"), _artifact("bundle_segments/", "directory", ready=bool(segments))],
        "metrics": {
            "detected_bundle": bool(analysis.get("detected_bundle", False)),
            "segment_count": _safe_int(analysis.get("segment_count"), len(segments)),
            "segments": [
                {
                    "segment_id": segment.get("segment_id", ""),
                    "label": segment.get("label", ""),
                    "start_page": segment.get("start_page"),
                    "end_page": segment.get("end_page"),
                    "confidence": segment.get("confidence", 0.0),
                }
                for segment in segments
            ],
        },
        "handoff": "Each segment keeps page ranges so later extractors can explain which subdocument produced an answer.",
    }


def _ocr_layout_extraction_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    review_analysis = ocr_result.get("signature_handwriting_review_result", {}).get("analysis", {})
    return {
        "stage_id": "ocr_layout_extraction",
        "name": "OCR, layout, tables, and business extraction",
        "status": _status(bool(ocr_result.get("pages"))),
        "inherits": ["1", "2", "3", "4", "5", "8", "10", "11"],
        "inputs": ["page images", "OCR words/lines", "tables"],
        "outputs": [
            _artifact("ocr.json", "json"),
            _artifact("document.md", "markdown"),
            _artifact("tables/index.html", "html", ready=bool(ocr_result.get("tables"))),
            _artifact("form.json", "json"),
            _artifact("receipt_invoice.json", "json"),
            _artifact("contract_schema.json", "json"),
            _artifact("multi_page_consolidation.json", "json"),
            _artifact("signature_handwriting_review.json", "json"),
        ],
        "metrics": {
            "page_count": _safe_int(ocr_result.get("page_count"), len(ocr_result.get("pages", []))),
            "word_count": _word_count(ocr_result),
            "line_count": _line_count(ocr_result),
            "low_confidence_word_count": _count_low_confidence_words(ocr_result),
            "table_count": len(ocr_result.get("tables", [])),
            "form_field_count": ocr_result.get("form_analysis", {}).get("field_count", 0),
            "receipt_line_item_count": ocr_result.get("receipt_invoice_analysis", {}).get("line_item_count", 0),
            "contract_field_count": ocr_result.get("contract_schema_result", {}).get("analysis", {}).get("field_count", 0),
            "suspicious_field_count": review_analysis.get("suspicious_field_count", 0),
        },
        "handoff": "Structured extraction becomes the source for review, QA evidence, and final JSON export.",
    }


def _chunking_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    chunk_analysis = ocr_result.get("layout_chunk_result", {}).get("analysis", {})
    return {
        "stage_id": "chunking",
        "name": "Layout-aware RAG chunking",
        "status": _status(_safe_int(chunk_analysis.get("chunk_count")) > 0),
        "inherits": ["13"],
        "inputs": ["layout items", "tables", "heading context"],
        "outputs": [_artifact("layout_chunks.json", "json")],
        "metrics": {
            "chunk_count": chunk_analysis.get("chunk_count", 0),
            "table_chunk_count": chunk_analysis.get("table_chunk_count", 0),
            "heading_context_chunk_count": chunk_analysis.get("heading_context_chunk_count", 0),
        },
        "handoff": "Chunks preserve headings, table headers, and page numbers for traceable QA and downstream RAG.",
    }


def _qa_summary_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    evidence_analysis = ocr_result.get("evidence_qa_result", {}).get("analysis", {})
    direct_analysis = ocr_result.get("direct_pdf_structure_result", {}).get("analysis", {})
    chart_analysis = ocr_result.get("complex_page_analysis_result", {}).get("analysis", {})
    return {
        "stage_id": "qa_summary",
        "name": "Summary and evidence-grounded QA",
        "status": _status(bool(_extract_summary(ocr_result)) or _safe_int(evidence_analysis.get("unit_count")) > 0),
        "inherits": ["9", "14", "15", "16"],
        "inputs": ["layout chunks", "direct PDF strict schema", "chart candidates"],
        "outputs": [
            _artifact("direct_pdf_structure.json", "json"),
            _artifact("evidence_qa.json", "json"),
            _artifact("complex_page_analysis.json", "json"),
        ],
        "metrics": {
            "summary_available": bool(_extract_summary(ocr_result)),
            "native_text_page_count": direct_analysis.get("native_text_page_count", 0),
            "evidence_unit_count": evidence_analysis.get("unit_count", 0),
            "chart_candidate_count": chart_analysis.get("chart_candidate_count", 0),
            "suggested_questions": _suggest_questions(ocr_result),
        },
        "handoff": "Answers should point back to evidence pages/chunks instead of becoming untraceable prose.",
    }


def _human_review_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    review = ocr_result.get("signature_handwriting_review_result", {})
    analysis = review.get("analysis", {})
    queue_count = (
        _safe_int(analysis.get("signature_region_count"))
        + _safe_int(analysis.get("handwriting_region_count"))
        + _safe_int(analysis.get("suspicious_field_count"))
        + _count_low_confidence_words(ocr_result)
    )
    return {
        "stage_id": "human_review",
        "name": "Human review workbench",
        "status": _status(bool(review), needs_review=queue_count > 0),
        "inherits": ["8", "12"],
        "inputs": ["review overlays", "low confidence words", "extracted fields"],
        "outputs": [
            _artifact("Review Workbench", "ui"),
            _artifact("review_workbench_revisions.json", "json"),
            _artifact("review_overlays/", "directory", ready=bool(review.get("pages"))),
        ],
        "metrics": {
            "review_queue_estimate": queue_count,
            "review_page_count": analysis.get("review_page_count", 0),
            "signature_region_count": analysis.get("signature_region_count", 0),
            "handwriting_region_count": analysis.get("handwriting_region_count", 0),
            "suspicious_field_count": analysis.get("suspicious_field_count", 0),
            "low_confidence_word_count": _count_low_confidence_words(ocr_result),
        },
        "handoff": "This stage answers the product question: what happens after the model is wrong or uncertain?",
    }


def _robustness_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    robustness = ocr_result.get("robustness_lab_result", {})
    analysis = robustness.get("analysis", {})
    return {
        "stage_id": "robustness",
        "name": "Robustness diagnostics",
        "status": _status(bool(robustness)),
        "inherits": ["17"],
        "inputs": ["page images", "pipeline baseline metrics"],
        "outputs": [
            _artifact("degradation_report.json", "json"),
            _artifact("robustness_lab/", "directory", ready=_safe_int(analysis.get("generated_page_count")) > 0),
        ],
        "metrics": {
            "variant_count": analysis.get("variant_count", 0),
            "generated_page_count": analysis.get("generated_page_count", 0),
            "most_fragile_layer": analysis.get("most_fragile_layer", "unknown"),
            "evaluation_mode": analysis.get("evaluation_mode", "visual_proxy"),
        },
        "handoff": "Robustness diagnostics explain whether failures are likely OCR, layout, extraction, or reasoning problems.",
    }


def _exports_stage(ocr_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage_id": "exports",
        "name": "Unified JSON and Markdown export",
        "status": "completed",
        "inherits": ["all"],
        "inputs": ["all previous stage results"],
        "outputs": [
            *(_artifact(name, "json") for name in JSON_EXPORTS),
            *(_artifact(name, "markdown") for name in MARKDOWN_EXPORTS),
        ],
        "metrics": {
            "json_export_count": len(JSON_EXPORTS),
            "markdown_export_count": len(MARKDOWN_EXPORTS),
            "machine_consumable": True,
        },
        "handoff": "The demo now has a product-level handoff bundle, not only isolated intermediate files.",
    }


def _build_stages(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    stage_builders = {
        "classification": _router_stage,
        "splitting": _split_stage,
        "ocr_layout_extraction": _ocr_layout_extraction_stage,
        "chunking": _chunking_stage,
        "qa_summary": _qa_summary_stage,
        "human_review": _human_review_stage,
        "robustness": _robustness_stage,
        "exports": _exports_stage,
    }
    return [stage_builders[stage_id](ocr_result) for stage_id in PIPELINE_STAGE_ORDER]


def _readiness(stages: list[dict[str, Any]]) -> dict[str, Any]:
    completed = sum(1 for stage in stages if stage["status"] in {"completed", "needs_review"})
    needs_review = [stage["stage_id"] for stage in stages if stage["status"] == "needs_review"]
    pending = [stage["stage_id"] for stage in stages if stage["status"] == "pending"]
    score = round(completed / max(1, len(stages)), 3)
    if pending:
        status = "partial"
    elif needs_review:
        status = "ready_with_review"
    else:
        status = "ready"
    return {
        "status": status,
        "score": score,
        "completed_stage_count": completed,
        "stage_count": len(stages),
        "needs_review_stages": needs_review,
        "pending_stages": pending,
        "demo_ready": not pending,
    }


def build_document_ai_copilot_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """生成端到端 Copilot 总结果，统一描述整条产品演示链路。"""
    stages = _build_stages(ocr_result)
    readiness = _readiness(stages)
    summary = _extract_summary(ocr_result)
    key_facts = _build_key_facts(ocr_result)
    router = ocr_result.get("document_router_result", {})
    bundle_analysis = ocr_result.get("bundle_splitter_result", {}).get("analysis", {})

    return {
        "schema_version": COPILOT_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "status": readiness["status"],
        "product_goal": "one connected demo pipeline for mixed document understanding",
        "document_package": {
            "page_count": _safe_int(ocr_result.get("page_count"), len(ocr_result.get("pages", []))),
            "document_label": router.get("label", ocr_result.get("document_label", "unknown")),
            "router_confidence": router.get("analysis", {}).get("confidence", 0.0),
            "detected_bundle": bool(bundle_analysis.get("detected_bundle", False)),
            "segment_count": bundle_analysis.get("segment_count", 0),
        },
        "executive_summary": summary,
        "key_facts": key_facts,
        "pipeline": {
            "stage_order": list(PIPELINE_STAGE_ORDER),
            "stages": stages,
        },
        "qa": {
            "suggested_questions": _suggest_questions(ocr_result),
            "evidence_unit_count": ocr_result.get("evidence_qa_result", {}).get("analysis", {}).get("unit_count", 0),
            "query_endpoint": "/evidence-qa",
            "chart_qa_endpoint": "/complex-chart-qa",
        },
        "human_review": {
            "review_workbench": "Review Workbench",
            "revision_file": "review_workbench_revisions.json",
            "queue_stage_status": next(stage for stage in stages if stage["stage_id"] == "human_review")["status"],
        },
        "exports": {
            "json": list(JSON_EXPORTS),
            "markdown": list(MARKDOWN_EXPORTS),
            "directories": ["pages/", "overlays/", "tables/", "bundle_segments/", "review_overlays/", "robustness_lab/"],
        },
        "readiness": readiness,
        "demo_script": [
            "Upload a mixed PDF or image package.",
            "Show document_router.json for classification and dispatch.",
            "Open bundle_splitter.json to verify page ranges.",
            "Inspect OCR overlays, tables, business JSON, and layout chunks.",
            "Ask an evidence-grounded question and verify returned pages/chunks.",
            "Open Review Workbench for uncertain fields and save a revision batch.",
            "Export document_ai_copilot.json and document_ai_copilot.md as the final handoff.",
        ],
    }


def build_document_ai_copilot_markdown(copilot_result: dict[str, Any]) -> str:
    """把 Copilot JSON 转成可读 Markdown，方便直接演示或提交作业。"""
    package = copilot_result.get("document_package", {})
    readiness = copilot_result.get("readiness", {})
    lines = [
        "# End-to-End Document AI Copilot",
        "",
        f"- Source: `{copilot_result.get('source_file', '')}`",
        f"- Type: `{package.get('document_label', 'unknown')}`",
        f"- Pages: `{package.get('page_count', 0)}`",
        f"- Bundle detected: `{package.get('detected_bundle', False)}`",
        f"- Readiness: `{readiness.get('status', 'unknown')}` ({readiness.get('score', 0)})",
        "",
        "## Executive Summary",
        "",
        copilot_result.get("executive_summary", "") or "No summary text was available.",
        "",
        "## Pipeline Stages",
        "",
    ]

    for stage in copilot_result.get("pipeline", {}).get("stages", []):
        metrics = stage.get("metrics", {})
        metric_parts = [
            f"{key}={value}"
            for key, value in metrics.items()
            if key not in {"segments", "suggested_questions", "dispatch_chain"} and value not in ("", [], {})
        ][:6]
        metric_text = "; ".join(metric_parts) if metric_parts else "no headline metrics"
        lines.extend(
            [
                f"### {stage.get('name', stage.get('stage_id', 'stage'))}",
                "",
                f"- Status: `{stage.get('status', '')}`",
                f"- Inherits: `{', '.join(stage.get('inherits', []))}`",
                f"- Metrics: {metric_text}",
                f"- Handoff: {stage.get('handoff', '')}",
                "",
            ]
        )

    questions = copilot_result.get("qa", {}).get("suggested_questions", [])
    lines.extend(["## Suggested Questions", ""])
    if questions:
        lines.extend(f"- {question}" for question in questions)
    else:
        lines.append("- No suggested questions.")

    lines.extend(["", "## Exports", ""])
    for name in copilot_result.get("exports", {}).get("json", []):
        lines.append(f"- JSON: `{name}`")
    for name in copilot_result.get("exports", {}).get("markdown", []):
        lines.append(f"- Markdown: `{name}`")
    lines.append("")
    return "\n".join(lines)


def write_document_ai_copilot_json(copilot_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(copilot_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_document_ai_copilot_markdown(copilot_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        build_document_ai_copilot_markdown(copilot_result),
        encoding="utf-8",
    )
