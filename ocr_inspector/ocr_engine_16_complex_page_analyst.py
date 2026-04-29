from __future__ import annotations

"""Complex page analysis helpers.

第 16 步三选一做深，这里选择“图表问答”：
1. 从表格和 layout chunk 中找图表/图形数据候选；
2. 把可问答的数据点结构化；
3. 回答 max / min / total / trend 这类可解释问题；
4. 每次回答都带证据页和错误解释。

当前实现不追求全能视觉理解，而是做一个可演示、可追溯、能说明错误边界的 chart QA demo。
"""

from pathlib import Path
import json
import re
from typing import Any

COMPLEX_PAGE_SCHEMA_VERSION = "1.0"
SELECTED_DOMAIN = "chart_qa"
MONEY_RE = re.compile(r"(?<!\d)(?:[$€£¥]|USD|CNY|RMB)?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?(?!\d)", re.IGNORECASE)
CHART_HINTS = (
    "chart",
    "figure",
    "graph",
    "trend",
    "growth",
    "revenue",
    "sales",
    "metric",
    "图表",
    "图",
    "趋势",
    "增长",
    "收入",
    "销售",
)
QUESTION_STOPWORDS = {
    "the",
    "is",
    "are",
    "which",
    "what",
    "highest",
    "lowest",
    "largest",
    "smallest",
    "total",
    "sum",
    "trend",
    "最大",
    "最高",
    "最低",
    "最小",
    "合计",
    "总计",
    "趋势",
    "是多少",
    "哪个",
}


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize(text: Any) -> str:
    return _normalize_text(text).casefold()


def _tokenize(text: str) -> set[str]:
    lowered = _canonicalize(text)
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", lowered))
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", lowered)
    for index in range(max(0, len(chinese_chars) - 1)):
        tokens.add("".join(chinese_chars[index:index + 2]))
    return {token for token in tokens if token not in QUESTION_STOPWORDS}


def _parse_number(value: Any) -> float | None:
    text = _normalize_text(value)
    if not text:
        return None
    match = MONEY_RE.search(text)
    if not match:
        return None
    cleaned = match.group(0)
    cleaned = re.sub(r"^(USD|CNY|RMB)", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
    cleaned = cleaned.replace(",", "").replace("%", "")
    try:
        return round(float(cleaned), 4)
    except ValueError:
        return None


def _find_dimension_column(rows: list[list[Any]]) -> int:
    header = rows[0] if rows else []
    for index, cell in enumerate(header):
        lowered = _canonicalize(cell)
        if any(keyword in lowered for keyword in ("region", "category", "year", "month", "quarter", "name", "地区", "类别", "年份", "月份", "季度")):
            return index
    return 0


def _numeric_columns(rows: list[list[Any]], dimension_col: int) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return []
    header = rows[0]
    columns: list[dict[str, Any]] = []
    for col_index, header_cell in enumerate(header):
        if col_index == dimension_col:
            continue
        values: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows[1:], start=2):
            if col_index >= len(row):
                continue
            parsed = _parse_number(row[col_index])
            if parsed is None:
                continue
            dimension = _normalize_text(row[dimension_col] if dimension_col < len(row) else f"row_{row_index}")
            values.append(
                {
                    "label": dimension,
                    "value": parsed,
                    "raw_value": _normalize_text(row[col_index]),
                    "row_index": row_index,
                }
            )
        if len(values) >= 2:
            columns.append(
                {
                    "name": _normalize_text(header_cell) or f"column_{col_index + 1}",
                    "column_index": col_index,
                    "points": values,
                }
            )
    return columns


def _table_has_chart_context(table: dict[str, Any], layout_chunks: list[dict[str, Any]]) -> bool:
    table_text = " ".join(" ".join(_normalize_text(cell) for cell in row) for row in table.get("rows", []))
    lowered = _canonicalize(table_text)
    if any(hint in lowered for hint in CHART_HINTS):
        return True

    page_num = table.get("page_num")
    for chunk in layout_chunks:
        if page_num not in (chunk.get("page_nums") or []):
            continue
        context = _canonicalize(f"{chunk.get('title_context', '')} {chunk.get('text', '')}")
        if any(hint in context for hint in CHART_HINTS):
            return True
    return False


