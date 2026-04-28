from __future__ import annotations

"""Receipt & Invoice extraction helpers.

这个模块专门负责：
1. 识别票据 / 发票的基础 schema；
2. 提取 vendor / date / tax / total 等关键字段；
3. 从表格或行文本恢复 line items。

单独拆文件是为了避免继续把 ocr_engine.py 扩成一个超大模块。
"""

from pathlib import Path
import json
import re
from typing import Any

RECEIPT_SCHEMA_VERSION = "1.0"
DATE_PATTERNS = (
    re.compile(r"(?P<year>\d{4})\s*[年/\-.]\s*(?P<month>\d{1,2})\s*[月/\-.]\s*(?P<day>\d{1,2})\s*日?"),
    re.compile(r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})"),
)
MONEY_RE = re.compile(r"(?<!\d)(?:[$€£¥]|USD|CNY|RMB)?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?!\d)", re.IGNORECASE)
INVOICE_NUMBER_RE = re.compile(r"(?:invoice\s*(?:no|#|number)?|发票号码|票据编号|单号)[:：]?\s*([A-Z0-9\-]{4,})", re.IGNORECASE)

TOTAL_KEYWORDS = ("grand total", "amount due", "total due", "total", "合计", "总计", "应付")
SUBTOTAL_KEYWORDS = ("subtotal", "sub total", "net amount", "小计")
TAX_KEYWORDS = ("tax", "vat", "gst", "sales tax", "税额", "税金", "增值税")
DOCUMENT_TYPE_KEYWORDS = {
    "invoice": ("invoice", "tax invoice", "发票", "invoice no"),
    "receipt": ("receipt", "sales receipt", "收据", "小票"),
}
ITEM_HEADER_ALIASES = {
    "description": ("item", "items", "description", "product", "service", "name", "项目", "品名", "商品", "描述"),
    "quantity": ("qty", "quantity", "q'ty", "数量"),
    "unit_price": ("unit price", "price", "rate", "单价"),
    "amount": ("amount", "total", "line total", "金额", "小计"),
}
VENDOR_HINTS = ("ltd", "llc", "inc", "corp", "co.", "company", "store", "mart", "cafe", "restaurant", "pharmacy", "market", "trading")
NON_VENDOR_HINTS = ("invoice", "receipt", "tax", "total", "date", "phone", "tel", "email", "address", "qty", "amount", "price")
LINE_ITEM_SKIP_HINTS = ("subtotal", "sub total", "tax", "vat", "gst", "total", "amount due", "invoice", "receipt", "date", "change", "cash")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize(text: str) -> str:
    return _normalize_text(text).casefold()


def _parse_money_token(token: str) -> float | None:
    cleaned = token.strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"^(USD|CNY|RMB)", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
    cleaned = cleaned.replace(",", "")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _extract_money_values(text: str) -> list[float]:
    values: list[float] = []
    for match in MONEY_RE.finditer(text):
        value = _parse_money_token(match.group(0))
        if value is not None:
            values.append(value)
    return values


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


def _extract_currency(text: str) -> str:
    lowered = _canonicalize(text)
    if "$" in text or "usd" in lowered:
        return "USD"
    if "¥" in text or "rmb" in lowered or "cny" in lowered:
        return "CNY"
    if "€" in text:
        return "EUR"
    if "£" in text:
        return "GBP"
    return ""


