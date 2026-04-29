from __future__ import annotations

"""Evidence-grounded multi-page QA helpers.

第 15 步只建立“有证据的问答”能力：
1. 复用第 13 步 layout chunks 作为主要检索单元；
2. 复用第 14 步 direct PDF strict schema 作为补充证据；
3. 回答必须返回 evidence_pages 和 evidence_chunks。

如果证据不足，这里会明确返回 insufficient_evidence，避免给出无法追溯的答案。
"""

from pathlib import Path
import json
import re
from typing import Any

EVIDENCE_QA_SCHEMA_VERSION = "1.0"
DEFAULT_TOP_K = 5
MIN_EVIDENCE_SCORE = 1.4
MONEY_RE = re.compile(r"(?<!\d)(?:[$€£¥]|USD|CNY|RMB)?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?!\d)", re.IGNORECASE)
DATE_RE = re.compile(
    r"\d{4}\s*[年/\-.]\s*\d{1,2}\s*[月/\-.]\s*\d{1,2}\s*日?|\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4}"
)
QUESTION_STOPWORDS = {
    "the",
    "is",
    "are",
    "what",
    "when",
    "where",
    "which",
    "who",
    "how",
    "much",
    "many",
    "please",
    "tell",
    "me",
    "是多少",
    "是什么",
    "哪天",
    "哪里",
    "什么",
}
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "amount": ("total", "amount", "value", "price", "fee", "cost", "总金额", "金额", "总价", "合同金额"),
    "date": ("date", "effective", "start", "end", "signed", "日期", "起始", "开始", "结束", "签署", "签订"),
    "party": ("party", "vendor", "customer", "甲方", "乙方", "供应商", "客户"),
    "summary": ("summary", "overview", "摘要", "概述", "总结"),
}


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _compact_text(text: str, max_chars: int = 420) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def _tokenize(text: str) -> set[str]:
    normalized = _normalize_text(text).casefold()
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", normalized))

    # 中文短问句经常没有空格，额外加入连续双字片段能让检索更稳一点。
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    for index in range(max(0, len(chinese_chars) - 1)):
        words.add("".join(chinese_chars[index:index + 2]))

    return {word for word in words if word not in QUESTION_STOPWORDS}


def _detect_intents(query: str) -> list[str]:
    lowered = _normalize_text(query).casefold()
    intents: list[str] = []
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            intents.append(intent)
    return intents


def _page_nums_from_range(page_range: dict[str, Any]) -> list[int]:
    start_page = page_range.get("start_page")
    end_page = page_range.get("end_page")
    if start_page is None or end_page is None:
        return []
    return list(range(int(start_page), int(end_page) + 1))


def _append_unit(
        units: list[dict[str, Any]],
        *,
        unit_type: str,
        text: str,
        page_nums: list[int],
        source_ref: dict[str, Any],
        title_context: str = "",
) -> None:
    normalized_text = _normalize_text(text)
    if not normalized_text:
        return

    units.append(
        {
            "unit_id": f"evidence_{len(units) + 1:04d}_{unit_type}",
            "unit_type": unit_type,
            "text": normalized_text,
            "page_nums": sorted(set(page_nums)),
            "title_context": title_context,
            "source_ref": source_ref,
            "token_count": len(_tokenize(normalized_text)),
        }
    )


