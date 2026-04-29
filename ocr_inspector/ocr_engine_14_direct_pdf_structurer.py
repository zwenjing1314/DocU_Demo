from __future__ import annotations

"""Direct PDF structuring helpers.

第 14 步的目标是“直接 PDF 理解 + 严格 schema 输出”。
当前 demo 不调用外部多模态 API，而是先提供一个稳定的本地适配层：
1. 直接读取 PDF 的原生目录、元数据和每页文本片段；
2. 复用第 10 步合同 schema 和第 13 步 chunks 作为可选结构化上下文；
3. 输出固定 JSON schema，方便后续替换为真正的多模态模型调用。
"""

from pathlib import Path
import json
import re
from typing import Any

import pymupdf

DIRECT_PDF_SCHEMA_VERSION = "1.0"
SUMMARY_MAX_CHARS = 520
PAGE_SAMPLE_MAX_CHARS = 1600
HEADING_LINE_MAX_CHARS = 96
STRICT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "outline_tree", "fixed_json", "rag_context"],
    "properties": {
        "summary": {
            "type": "object",
            "required": ["short", "page_count", "detected_topics"],
        },
        "outline_tree": {"type": "array"},
        "fixed_json": {"type": "object"},
        "rag_context": {"type": "object"},
    },
}


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _compact_text(text: str, max_chars: int) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _empty_strict_payload(page_count: int = 0) -> dict[str, Any]:
    return {
        "summary": {
            "short": "",
            "page_count": page_count,
            "detected_topics": [],
        },
        "outline_tree": [],
        "fixed_json": {
            "document_type": "",
            "contract": {},
        },
        "rag_context": {
            "chunk_count": 0,
            "table_chunk_count": 0,
            "referenced_chunk_ids": [],
        },
    }


def _toc_to_tree(toc_rows: list[list[Any]]) -> list[dict[str, Any]]:
    root: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []

    for row in toc_rows:
        if len(row) < 3:
            continue
        level = max(1, int(row[0]))
        node = {
            "title": _normalize_text(row[1]),
            "page_num": int(row[2]) if row[2] else None,
            "level": level,
            "children": [],
        }
        while stack and int(stack[-1]["level"]) >= level:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            root.append(node)
        stack.append(node)

    return root