def _collect_sorted_lines(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
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


def _detect_document_type(text_blob: str) -> str:
    lowered = _canonicalize(text_blob)
    for document_type, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return document_type
    return "invoice"


def _extract_vendor(lines: list[dict[str, Any]]) -> str:
    first_page_lines = [line for line in lines if line["page_num"] == 1][:8]
    if not first_page_lines:
        return ""

    best_text = ""
    best_score = float("-inf")
    for index, line in enumerate(first_page_lines):
        text = line["text"]
        lowered = _canonicalize(text)
        score = 0.0

        if any(hint in lowered for hint in VENDOR_HINTS):
            score += 4
        if index == 0:
            score += 3
        elif index <= 2:
            score += 1.5
        if any(character.isalpha() for character in text) or any("\u4e00" <= character <= "\u9fff" for character in text):
            score += 1
        if not any(character.isdigit() for character in text):
            score += 1
        if len(text) > 64:
            score -= 2
        if any(hint in lowered for hint in NON_VENDOR_HINTS):
            score -= 3
        if _extract_money_values(text):
            score -= 3

        if score > best_score:
            best_score = score
            best_text = text

    return best_text if best_score >= 0 else first_page_lines[0]["text"]


def _extract_invoice_number(text_blob: str) -> str:
    match = INVOICE_NUMBER_RE.search(text_blob)
    return match.group(1) if match else ""


def _line_type_score(text: str, keywords: tuple[str, ...]) -> int:
    lowered = _canonicalize(text)
    for index, keyword in enumerate(keywords):
        if keyword in lowered:
            return len(keywords) - index
    return 0


def _extract_amount_by_keywords(lines: list[dict[str, Any]], keywords: tuple[str, ...]) -> float | None:
    best_score = float("-inf")
    best_value: float | None = None

    for index, line in enumerate(lines):
        text = line["text"]
        keyword_score = _line_type_score(text, keywords)
        if keyword_score <= 0:
            continue
        values = _extract_money_values(text)
        if not values:
            continue

        # 票据底部的 total / tax 往往更可靠，因此靠后的行额外加一点分。
        score = keyword_score * 10 + index * 0.1
        candidate = values[-1]
        if score > best_score:
            best_score = score
            best_value = candidate

    return best_value


def _looks_like_item_header(row: list[str]) -> bool:
    header_text = " ".join(_canonicalize(cell) for cell in row if _normalize_text(cell))
    return any(alias in header_text for aliases in ITEM_HEADER_ALIASES.values() for alias in aliases)


def _match_column(header_row: list[str], aliases: tuple[str, ...]) -> int | None:
    normalized_headers = [_canonicalize(cell) for cell in header_row]
    for index, header in enumerate(normalized_headers):
        if any(alias in header for alias in aliases):
            return index
    return None


def _row_is_summary(row: list[str]) -> bool:
    lowered = " ".join(_canonicalize(cell) for cell in row if _normalize_text(cell))
    return any(keyword in lowered for keyword in (*TOTAL_KEYWORDS, *SUBTOTAL_KEYWORDS, *TAX_KEYWORDS))


def _parse_number_from_cell(cell: str) -> float | None:
    values = _extract_money_values(cell)
    if values:
        return values[-1]
    cleaned = _normalize_text(cell).replace(",", "")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _build_items_from_table(table: dict[str, Any]) -> list[dict[str, Any]]:
    rows = table.get("rows", [])
    if len(rows) < 2:
        return []

    header_row = rows[0]
    has_header = _looks_like_item_header(header_row)
    data_rows = rows[1:] if has_header else rows
    description_col = _match_column(header_row, ITEM_HEADER_ALIASES["description"]) if has_header else 0
    quantity_col = _match_column(header_row, ITEM_HEADER_ALIASES["quantity"]) if has_header else None
    unit_price_col = _match_column(header_row, ITEM_HEADER_ALIASES["unit_price"]) if has_header else None
    amount_col = _match_column(header_row, ITEM_HEADER_ALIASES["amount"]) if has_header else None

    items: list[dict[str, Any]] = []
    for row in data_rows:
        normalized_row = [_normalize_text(cell) for cell in row]
        if not any(normalized_row) or _row_is_summary(normalized_row):
            continue

        description = normalized_row[description_col] if description_col is not None and description_col < len(normalized_row) else normalized_row[0]
        amount = None
        if amount_col is not None and amount_col < len(normalized_row):
            amount = _parse_number_from_cell(normalized_row[amount_col])
        if amount is None:
            money_cells = [_parse_number_from_cell(cell) for cell in normalized_row if cell]
            money_cells = [value for value in money_cells if value is not None]
            amount = money_cells[-1] if money_cells else None

        quantity = None
        if quantity_col is not None and quantity_col < len(normalized_row):
            quantity = _parse_number_from_cell(normalized_row[quantity_col])

        unit_price = None
        if unit_price_col is not None and unit_price_col < len(normalized_row):
            unit_price = _parse_number_from_cell(normalized_row[unit_price_col])

        if not description and amount is None:
            continue

        items.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "raw_cells": normalized_row,
                "source": "table",
            }
        )
    return items


