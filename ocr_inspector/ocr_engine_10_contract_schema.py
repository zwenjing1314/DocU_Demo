from __future__ import annotations

"""Custom contract schema extraction helpers.

这个模块只聚焦一个垂直领域：
1. 合同类文档；
2. 输出统一的 8 字段 JSON；
3. 复用已有 form / router / query 结果来提升稳定性。

之所以单独拆文件，是为了避免继续把垂直领域规则堆进 ocr_engine.py。
"""

from pathlib import Path
import copy
import json
import re
from typing import Any

from ocr_engine_9_query import answer_document_query

CONTRACT_SCHEMA_VERSION = "1.0"
CONTRACT_FIELD_ORDER = (
    "contract_title",
    "contract_number",
    "party_a",
    "party_b",
    "signing_date",
    "effective_date",
    "end_date",
    "total_amount",
)
CONTRACT_TITLE_RE = re.compile(
    r"(service agreement|employment agreement|consulting agreement|lease agreement|nda|non-disclosure agreement|contract|agreement|合同|协议)",
    re.IGNORECASE,
)
CONTRACT_NUMBER_RE = re.compile(
    r"(?:contract\s*(?:no|#|number)|agreement\s*(?:no|#|number)|合同编号|合同号|协议编号)[:：]?\s*([A-Z0-9\-_/]{4,})",
    re.IGNORECASE,
)
PARTY_A_RE = re.compile(r"(?:party\s*a|甲方)[:：]?\s*(?P<value>.+)$", re.IGNORECASE)
PARTY_B_RE = re.compile(r"(?:party\s*b|乙方)[:：]?\s*(?P<value>.+)$", re.IGNORECASE)
SIGNING_DATE_RE = re.compile(r"(?:signing\s*date|signed\s*on|签署日期|签订日期)[:：]?\s*(?P<value>.+)$", re.IGNORECASE)
EFFECTIVE_DATE_RE = re.compile(r"(?:effective\s*date|start\s*date|commencement\s*date|生效日期|起始日期|开始日期)[:：]?\s*(?P<value>.+)$", re.IGNORECASE)
END_DATE_RE = re.compile(r"(?:end\s*date|expiration\s*date|expiry\s*date|termination\s*date|到期日期|结束日期|终止日期)[:：]?\s*(?P<value>.+)$", re.IGNORECASE)
TOTAL_AMOUNT_RE = re.compile(r"(?:contract\s*amount|total\s*amount|contract\s*value|总金额|合同总价|合同金额)[:：]?\s*(?P<value>.+)$", re.IGNORECASE)
CONTRACT_HINTS = (
    "contract",
    "agreement",
    "party a",
    "party b",
    "甲方",
    "乙方",
    "协议",
    "合同",
    "effective date",
    "start date",
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _extract_date(text: str) -> str:
    patterns = (
        re.compile(r"(?P<year>\d{4})\s*[年/\-.]\s*(?P<month>\d{1,2})\s*[月/\-.]\s*(?P<day>\d{1,2})\s*日?"),
        re.compile(r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})"),
    )
    normalized = _normalize_text(text)
    for pattern in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return normalized


