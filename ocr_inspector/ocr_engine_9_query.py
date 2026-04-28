from __future__ import annotations

"""Query-based extraction helpers.

这个模块专门负责：
1. 从 OCR / layout / table / form / receipt 结果里构建可查询索引；
2. 对自然语言问题做轻量意图识别；
3. 返回答案、页码、bbox 和原文片段。

这里继续拆独立文件，是为了避免把 query 逻辑继续堆进 ocr_engine.py 或 app.py。
"""

from pathlib import Path
import json
import re
from typing import Any

QUERY_SCHEMA_VERSION = "1.0"
MONEY_RE = re.compile(r"(?<!\d)(?:[$€£¥]|USD|CNY|RMB)?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?!\d)", re.IGNORECASE)
DATE_PATTERNS = (
    re.compile(r"(?P<year>\d{4})\s*[年/\-.]\s*(?P<month>\d{1,2})\s*[月/\-.]\s*(?P<day>\d{1,2})\s*日?"),
    re.compile(r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})"),
)
INVOICE_NUMBER_RE = re.compile(r"(?:invoice\s*(?:no|#|number)?|发票号码|票据编号|单号)[:：]?\s*([A-Z0-9\-]{4,})", re.IGNORECASE)

QUERY_INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "total_amount": ("total amount", "grand total", "amount due", "total due", "总金额", "总额", "合计", "总计", "应付金额"),
    "tax_amount": ("tax amount", "tax", "vat", "gst", "税额", "税金", "增值税"),
    "subtotal_amount": ("subtotal", "sub total", "小计"),
    "document_date": ("date", "document date", "invoice date", "receipt date", "日期", "单据日期"),
    "contract_start_date": ("contract start date", "start date", "effective date", "commencement date", "合同起始日", "合同开始日期", "合同生效日", "生效日期", "起始日"),
    "contract_end_date": ("contract end date", "end date", "expiration date", "expiry date", "termination date", "合同结束日", "合同终止日", "合同到期日", "到期日"),
    "signing_date": ("signing date", "signed on", "execution date", "签署日期", "签订日期"),
    "invoice_number": ("invoice number", "invoice no", "invoice #", "发票号码", "票据编号", "单号"),
    "vendor": ("vendor", "seller", "merchant", "store", "商户", "卖方", "供应商"),
    "party_a": ("party a", "甲方"),
    "party_b": ("party b", "乙方"),
    "name": ("name", "applicant name", "full name", "姓名", "申请人姓名"),
    "address": ("address", "mailing address", "住址", "地址"),
    "phone": ("phone", "telephone", "mobile", "联系电话", "手机号", "电话"),
    "email": ("email", "e-mail", "邮箱", "电子邮箱"),
    "id_number": ("id number", "identification number", "身份证号", "证件号"),
    "gender": ("gender", "sex", "性别"),
}
QUERY_INTENT_PRIORITY = (
    "total_amount",
    "tax_amount",
    "subtotal_amount",
    "contract_start_date",
    "contract_end_date",
    "signing_date",
    "invoice_number",
    "vendor",
    "party_a",
    "party_b",
    "document_date",
    "name",
    "address",
    "phone",
    "email",
    "id_number",
    "gender",
)
LINE_CANDIDATE_PATTERNS: dict[str, tuple[str, ...]] = {
    "total_amount": ("total", "grand total", "amount due", "合计", "总计", "应付"),
    "tax_amount": ("tax", "vat", "gst", "税额", "税金", "增值税"),
    "subtotal_amount": ("subtotal", "sub total", "小计"),
    "contract_start_date": ("start date", "effective date", "commencement", "起始日", "生效日期", "开始日期"),
    "contract_end_date": ("end date", "expiration", "expiry", "termination", "到期日", "结束日期", "终止日期"),
    "signing_date": ("signing date", "signed on", "execution date", "签署日期", "签订日期"),
    "invoice_number": ("invoice no", "invoice #", "invoice number", "发票号码", "票据编号", "单号"),
}
LINE_FIELD_HINTS = {
    "party_a": ("party a", "甲方"),
    "party_b": ("party b", "乙方"),
    "name": ("name", "姓名"),
    "address": ("address", "地址", "住址"),
    "phone": ("phone", "mobile", "telephone", "电话", "手机号"),
    "email": ("email", "e-mail", "邮箱"),
    "id_number": ("id number", "identification", "身份证", "证件号"),
    "gender": ("gender", "sex", "性别"),
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize(text: str) -> str:
    return _normalize_text(text).casefold()


def _extract_date(text: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def _extract_money_token(text: str) -> str | None:
    matches = list(MONEY_RE.finditer(text))
    if not matches:
        return None
    return _normalize_text(matches[-1].group(0))


def _find_line_for_value(
    *,
    page_num: int | None,
    label_text: str,
    value_text: str,
    pages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for page in pages:
        if page_num is not None and page.get("page_num") != page_num:
            continue
        for line in sorted(page.get("lines", []), key=lambda item: (item["bbox"]["top"], item["bbox"]["left"])):
            line_text = _normalize_text(line.get("text", ""))
            if not line_text:
                continue
            if label_text and _normalize_text(label_text) in line_text:
                return line
            if value_text and _normalize_text(value_text) in line_text:
                return line
    return None


def _append_candidate(
    candidates: list[dict[str, Any]],
    *,
    field_key: str,
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
    candidates.append(
        {
            "field_key": field_key,
            "value": normalized_value,
            "page_num": page_num,
            "bbox": bbox or {},
            "snippet": _normalize_text(snippet),
            "source": source,
            "confidence": round(confidence, 3),
        }
    )


def _build_form_candidates(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    form_result = ocr_result.get("form_result", {})
    if form_result.get("status") == "skipped":
        return []

    candidates: list[dict[str, Any]] = []
    for field_key, detail in form_result.get("fields", {}).items():
        value = detail.get("value", "")
        if not value:
            continue
        line = _find_line_for_value(
            page_num=detail.get("page_num"),
            label_text=detail.get("label", ""),
            value_text=value,
            pages=ocr_result.get("pages", []),
        )
        _append_candidate(
            candidates,
            field_key=field_key,
            value=value,
            page_num=detail.get("page_num"),
            bbox=line.get("bbox", {}) if line else {},
            snippet=line.get("text", detail.get("label", value)) if line else detail.get("label", value),
            source="form_result",
            confidence=0.93,
        )

    return candidates


def _build_receipt_candidates(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    receipt_result = ocr_result.get("receipt_invoice_result", {})
    normalized_receipt = receipt_result.get("normalized_receipt", {})
    if receipt_result.get("status") == "skipped":
        return []

    field_mapping = {
        "vendor": "vendor",
        "date": "document_date",
        "invoice_number": "invoice_number",
        "subtotal": "subtotal_amount",
        "tax": "tax_amount",
        "total": "total_amount",
    }
    candidates: list[dict[str, Any]] = []

    for source_key, field_key in field_mapping.items():
        value = normalized_receipt.get(source_key)
        if value in {"", None}:
            continue
        line = _find_line_for_value(
            page_num=None,
            label_text="",
            value_text=str(value),
            pages=ocr_result.get("pages", []),
        )
        rendered_value = str(value)
        if line and field_key in {"subtotal_amount", "tax_amount", "total_amount"}:
            rendered_value = _extract_money_token(line.get("text", "")) or rendered_value
        _append_candidate(
            candidates,
            field_key=field_key,
            value=rendered_value,
            page_num=line.get("page_num") if line else 1,
            bbox=line.get("bbox", {}) if line else {},
            snippet=line.get("text", rendered_value) if line else rendered_value,
            source="receipt_invoice_result",
            confidence=0.96 if field_key in {"total_amount", "tax_amount"} else 0.9,
        )

    return candidates


def _build_line_candidates(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for page in ocr_result.get("pages", []):
        for line in sorted(page.get("lines", []), key=lambda item: (item["bbox"]["top"], item["bbox"]["left"])):
            text = _normalize_text(line.get("text", ""))
            lowered = _canonicalize(text)
            if not text:
                continue

            for field_key, keywords in LINE_CANDIDATE_PATTERNS.items():
                if not any(keyword in lowered for keyword in keywords):
                    continue
                value = ""
                if "date" in field_key:
                    value = _extract_date(text) or ""
                elif field_key == "invoice_number":
                    match = INVOICE_NUMBER_RE.search(text)
                    value = match.group(1) if match else ""
                else:
                    value = _extract_money_token(text) or ""
                _append_candidate(
                    candidates,
                    field_key=field_key,
                    value=value,
                    page_num=page["page_num"],
                    bbox=line.get("bbox", {}),
                    snippet=text,
                    source="line_pattern",
                    confidence=0.78,
                )

            if detected_date := _extract_date(text):
                _append_candidate(
                    candidates,
                    field_key="document_date",
                    value=detected_date,
                    page_num=page["page_num"],
                    bbox=line.get("bbox", {}),
                    snippet=text,
                    source="line_date",
                    confidence=0.68,
                )

            for field_key, keywords in LINE_FIELD_HINTS.items():
                if not any(keyword in lowered for keyword in keywords):
                    continue
                label_match = re.search(r"[:：]\s*(?P<value>.+)$", text)
                if not label_match:
                    continue
                _append_candidate(
                    candidates,
                    field_key=field_key,
                    value=label_match.group("value"),
                    page_num=page["page_num"],
                    bbox=line.get("bbox", {}),
                    snippet=text,
                    source="line_label",
                    confidence=0.74,
                )

    return candidates


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()
    for candidate in sorted(candidates, key=lambda item: (-item["confidence"], item["page_num"] or 0)):
        key = (candidate["field_key"], candidate["value"], candidate["page_num"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def build_query_extractor_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """构建 query-based extraction 的候选索引。"""
    candidates = _dedupe_candidates(
        [
            * _build_form_candidates(ocr_result),
            * _build_receipt_candidates(ocr_result),
            * _build_line_candidates(ocr_result),
        ]
    )

    return {
        "schema_version": QUERY_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "candidates": candidates,
        "query_history": [],
        "analysis": {
            "candidate_count": len(candidates),
            "field_key_count": len({candidate["field_key"] for candidate in candidates}),
        },
    }


def write_query_json(query_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(query_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_query_json(input_path: Path) -> dict[str, Any]:
    return json.loads(input_path.read_text(encoding="utf-8"))


def _match_query_intents(query: str) -> list[str]:
    lowered_query = _canonicalize(query)
    matched_intents = [
        intent
        for intent in QUERY_INTENT_PRIORITY
        if any(alias in lowered_query for alias in QUERY_INTENT_ALIASES[intent])
    ]

    if matched_intents:
        return matched_intents

    # 如果完全匹配不到，就给一个宽松兜底，避免 query 直接无结果。
    if "金额" in query or "amount" in lowered_query:
        return ["total_amount", "tax_amount", "subtotal_amount"]
    if "日期" in query or "哪天" in query or "date" in lowered_query:
        return ["contract_start_date", "contract_end_date", "document_date"]
    return ["document_date", "name", "total_amount"]


def _query_keyword_overlap(query: str, snippet: str) -> int:
    lowered_query = _canonicalize(query)
    lowered_snippet = _canonicalize(snippet)
    return sum(
        1
        for token in re.split(r"[\s,，。！？?]+", lowered_query)
        if len(token) >= 2 and token in lowered_snippet
    )


def answer_document_query(query_result: dict[str, Any], query: str) -> dict[str, Any]:
    """对同一份文档做轻量 query-based extraction。"""
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return {
            "query": query,
            "status": "invalid_query",
            "answer": "",
            "page_num": None,
            "bbox": {},
            "snippet": "",
            "matched_field": "",
            "confidence": 0.0,
        }

    intents = _match_query_intents(normalized_query)
    candidates = query_result.get("candidates", [])
    scored_candidates: list[tuple[float, dict[str, Any]]] = []

    for candidate in candidates:
        score = candidate.get("confidence", 0.0) * 10
        field_key = candidate.get("field_key", "")

        if field_key in intents:
            score += 12
            score += max(0, len(intents) - intents.index(field_key))
        elif field_key == "document_date" and any(intent.endswith("_date") for intent in intents):
            score += 6
        elif field_key in {"total_amount", "tax_amount", "subtotal_amount"} and any(intent.endswith("_amount") for intent in intents):
            score += 5

        score += min(4, _query_keyword_overlap(normalized_query, candidate.get("snippet", "")))
        if candidate.get("source") in {"receipt_invoice_result", "form_result"}:
            score += 1.5

        if score > 0:
            scored_candidates.append((score, candidate))

    if not scored_candidates:
        answer_payload = {
            "query": query,
            "status": "no_answer",
            "answer": "",
            "page_num": None,
            "bbox": {},
            "snippet": "",
            "matched_field": "",
            "confidence": 0.0,
        }
        query_result.setdefault("query_history", []).append(answer_payload)
        return answer_payload

    best_score, best_candidate = max(scored_candidates, key=lambda item: item[0])
    answer_payload = {
        "query": query,
        "status": "ok",
        "answer": best_candidate.get("value", ""),
        "page_num": best_candidate.get("page_num"),
        "bbox": best_candidate.get("bbox", {}),
        "snippet": best_candidate.get("snippet", ""),
        "matched_field": best_candidate.get("field_key", ""),
        "source": best_candidate.get("source", ""),
        "confidence": round(min(0.99, best_score / 24), 3),
    }
    query_result.setdefault("query_history", []).append(answer_payload)
    return answer_payload