def _score_item_table(table: dict[str, Any]) -> float:
    rows = table.get("rows", [])
    if len(rows) < 2:
        return -1

    score = 0.0
    header_row = rows[0]
    if _looks_like_item_header(header_row):
        score += 6
    header_text = " ".join(_canonicalize(cell) for cell in header_row)
    if any(alias in header_text for alias in ITEM_HEADER_ALIASES["description"]):
        score += 3
    if any(alias in header_text for alias in ITEM_HEADER_ALIASES["amount"]):
        score += 3
    if any(alias in header_text for alias in ITEM_HEADER_ALIASES["quantity"]):
        score += 2
    score += min(4, max(0, len(rows) - 1))
    return score


def _extract_items_from_tables(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    tables = ocr_result.get("tables", [])
    if not tables:
        return []

    best_table = max(tables, key=_score_item_table, default=None)
    if not best_table or _score_item_table(best_table) <= 0:
        return []
    return _build_items_from_table(best_table)


def _extract_items_from_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in lines:
        text = line["text"]
        lowered = _canonicalize(text)
        if any(hint in lowered for hint in LINE_ITEM_SKIP_HINTS):
            continue
        if _extract_date(text):
            continue

        amounts = _extract_money_values(text)
        if not amounts:
            continue

        # 这里做一个轻量兜底：只要行尾像“描述 + 金额”，就保留为 item。
        amount = amounts[-1]
        desc = re.sub(MONEY_RE, "", text).strip(" -:|")
        if not desc or len(desc) < 2:
            continue
        if not any(character.isalpha() or "\u4e00" <= character <= "\u9fff" for character in desc):
            continue

        unit_price = amounts[-2] if len(amounts) >= 2 else None
        quantity = None
        qty_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:x|×)\b", text, re.IGNORECASE)
        if qty_match:
            try:
                quantity = round(float(qty_match.group(1)), 2)
            except ValueError:
                quantity = None

        items.append(
            {
                "description": _normalize_text(desc),
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "raw_text": text,
                "source": "line_fallback",
            }
        )
    return items


def build_receipt_invoice_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    lines = _collect_sorted_lines(ocr_result)
    text_blob = "\n".join(line["text"] for line in lines)
    vendor = _extract_vendor(lines)
    date = next((detected for line in lines if (detected := _extract_date(line["text"]))), "") or ""
    subtotal = _extract_amount_by_keywords(lines, SUBTOTAL_KEYWORDS)
    tax = _extract_amount_by_keywords(lines, TAX_KEYWORDS)
    total = _extract_amount_by_keywords(lines, TOTAL_KEYWORDS)
    items = _extract_items_from_tables(ocr_result)
    if not items:
        items = _extract_items_from_lines(lines)

    currency = _extract_currency(text_blob)
    receipt_result = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "document_type": _detect_document_type(text_blob),
        "source_file": ocr_result.get("source_file", ""),
        "normalized_receipt": {
            "vendor": vendor,
            "date": date,
            "invoice_number": _extract_invoice_number(text_blob),
            "currency": currency,
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
            "items": items,
        },
        "analysis": {
            "line_item_count": len(items),
            "table_backed_item_count": sum(1 for item in items if item.get("source") == "table"),
            "fallback_item_count": sum(1 for item in items if item.get("source") == "line_fallback"),
            "has_total": total is not None,
            "has_tax": tax is not None,
            "has_date": bool(date),
        },
    }
    return receipt_result


def write_receipt_invoice_json(receipt_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(receipt_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