def _sorted_lines(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for page in ocr_result.get("pages", []):
        for line in sorted(page.get("lines", []), key=lambda item: (item["bbox"]["top"], item["bbox"]["left"])):
            text = _normalize_text(line.get("text", ""))
            if not text:
                continue
            lines.append(
                {
                    "page_num": page["page_num"],
                    "text": text,
                    "bbox": line["bbox"],
                }
            )
    return lines


def _top_lines(lines: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    return [line for line in lines if line["page_num"] == 1][:limit]


def _build_empty_field() -> dict[str, Any]:
    return {
        "value": "",
        "page_num": None,
        "bbox": {},
        "snippet": "",
        "source": "",
        "confidence": 0.0,
    }


def build_skipped_contract_schema_result(source_file: str, reason: str) -> dict[str, Any]:
    """为非合同文档输出稳定占位结果。"""
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "document_type": "contract",
        "source_file": source_file,
        "status": "skipped",
        "skip_reason": reason,
        "normalized_contract": {field_name: "" for field_name in CONTRACT_FIELD_ORDER},
        "fields": {field_name: _build_empty_field() for field_name in CONTRACT_FIELD_ORDER},
        "analysis": {
            "field_count": 0,
            "is_contract_like": False,
        },
    }


def _store_field(
    detail_fields: dict[str, dict[str, Any]],
    field_name: str,
    *,
    value: str,
    page_num: int | None,
    bbox: dict[str, Any] | None,
    snippet: str,
    source: str,
    confidence: float,
) -> None:
    normalized_value = _normalize_text(value)
    if not normalized_value:
        return

    if field_name.endswith("_date"):
        normalized_value = _extract_date(normalized_value)

    detail_fields[field_name] = {
        "value": normalized_value,
        "page_num": page_num,
        "bbox": bbox or {},
        "snippet": _normalize_text(snippet),
        "source": source,
        "confidence": round(confidence, 3),
    }


def _match_line_pattern(lines: list[dict[str, Any]], pattern: re.Pattern[str]) -> dict[str, Any] | None:
    for line in lines:
        match = pattern.search(line["text"])
        if not match:
            continue
        return {
            "value": _normalize_text(match.group("value") if "value" in match.groupdict() else match.group(1)),
            "page_num": line["page_num"],
            "bbox": line["bbox"],
            "snippet": line["text"],
        }
    return None


def _extract_contract_title(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    for line in _top_lines(lines, limit=6):
        text = line["text"]
        if CONTRACT_TITLE_RE.search(text) and len(text) <= 120:
            return {
                "value": text,
                "page_num": line["page_num"],
                "bbox": line["bbox"],
                "snippet": text,
            }
    return None


def _query_lookup(query_result: dict[str, Any], query: str) -> dict[str, Any]:
    temp_result = copy.deepcopy(query_result)
    return answer_document_query(temp_result, query)


def _extract_field_from_query(query_result: dict[str, Any], query: str) -> dict[str, Any] | None:
    answer = _query_lookup(query_result, query)
    if answer.get("status") != "ok" or not answer.get("answer"):
        return None
    return {
        "value": answer["answer"],
        "page_num": answer.get("page_num"),
        "bbox": answer.get("bbox", {}),
        "snippet": answer.get("snippet", ""),
        "confidence": answer.get("confidence", 0.0),
        "source": f"query:{query}",
    }


def _is_contract_like(ocr_result: dict[str, Any], lines: list[dict[str, Any]]) -> bool:
    lowered_blob = "\n".join(line["text"].casefold() for line in lines)
    top_text = "\n".join(line["text"].casefold() for line in _top_lines(lines))
    hint_count = sum(1 for hint in CONTRACT_HINTS if hint in lowered_blob)

    if CONTRACT_TITLE_RE.search(top_text):
        hint_count += 3
    if PARTY_A_RE.search(lowered_blob) or PARTY_B_RE.search(lowered_blob):
        hint_count += 2
    if _match_line_pattern(lines, CONTRACT_NUMBER_RE):
        hint_count += 1

    source_file = _normalize_text(ocr_result.get("source_file", "")).casefold()
    if "contract" in source_file or "agreement" in source_file or "合同" in source_file:
        hint_count += 1

    return hint_count >= 3


def build_contract_schema_result(
    ocr_result: dict[str, Any],
    *,
    query_result: dict[str, Any],
) -> dict[str, Any]:
    """抽取合同 8 字段统一 JSON。"""
    lines = _sorted_lines(ocr_result)
    if not _is_contract_like(ocr_result, lines):
        return build_skipped_contract_schema_result(
            ocr_result.get("source_file", ""),
            "document does not look like a contract",
        )

    detail_fields = {field_name: _build_empty_field() for field_name in CONTRACT_FIELD_ORDER}

    if title := _extract_contract_title(lines):
        _store_field(detail_fields, "contract_title", source="title_line", confidence=0.93, **title)

    if contract_number := _match_line_pattern(lines, CONTRACT_NUMBER_RE):
        _store_field(detail_fields, "contract_number", source="line_pattern", confidence=0.92, **contract_number)

    if party_a := _match_line_pattern(lines, PARTY_A_RE):
        _store_field(detail_fields, "party_a", source="line_pattern", confidence=0.94, **party_a)

    if party_b := _match_line_pattern(lines, PARTY_B_RE):
        _store_field(detail_fields, "party_b", source="line_pattern", confidence=0.94, **party_b)

    if signing_date := _match_line_pattern(lines, SIGNING_DATE_RE):
        _store_field(detail_fields, "signing_date", source="line_pattern", confidence=0.9, **signing_date)

    if effective_date := _match_line_pattern(lines, EFFECTIVE_DATE_RE):
        _store_field(detail_fields, "effective_date", source="line_pattern", confidence=0.9, **effective_date)
    elif effective_date := _extract_field_from_query(query_result, "合同起始日是哪天"):
        _store_field(detail_fields, "effective_date", **effective_date)

    if end_date := _match_line_pattern(lines, END_DATE_RE):
        _store_field(detail_fields, "end_date", source="line_pattern", confidence=0.9, **end_date)
    elif end_date := _extract_field_from_query(query_result, "合同结束日是哪天"):
        _store_field(detail_fields, "end_date", **end_date)

    if total_amount := _match_line_pattern(lines, TOTAL_AMOUNT_RE):
        _store_field(detail_fields, "total_amount", source="line_pattern", confidence=0.88, **total_amount)
    elif total_amount := _extract_field_from_query(query_result, "总金额是多少"):
        _store_field(detail_fields, "total_amount", **total_amount)

    # 签署日期缺失时，尝试退回到文档日期兜底。
    if not detail_fields["signing_date"]["value"]:
        if signing_date := _extract_field_from_query(query_result, "合同签署日期是哪天"):
            _store_field(detail_fields, "signing_date", **signing_date)
        elif document_date := _extract_field_from_query(query_result, "文档日期是哪天"):
            _store_field(detail_fields, "signing_date", **document_date)

    normalized_contract = {
        field_name: detail_fields[field_name]["value"]
        for field_name in CONTRACT_FIELD_ORDER
    }
    field_count = sum(1 for field_name in CONTRACT_FIELD_ORDER if normalized_contract[field_name])

    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "document_type": "contract",
        "source_file": ocr_result.get("source_file", ""),
        "status": "ok",
        "normalized_contract": normalized_contract,
        "fields": detail_fields,
        "analysis": {
            "field_count": field_count,
            "is_contract_like": True,
        },
    }


def write_contract_schema_json(contract_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(contract_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