def _summarize_series(series: dict[str, Any]) -> dict[str, Any]:
    points = series["points"]
    max_point = max(points, key=lambda item: item["value"])
    min_point = min(points, key=lambda item: item["value"])
    total = round(sum(point["value"] for point in points), 4)
    first = points[0]
    last = points[-1]
    trend_delta = round(last["value"] - first["value"], 4)
    if trend_delta > 0:
        trend = "increasing"
    elif trend_delta < 0:
        trend = "decreasing"
    else:
        trend = "flat"
    return {
        "max": max_point,
        "min": min_point,
        "total": total,
        "trend": trend,
        "trend_delta": trend_delta,
    }


def _build_chart_candidate(
        table: dict[str, Any],
        *,
        layout_chunks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    rows = table.get("rows", [])
    if len(rows) < 3:
        return None
    dimension_col = _find_dimension_column(rows)
    series = _numeric_columns(rows, dimension_col)
    if not series:
        return None

    has_context = _table_has_chart_context(table, layout_chunks)
    if not has_context and len(series) < 2:
        return None

    for item in series:
        item["summary"] = _summarize_series(item)

    context_chunks = [
        {
            "chunk_id": chunk.get("chunk_id", ""),
            "title_context": chunk.get("title_context", ""),
        }
        for chunk in layout_chunks
        if table.get("page_num") in (chunk.get("page_nums") or [])
    ][:3]
    reliability = 0.72 if has_context else 0.58
    if len(series) >= 2:
        reliability += 0.08

    return {
        "candidate_id": f"chart_{table.get('table_id', 'unknown')}",
        "page_num": table.get("page_num"),
        "table_id": table.get("table_id", ""),
        "title_context": context_chunks[0]["title_context"] if context_chunks else "",
        "dimension_header": _normalize_text(rows[0][dimension_col]) if rows and dimension_col < len(rows[0]) else "",
        "series": series,
        "source_refs": [
            {
                "kind": "table",
                "table_id": table.get("table_id", ""),
                "page_num": table.get("page_num"),
                "csv_path": table.get("csv_path", ""),
                "html_path": table.get("html_path", ""),
            },
            *({"kind": "layout_chunk", **chunk} for chunk in context_chunks),
        ],
        "analysis": {
            "series_count": len(series),
            "point_count": sum(len(item["points"]) for item in series),
            "has_chart_context": has_context,
            "reliability": round(min(reliability, 0.95), 3),
        },
        "error_explanations": [
            "Chart QA is based on extracted table values, not visual bar/axis measurement.",
            "If OCR/table extraction shifted columns, max/min/total answers may be wrong.",
            "Legend colors and visual-only annotations are not interpreted in this local demo.",
        ],
    }


def build_complex_page_analysis_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """构建复杂页面分析结果；当前只做 chart QA 这一类。"""
    layout_chunks = ocr_result.get("layout_chunk_result", {}).get("chunks", [])
    candidates = [
        candidate
        for table in ocr_result.get("tables", [])
        if (candidate := _build_chart_candidate(table, layout_chunks=layout_chunks))
    ]
    page_count = ocr_result.get("page_count", len(ocr_result.get("pages", [])))
    return {
        "schema_version": COMPLEX_PAGE_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "selected_domain": SELECTED_DOMAIN,
        "status": "ok" if candidates else "no_chart_candidate",
        "chart_candidates": candidates,
        "query_history": [],
        "analysis": {
            "page_count": page_count,
            "chart_candidate_count": len(candidates),
            "qa_ready": bool(candidates),
            "selected_domain": SELECTED_DOMAIN,
        },
        "demo_scope": {
            "does": [
                "answers max/min/total/trend questions over extracted chart-like tables",
                "returns evidence page and table reference",
                "explains common failure reasons",
            ],
            "does_not": [
                "read chart pixels directly",
                "interpret legend colors without extracted text/table data",
                "solve formulas or poster semantics",
            ],
        },
    }


def _detect_question_intent(query: str) -> str:
    lowered = _canonicalize(query)
    if any(keyword in lowered for keyword in ("highest", "largest", "max", "maximum", "最高", "最大")):
        return "max"
    if any(keyword in lowered for keyword in ("lowest", "smallest", "min", "minimum", "最低", "最小")):
        return "min"
    if any(keyword in lowered for keyword in ("total", "sum", "合计", "总计", "总和")):
        return "total"
    if any(keyword in lowered for keyword in ("trend", "increase", "decrease", "变化", "趋势", "增长", "下降")):
        return "trend"
    return "summary"


def _score_candidate(candidate: dict[str, Any], query_tokens: set[str]) -> float:
    candidate_text_parts = [
        candidate.get("title_context", ""),
        candidate.get("dimension_header", ""),
        " ".join(series.get("name", "") for series in candidate.get("series", [])),
        " ".join(point.get("label", "") for series in candidate.get("series", []) for point in series.get("points", [])),
    ]
    candidate_tokens = _tokenize(" ".join(candidate_text_parts))
    return float(len(query_tokens & candidate_tokens))


def _choose_series(candidate: dict[str, Any], query_tokens: set[str]) -> dict[str, Any]:
    best_series = candidate["series"][0]
    best_score = -1
    for series in candidate.get("series", []):
        series_tokens = _tokenize(series.get("name", ""))
        score = len(query_tokens & series_tokens)
        if score > best_score:
            best_series = series
            best_score = score
    return best_series


def answer_chart_question(analysis_result: dict[str, Any], query: str) -> dict[str, Any]:
    """回答图表问题，并返回证据表和错误解释。"""
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return {
            "status": "error",
            "answer": "",
            "message": "query is empty",
            "evidence_pages": [],
            "evidence_items": [],
        }
    candidates = analysis_result.get("chart_candidates", [])
    if not candidates:
        return {
            "status": "insufficient_evidence",
            "answer": "",
            "message": "No chart-like table candidate is available.",
            "evidence_pages": [],
            "evidence_items": [],
            "error_explanations": ["No extracted table looked reliable enough for chart QA."],
        }

    query_tokens = _tokenize(normalized_query)
    best_candidate = max(candidates, key=lambda candidate: (_score_candidate(candidate, query_tokens), candidate["analysis"]["reliability"]))
    series = _choose_series(best_candidate, query_tokens)
    intent = _detect_question_intent(normalized_query)
    summary = series["summary"]

    if intent in {"max", "min"}:
        point = summary[intent]
        answer = f"{point['label']} has the {intent} {series['name']} value: {point['raw_value']}."
    elif intent == "total":
        answer = f"The total {series['name']} value is {summary['total']}."
    elif intent == "trend":
        answer = f"{series['name']} is {summary['trend']} from {series['points'][0]['label']} to {series['points'][-1]['label']} (delta {summary['trend_delta']})."
    else:
        point = summary["max"]
        answer = f"{series['name']} has {len(series['points'])} points; the highest visible value is {point['label']} at {point['raw_value']}."

    evidence_item = {
        "candidate_id": best_candidate["candidate_id"],
        "table_id": best_candidate["table_id"],
        "page_num": best_candidate["page_num"],
        "series_name": series["name"],
        "intent": intent,
        "source_refs": best_candidate["source_refs"],
    }
    answer_payload = {
        "status": "ok",
        "answer": answer,
        "query": normalized_query,
        "selected_domain": SELECTED_DOMAIN,
        "evidence_pages": [best_candidate["page_num"]] if best_candidate.get("page_num") is not None else [],
        "evidence_items": [evidence_item],
        "confidence": round(min(0.92, best_candidate["analysis"]["reliability"] + 0.08), 3),
        "error_explanations": best_candidate.get("error_explanations", []),
    }
    analysis_result.setdefault("query_history", []).append(answer_payload)
    analysis_result.setdefault("analysis", {})["query_history_count"] = len(analysis_result["query_history"])
    return answer_payload


def load_complex_page_analysis_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_complex_page_analysis_json(analysis_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(analysis_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