def _extract_heading_candidates(page_text: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in page_text.splitlines():
        line = _normalize_text(raw_line)
        if not line or len(line) > HEADING_LINE_MAX_CHARS:
            continue
        if re.match(r"^(\d+(?:\.\d+){0,3}|第[一二三四五六七八九十百千0-9]+[章节部分篇]|[A-Z][A-Za-z ]{3,})", line):
            candidates.append(line)
        if len(candidates) >= 5:
            break
    return candidates


def _infer_outline_from_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for page in pages:
        for title in page.get("heading_candidates", [])[:2]:
            outline.append(
                {
                    "title": title,
                    "page_num": page["page_num"],
                    "level": 1,
                    "children": [],
                    "source": "native_text_heading_candidate",
                }
            )
    return outline[:24]


def _summarize_from_page_samples(pages: list[dict[str, Any]]) -> tuple[str, list[str]]:
    text_blob = " ".join(page.get("sample_text", "") for page in pages if page.get("sample_text"))
    summary = _compact_text(text_blob, SUMMARY_MAX_CHARS)
    topics: list[str] = []
    for page in pages:
        for heading in page.get("heading_candidates", []):
            if heading not in topics:
                topics.append(heading)
            if len(topics) >= 8:
                return summary, topics
    return summary, topics


def _build_fixed_json(ocr_result: dict[str, Any] | None, document_type: str) -> dict[str, Any]:
    contract_result = (ocr_result or {}).get("contract_schema_result", {})
    normalized_contract = contract_result.get("normalized_contract", {}) if contract_result.get("status") == "ok" else {}
    return {
        "document_type": document_type,
        "contract": normalized_contract,
    }


def _build_rag_context(ocr_result: dict[str, Any] | None) -> dict[str, Any]:
    chunk_result = (ocr_result or {}).get("layout_chunk_result", {})
    chunks = chunk_result.get("chunks", [])
    return {
        "chunk_count": chunk_result.get("analysis", {}).get("chunk_count", len(chunks)),
        "table_chunk_count": chunk_result.get("analysis", {}).get("table_chunk_count", 0),
        "referenced_chunk_ids": [chunk.get("chunk_id") for chunk in chunks[:12] if chunk.get("chunk_id")],
    }


def _validate_strict_payload(payload: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    for key in STRICT_OUTPUT_SCHEMA["required"]:
        if key not in payload:
            missing.append(key)
    return {
        "schema_valid": not missing,
        "machine_consumable": not missing,
        "free_text_only": False,
        "missing_required_fields": missing,
    }


def _build_model_contract() -> dict[str, Any]:
    return {
        "input": "PDF file bytes or PDF URL",
        "output_format": "strict_json",
        "json_schema": STRICT_OUTPUT_SCHEMA,
        "instruction": (
            "Return only JSON matching the schema. Include summary, outline_tree, "
            "fixed_json, and rag_context. Do not return prose outside JSON."
        ),
    }


def build_skipped_direct_pdf_structure_result(source_file: str, reason: str) -> dict[str, Any]:
    strict_payload = _empty_strict_payload()
    return {
        "schema_version": DIRECT_PDF_SCHEMA_VERSION,
        "source_file": source_file,
        "source_kind": "non_pdf",
        "status": "skipped",
        "skip_reason": reason,
        "mode": "unsupported_non_pdf",
        "strict_schema": strict_payload,
        "pages": [],
        "model_contract": _build_model_contract(),
        "validation": _validate_strict_payload(strict_payload),
        "analysis": {
            "page_count": 0,
            "native_text_page_count": 0,
            "outline_item_count": 0,
            "summary_char_count": 0,
            "fixed_field_count": 0,
            "needs_multimodal_model": False,
        },
    }


def build_direct_pdf_structure_result(
        *,
        source_path: Path,
        source_kind: str,
        ocr_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """直接从 PDF 文件构建严格结构化 JSON。"""
    if source_kind != "pdf":
        return build_skipped_direct_pdf_structure_result(
            source_path.name,
            "direct PDF structurer only accepts PDF input",
        )

    pages: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    toc_rows: list[list[Any]] = []
    doc = pymupdf.open(source_path)
    try:
        metadata = {key: _normalize_text(value) for key, value in (doc.metadata or {}).items() if _normalize_text(value)}
        toc_rows = doc.get_toc(simple=True)
        for page_index, page in enumerate(doc, start=1):
            page_text = page.get_text("text") or ""
            sample_text = _compact_text(page_text, PAGE_SAMPLE_MAX_CHARS)
            pages.append(
                {
                    "page_num": page_index,
                    "native_text_available": bool(_normalize_text(page_text)),
                    "native_text_char_count": len(_normalize_text(page_text)),
                    "sample_text": sample_text,
                    "heading_candidates": _extract_heading_candidates(page_text),
                }
            )
    finally:
        doc.close()

    page_count = len(pages)
    native_text_page_count = sum(1 for page in pages if page["native_text_available"])
    outline_tree = _toc_to_tree(toc_rows) if toc_rows else _infer_outline_from_pages(pages)
    summary, topics = _summarize_from_page_samples(pages)
    document_type = (ocr_result or {}).get("document_label", "") or "pdf"
    strict_payload = {
        "summary": {
            "short": summary,
            "page_count": page_count,
            "detected_topics": topics,
        },
        "outline_tree": outline_tree,
        "fixed_json": _build_fixed_json(ocr_result, document_type),
        "rag_context": _build_rag_context(ocr_result),
    }
    validation = _validate_strict_payload(strict_payload)
    contract_fields = strict_payload["fixed_json"].get("contract", {})

    return {
        "schema_version": DIRECT_PDF_SCHEMA_VERSION,
        "source_file": source_path.name,
        "source_kind": source_kind,
        "status": "ok" if native_text_page_count else "needs_model_or_ocr",
        "mode": "direct_pdf_native_text" if native_text_page_count else "direct_pdf_no_native_text",
        "metadata": metadata,
        "strict_schema": strict_payload,
        "pages": pages,
        "model_contract": _build_model_contract(),
        "validation": validation,
        "analysis": {
            "page_count": page_count,
            "native_text_page_count": native_text_page_count,
            "outline_item_count": len(toc_rows) if toc_rows else len(outline_tree),
            "summary_char_count": len(summary),
            "fixed_field_count": sum(1 for value in contract_fields.values() if _normalize_text(value)),
            "needs_multimodal_model": native_text_page_count == 0,
        },
    }


def write_direct_pdf_structure_json(structure_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(structure_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