def _collect_chunk_units(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for chunk in ocr_result.get("layout_chunk_result", {}).get("chunks", []):
        page_nums = chunk.get("page_nums") or _page_nums_from_range(chunk.get("page_range", {}))
        _append_unit(
            units,
            unit_type=f"chunk_{chunk.get('type', 'unknown')}",
            text=chunk.get("text", ""),
            page_nums=page_nums,
            title_context=chunk.get("title_context", ""),
            source_ref={
                "kind": "layout_chunk",
                "chunk_id": chunk.get("chunk_id", ""),
                "chunk_type": chunk.get("type", ""),
                "source_refs": chunk.get("source_refs", []),
            },
        )
    return units


def _flatten_outline(outline: list[dict[str, Any]], *, level_prefix: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, node in enumerate(outline, start=1):
        current_prefix = f"{level_prefix}.{index}" if level_prefix else str(index)
        title = _normalize_text(node.get("title", ""))
        if title:
            rows.append(
                {
                    "title": title,
                    "page_num": node.get("page_num"),
                    "level": node.get("level", 1),
                    "path": current_prefix,
                }
            )
        rows.extend(_flatten_outline(node.get("children", []), level_prefix=current_prefix))
    return rows


def _collect_direct_pdf_units(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    strict_schema = ocr_result.get("direct_pdf_structure_result", {}).get("strict_schema", {})
    summary = strict_schema.get("summary", {})
    summary_text = summary.get("short", "")
    if summary_text:
        _append_unit(
            units,
            unit_type="direct_pdf_summary",
            text=summary_text,
            page_nums=list(range(1, int(summary.get("page_count") or 0) + 1)),
            source_ref={"kind": "direct_pdf_structure", "path": "strict_schema.summary"},
        )

    outline_rows = _flatten_outline(strict_schema.get("outline_tree", []))
    for row in outline_rows:
        _append_unit(
            units,
            unit_type="direct_pdf_outline",
            text=row["title"],
            page_nums=[row["page_num"]] if row.get("page_num") else [],
            source_ref={
                "kind": "direct_pdf_structure",
                "path": "strict_schema.outline_tree",
                "outline_path": row["path"],
            },
        )

    fixed_json = strict_schema.get("fixed_json", {})
    contract = fixed_json.get("contract", {})
    for field_name, value in contract.items():
        if not _normalize_text(value):
            continue
        _append_unit(
            units,
            unit_type="direct_pdf_fixed_json",
            text=f"{field_name}: {value}",
            page_nums=[],
            source_ref={
                "kind": "direct_pdf_structure",
                "path": f"strict_schema.fixed_json.contract.{field_name}",
            },
        )
    return units


def build_evidence_qa_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """构建 evidence QA 索引，后续提问直接复用 evidence_units。"""
    units = [*_collect_chunk_units(ocr_result), *_collect_direct_pdf_units(ocr_result)]
    page_count = ocr_result.get("page_count", len(ocr_result.get("pages", [])))
    return {
        "schema_version": EVIDENCE_QA_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "status": "ok" if units else "empty",
        "index": {
            "unit_count": len(units),
            "page_count": page_count,
            "source_types": sorted({unit["unit_type"] for unit in units}),
        },
        "evidence_units": units,
        "query_history": [],
        "analysis": {
            "unit_count": len(units),
            "chunk_unit_count": sum(1 for unit in units if unit["unit_type"].startswith("chunk_")),
            "direct_pdf_unit_count": sum(1 for unit in units if unit["unit_type"].startswith("direct_pdf_")),
            "page_count": page_count,
        },
    }


def _unit_score(unit: dict[str, Any], query_tokens: set[str], intents: list[str]) -> float:
    unit_text = unit.get("text", "")
    unit_tokens = _tokenize(unit_text)
    if not query_tokens:
        return 0.0

    overlap = len(query_tokens & unit_tokens)
    score = overlap * 2.0
    lowered = unit_text.casefold()
    for intent in intents:
        if any(keyword in lowered for keyword in INTENT_KEYWORDS[intent]):
            score += 1.5
    if unit.get("title_context"):
        title_tokens = _tokenize(unit["title_context"])
        score += len(query_tokens & title_tokens) * 0.8
    if unit.get("unit_type") == "chunk_table" and "amount" in intents:
        score += 0.8
    return round(score, 3)


def _best_sentence(unit_text: str, query_tokens: set[str]) -> str:
    candidates = re.split(r"(?<=[。.!?；;])\s+|\n+", unit_text)
    best = ""
    best_score = -1
    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if not normalized:
            continue
        score = len(query_tokens & _tokenize(normalized))
        if score > best_score:
            best = normalized
            best_score = score
    return _compact_text(best or unit_text)


def _extract_answer_from_evidence(query: str, evidence_text: str, snippet: str) -> str:
    intents = _detect_intents(query)
    search_text = f"{snippet}\n{evidence_text}"

    if "amount" in intents:
        values = MONEY_RE.findall(search_text)
        if values:
            return _normalize_text(values[-1])

    if "date" in intents:
        match = DATE_RE.search(search_text)
        if match:
            return _normalize_text(match.group(0))

    if "summary" in intents:
        return _compact_text(snippet, 260)

    return _compact_text(snippet, 220)


def answer_evidence_question(
        qa_result: dict[str, Any],
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """基于证据单元回答问题；没有证据就明确返回不足。"""
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return {
            "status": "error",
            "answer": "",
            "evidence_pages": [],
            "evidence_chunks": [],
            "message": "query is empty",
        }

    query_tokens = _tokenize(normalized_query)
    intents = _detect_intents(normalized_query)
    scored_units = []
    for unit in qa_result.get("evidence_units", []):
        score = _unit_score(unit, query_tokens, intents)
        if score <= 0:
            continue
        scored_units.append((score, unit))

    scored_units.sort(key=lambda item: item[0], reverse=True)
    top_units = scored_units[:top_k]
    if not top_units or top_units[0][0] < MIN_EVIDENCE_SCORE:
        answer_payload = {
            "status": "insufficient_evidence",
            "answer": "",
            "query": normalized_query,
            "evidence_pages": [],
            "evidence_chunks": [],
            "confidence": 0.0,
            "message": "No evidence chunk matched the question strongly enough.",
        }
    else:
        evidence_chunks: list[dict[str, Any]] = []
        evidence_pages: set[int] = set()
        for score, unit in top_units:
            snippet = _best_sentence(unit["text"], query_tokens)
            page_nums = unit.get("page_nums", [])
            evidence_pages.update(page_nums)
            evidence_chunks.append(
                {
                    "unit_id": unit["unit_id"],
                    "unit_type": unit["unit_type"],
                    "score": score,
                    "page_nums": page_nums,
                    "title_context": unit.get("title_context", ""),
                    "snippet": snippet,
                    "source_ref": unit.get("source_ref", {}),
                }
            )

        best_snippet = evidence_chunks[0]["snippet"]
        best_text = top_units[0][1]["text"]
        answer_payload = {
            "status": "ok",
            "answer": _extract_answer_from_evidence(normalized_query, best_text, best_snippet),
            "query": normalized_query,
            "evidence_pages": sorted(evidence_pages),
            "evidence_chunks": evidence_chunks,
            "confidence": round(min(0.95, 0.45 + top_units[0][0] / 16), 3),
            "message": "Answer is grounded in returned evidence chunks.",
        }

    qa_result.setdefault("query_history", []).append(answer_payload)
    qa_result["analysis"]["query_history_count"] = len(qa_result["query_history"])
    return answer_payload


def load_evidence_qa_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_evidence_qa_json(qa_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(qa_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
