from __future__ import annotations

"""Layout-aware chunking helpers.

第 13 步只消费前面已经生成的 layout / table / consolidation 结果：
1. 正文 chunk 保留标题链和页码；
2. 表格 chunk 原子化保留，不把表头和数据行切碎；
3. 跨页合并摘要作为补充 chunk，方便 RAG 直接检索 totals / balance。

这里继续拆独立模块，避免把 RAG 切块策略塞进 ocr_engine.py。
"""

from pathlib import Path
import json
import re
from typing import Any

LAYOUT_CHUNK_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_CHARS = 900
DEFAULT_OVERLAP_CHARS = 120


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _chunk_id(index: int, chunk_type: str) -> str:
    return f"chunk_{index:04d}_{chunk_type}"


def _page_range(page_nums: list[int]) -> dict[str, int | None]:
    if not page_nums:
        return {"start_page": None, "end_page": None}
    return {"start_page": min(page_nums), "end_page": max(page_nums)}


def _title_context_text(title_chain: list[dict[str, Any]]) -> str:
    if not title_chain:
        return ""
    return " > ".join(item["text"] for item in title_chain if item.get("text"))


def _set_heading(title_chain: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    level = int(item.get("level") or 1)
    next_chain = [heading for heading in title_chain if int(heading.get("level") or 1) < level]
    next_chain.append(
        {
            "level": level,
            "text": _normalize_text(item.get("text", "")),
            "page_num": item.get("page_num"),
        }
    )
    return next_chain


def _markdown_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""

    normalized_rows = [[_normalize_text(cell).replace("|", "\\|") for cell in row] for row in rows]
    col_count = max(len(row) for row in normalized_rows)
    padded_rows = [row + [""] * (col_count - len(row)) for row in normalized_rows]
    header = padded_rows[0]
    separator = ["---"] * col_count
    body = padded_rows[1:]

    def render_row(row: list[str]) -> str:
        return "| " + " | ".join(row) + " |"

    return "\n".join([render_row(header), render_row(separator), *(render_row(row) for row in body)])


def _split_long_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return [normalized] if normalized else []

    parts: list[str] = []
    cursor = 0
    while cursor < len(normalized):
        end = min(len(normalized), cursor + max_chars)
        if end < len(normalized):
            split_at = max(
                normalized.rfind("。", cursor, end),
                normalized.rfind(".", cursor, end),
                normalized.rfind("；", cursor, end),
                normalized.rfind(";", cursor, end),
                normalized.rfind(" ", cursor, end),
            )
            if split_at > cursor + max_chars * 0.45:
                end = split_at + 1
        part = normalized[cursor:end].strip()
        if part:
            parts.append(part)
        if end >= len(normalized):
            break
        cursor = max(end - overlap_chars, cursor + 1)
    return parts


def _make_chunk(
        *,
        index: int,
        chunk_type: str,
        text: str,
        title_chain: list[dict[str, Any]],
        page_nums: list[int],
        source_refs: list[dict[str, Any]],
        extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = _title_context_text(title_chain)
    chunk_text = text if not context else f"{context}\n\n{text}"
    payload = {
        "chunk_id": _chunk_id(index, chunk_type),
        "type": chunk_type,
        "text": chunk_text.strip(),
        "title_chain": title_chain,
        "title_context": context,
        "page_range": _page_range(page_nums),
        "page_nums": sorted(set(page_nums)),
        "source_refs": source_refs,
        "char_count": len(chunk_text.strip()),
    }
    if extra:
        payload.update(extra)
    return payload


def _flush_text_buffer(
        *,
        chunks: list[dict[str, Any]],
        buffer_items: list[dict[str, Any]],
        title_chain: list[dict[str, Any]],
        max_chars: int,
        overlap_chars: int,
) -> None:
    if not buffer_items:
        return

    text = "\n\n".join(item["text"] for item in buffer_items if item.get("text")).strip()
    page_nums = [item["page_num"] for item in buffer_items if item.get("page_num") is not None]
    source_refs = [
        {
            "kind": "layout_item",
            "page_num": item.get("page_num"),
            "item_type": item.get("type"),
        }
        for item in buffer_items
    ]
    for part in _split_long_text(text, max_chars, overlap_chars):
        chunks.append(
            _make_chunk(
                index=len(chunks) + 1,
                chunk_type="text",
                text=part,
                title_chain=title_chain,
                page_nums=page_nums,
                source_refs=source_refs,
            )
        )
    buffer_items.clear()


def _layout_text_for_item(item: dict[str, Any]) -> str:
    text = _normalize_text(item.get("text", ""))
    if not text:
        return ""
    if item.get("type") == "list_item":
        prefix = "1." if item.get("ordered") else "-"
        return f"{prefix} {text}"
    return text


def _build_text_chunks(
        ocr_result: dict[str, Any],
        *,
        max_chars: int,
        overlap_chars: int,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    chunks: list[dict[str, Any]] = []
    title_chain: list[dict[str, Any]] = []
    title_chain_by_page: dict[int, list[dict[str, Any]]] = {}
    buffer_items: list[dict[str, Any]] = []

    for page in ocr_result.get("pages", []):
        page_num = page.get("page_num")
        for item in page.get("layout", {}).get("items", []):
            item_type = item.get("type")
            if item_type == "heading":
                _flush_text_buffer(
                    chunks=chunks,
                    buffer_items=buffer_items,
                    title_chain=title_chain,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
                title_chain = _set_heading(title_chain, item)
                continue

            text = _layout_text_for_item(item)
            if not text:
                continue

            candidate_len = len("\n\n".join([*(entry["text"] for entry in buffer_items), text]))
            if buffer_items and candidate_len > max_chars:
                _flush_text_buffer(
                    chunks=chunks,
                    buffer_items=buffer_items,
                    title_chain=title_chain,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )

            buffer_items.append(
                {
                    "type": item_type,
                    "text": text,
                    "page_num": item.get("page_num", page_num),
                }
            )

        if page_num is not None:
            title_chain_by_page[page_num] = list(title_chain)

    _flush_text_buffer(
        chunks=chunks,
        buffer_items=buffer_items,
        title_chain=title_chain,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    return chunks, title_chain_by_page


def _build_table_chunks(
        ocr_result: dict[str, Any],
        title_chain_by_page: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for table in ocr_result.get("tables", []):
        rows = table.get("rows", [])
        table_markdown = _markdown_table(rows)
        if not table_markdown:
            continue

        page_num = table.get("page_num")
        title_chain = title_chain_by_page.get(page_num, [])
        header_context = [_normalize_text(cell) for cell in rows[0]] if rows else []
        text_parts = [
            f"Table {table.get('table_id', '')}",
            f"Header: {', '.join(header_context)}" if header_context else "",
            table_markdown,
        ]
        chunks.append(
            _make_chunk(
                index=len(chunks) + 1,
                chunk_type="table",
                text="\n\n".join(part for part in text_parts if part),
                title_chain=title_chain,
                page_nums=[page_num] if page_num is not None else [],
                source_refs=[
                    {
                        "kind": "table",
                        "table_id": table.get("table_id", ""),
                        "page_num": page_num,
                        "csv_path": table.get("csv_path", ""),
                        "html_path": table.get("html_path", ""),
                    }
                ],
                extra={
                    "table_id": table.get("table_id", ""),
                    "table_header_context": header_context,
                    "row_count": table.get("row_count", len(rows)),
                    "col_count": table.get("col_count", len(header_context)),
                },
            )
        )
    return chunks


def _build_consolidation_chunk(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    result = ocr_result.get("multi_page_consolidation_result", {})
    if not result or result.get("status") == "skipped":
        return []

    analysis = result.get("analysis", {})
    consolidated = result.get("consolidated", {})
    receipt = consolidated.get("receipt_invoice", {})
    statement = consolidated.get("bank_statement", {})
    lines = [
        f"Document kind: {result.get('document_kind', '')}",
        f"Consolidated item count: {analysis.get('consolidated_item_count', 0)}",
        f"Duplicate item count: {analysis.get('duplicate_item_count', 0)}",
        f"Transaction count: {analysis.get('transaction_count', 0)}",
        f"Total check: {analysis.get('total_check_status', 'not_available')}",
        f"Balance check: {analysis.get('balance_check_status', 'not_available')}",
    ]
    if receipt:
        lines.extend(
            [
                f"Receipt item sum: {receipt.get('item_sum')}",
                f"Receipt reported total: {receipt.get('reported_total')}",
                f"Receipt calculated total: {receipt.get('calculated_total')}",
            ]
        )
    if statement:
        lines.extend(
            [
                f"Opening balance: {statement.get('opening_balance')}",
                f"Closing balance: {statement.get('closing_balance')}",
                f"Net change: {statement.get('net_change')}",
            ]
        )

    page_count = ocr_result.get("page_count", len(ocr_result.get("pages", [])))
    return [
        _make_chunk(
            index=1,
            chunk_type="consolidation_summary",
            text="\n".join(_normalize_text(line) for line in lines if _normalize_text(line)),
            title_chain=[],
            page_nums=list(range(1, page_count + 1)),
            source_refs=[{"kind": "multi_page_consolidation", "path": "multi_page_consolidation.json"}],
        )
    ]


def build_layout_aware_chunk_result(
        ocr_result: dict[str, Any],
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> dict[str, Any]:
    """把 layout / table / consolidation 结果转换为适合 RAG 的 chunks。"""
    text_chunks, title_chain_by_page = _build_text_chunks(
        ocr_result,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )
    table_chunks = _build_table_chunks(ocr_result, title_chain_by_page)
    consolidation_chunks = _build_consolidation_chunk(ocr_result)

    chunks: list[dict[str, Any]] = []
    for chunk in [*text_chunks, *table_chunks, *consolidation_chunks]:
        chunk = dict(chunk)
        chunk["chunk_id"] = _chunk_id(len(chunks) + 1, chunk["type"])
        chunks.append(chunk)

    return {
        "schema_version": LAYOUT_CHUNK_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "chunking": {
            "strategy": "layout_aware_heading_table_atomic",
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
        },
        "chunks": chunks,
        "analysis": {
            "chunk_count": len(chunks),
            "text_chunk_count": sum(1 for chunk in chunks if chunk["type"] == "text"),
            "table_chunk_count": sum(1 for chunk in chunks if chunk["type"] == "table"),
            "consolidation_chunk_count": sum(1 for chunk in chunks if chunk["type"] == "consolidation_summary"),
            "heading_context_chunk_count": sum(1 for chunk in chunks if chunk.get("title_chain")),
        },
    }


def write_layout_chunks_json(chunk_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(chunk_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
