from __future__ import annotations

"""Multi-page consolidation helpers.

这个模块负责第 11 步：
1. 跨页聚合票据明细、银行流水交易和长表单字段；
2. 对重复实体做轻量去重；
3. 对跨页 totals / balance 做一致性校验。

它只消费前面步骤已经生成的 OCR / table / form / receipt / bundle / contract 结果，
避免继续把垂直规则塞进 ocr_engine.py。
"""

from pathlib import Path
import json
import re
from typing import Any, Callable

CONSOLIDATOR_SCHEMA_VERSION = "1.0"
MONEY_RE = re.compile(r"(?<!\d)(?:[$€£¥]|USD|CNY|RMB)?\s*-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?!\d)", re.IGNORECASE)
DATE_PATTERNS = (
    re.compile(r"(?P<year>\d{4})\s*[年/\-.]\s*(?P<month>\d{1,2})\s*[月/\-.]\s*(?P<day>\d{1,2})\s*日?"),
    re.compile(r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})"),
)
ITEM_HEADER_ALIASES = {
    "description": ("item", "items", "description", "product", "service", "name", "项目", "品名", "商品", "描述"),
    "quantity": ("qty", "quantity", "q'ty", "数量"),
    "unit_price": ("unit price", "price", "rate", "单价"),
    "amount": ("amount", "total", "line total", "金额", "小计"),
}
TRANSACTION_HEADER_ALIASES = {
    "date": ("date", "posting date", "交易日期", "日期"),
    "description": ("description", "details", "memo", "摘要", "交易说明", "对方户名"),
    "debit": ("debit", "withdrawal", "paid out", "支出", "借方"),
    "credit": ("credit", "deposit", "paid in", "收入", "贷方"),
    "amount": ("amount", "transaction amount", "金额", "发生额"),
    "balance": ("balance", "余额"),
}
SUMMARY_KEYWORDS = ("subtotal", "sub total", "tax", "vat", "gst", "total", "amount due", "合计", "总计", "税额", "小计")
BANK_STATEMENT_HINTS = ("bank statement", "account statement", "statement period", "transaction", "balance", "银行流水", "账户明细", "交易明细", "余额")


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize(text: Any) -> str:
    return _normalize_text(text).casefold()


def _parse_money_token(token: str) -> float | None:
    cleaned = _normalize_text(token)
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


def _parse_number_cell(cell: Any) -> float | None:
    text = _normalize_text(cell)
    if not text:
        return None
    money_values = _extract_money_values(text)
    if money_values:
        return money_values[-1]
    try:
        return round(float(text.replace(",", "")), 2)
    except ValueError:
        return None


def _extract_date(text: str) -> str:
    normalized = _normalize_text(text)
    for pattern in DATE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _collect_lines(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for page in ocr_result.get("pages", []):
        for line in sorted(page.get("lines", []), key=lambda item: (item["bbox"]["top"], item["bbox"]["left"])):
            text = _normalize_text(line.get("text", ""))
            if not text:
                continue
            lines.append(
                {
                    "page_num": page.get("page_num", 1),
                    "text": text,
                    "bbox": line.get("bbox", {}),
                }
            )
    return lines


def _header_text(row: list[Any]) -> str:
    return " ".join(_canonicalize(cell) for cell in row if _normalize_text(cell))


def _match_column(header_row: list[Any], aliases: tuple[str, ...]) -> int | None:
    normalized_headers = [_canonicalize(cell) for cell in header_row]
    for index, header in enumerate(normalized_headers):
        if any(alias in header for alias in aliases):
            return index
    return None


def _row_is_summary(row: list[Any]) -> bool:
    lowered = _header_text(row)
    return any(keyword in lowered for keyword in SUMMARY_KEYWORDS)


def _looks_like_item_header(row: list[Any]) -> bool:
    header = _header_text(row)
    return any(alias in header for aliases in ITEM_HEADER_ALIASES.values() for alias in aliases)


def _looks_like_transaction_header(row: list[Any]) -> bool:
    header = _header_text(row)
    date_hit = any(alias in header for alias in TRANSACTION_HEADER_ALIASES["date"])
    balance_hit = any(alias in header for alias in TRANSACTION_HEADER_ALIASES["balance"])
    amount_hit = any(alias in header for alias in (*TRANSACTION_HEADER_ALIASES["amount"], *TRANSACTION_HEADER_ALIASES["debit"], *TRANSACTION_HEADER_ALIASES["credit"]))
    return date_hit and amount_hit and (balance_hit or "transaction" in header or "交易" in header)


def _dedupe_records(
        records: list[dict[str, Any]],
        key_builder: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept_by_key: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []

    for record in records:
        key = key_builder(record)
        record["dedupe_key"] = key
        if key in kept_by_key:
            kept_by_key[key]["duplicate_count"] = kept_by_key[key].get("duplicate_count", 0) + 1
            duplicates.append(
                {
                    "dedupe_key": key,
                    "kept_page_num": kept_by_key[key].get("page_num"),
                    "duplicate_page_num": record.get("page_num"),
                    "representative": kept_by_key[key],
                    "duplicate": record,
                }
            )
            continue

        record["duplicate_count"] = 0
        kept_by_key[key] = record

    return list(kept_by_key.values()), duplicates


def _item_key(item: dict[str, Any]) -> str:
    description = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _canonicalize(item.get("description", "")))
    amount = item.get("amount")
    quantity = item.get("quantity")
    unit_price = item.get("unit_price")
    return f"{description}|{quantity}|{unit_price}|{amount}"


def _transaction_key(transaction: dict[str, Any]) -> str:
    description = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _canonicalize(transaction.get("description", "")))
    return f"{transaction.get('date', '')}|{description}|{transaction.get('debit')}|{transaction.get('credit')}|{transaction.get('amount')}|{transaction.get('balance')}"


def _extract_items_from_tables(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for table in ocr_result.get("tables", []):
        rows = table.get("rows", [])
        if len(rows) < 2 or not _looks_like_item_header(rows[0]):
            continue

        header_row = rows[0]
        description_col = _match_column(header_row, ITEM_HEADER_ALIASES["description"]) or 0
        quantity_col = _match_column(header_row, ITEM_HEADER_ALIASES["quantity"])
        unit_price_col = _match_column(header_row, ITEM_HEADER_ALIASES["unit_price"])
        amount_col = _match_column(header_row, ITEM_HEADER_ALIASES["amount"])

        for row_index, row in enumerate(rows[1:], start=2):
            normalized_row = [_normalize_text(cell) for cell in row]
            if not any(normalized_row) or _row_is_summary(normalized_row):
                continue

            description = normalized_row[description_col] if description_col < len(normalized_row) else normalized_row[0]
            amount = _parse_number_cell(normalized_row[amount_col]) if amount_col is not None and amount_col < len(normalized_row) else None
            if amount is None:
                money_cells = [_parse_number_cell(cell) for cell in normalized_row if cell]
                money_cells = [value for value in money_cells if value is not None]
                amount = money_cells[-1] if money_cells else None

            if not description and amount is None:
                continue

            items.append(
                {
                    "description": description,
                    "quantity": _parse_number_cell(normalized_row[quantity_col]) if quantity_col is not None and quantity_col < len(normalized_row) else None,
                    "unit_price": _parse_number_cell(normalized_row[unit_price_col]) if unit_price_col is not None and unit_price_col < len(normalized_row) else None,
                    "amount": amount,
                    "page_num": table.get("page_num"),
                    "table_id": table.get("table_id", ""),
                    "row_index": row_index,
                    "raw_cells": normalized_row,
                    "source": "table",
                }
            )
    return items


def _extract_items_from_receipt_result(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = ocr_result.get("receipt_invoice_result", {}).get("normalized_receipt", {})
    items: list[dict[str, Any]] = []
    for index, item in enumerate(normalized.get("items", []), start=1):
        copied = dict(item)
        copied.setdefault("page_num", None)
        copied.setdefault("table_id", "")
        copied.setdefault("row_index", index)
        copied.setdefault("source", "receipt_invoice_result")
        items.append(copied)
    return items


def _consolidate_invoice_items(ocr_result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table_items = _extract_items_from_tables(ocr_result)
    receipt_items = _extract_items_from_receipt_result(ocr_result)
    source_items = table_items or receipt_items
    consolidated_items, duplicates = _dedupe_records(source_items, _item_key)
    item_sum = round(sum(item.get("amount") or 0 for item in consolidated_items), 2)
    normalized_receipt = ocr_result.get("receipt_invoice_result", {}).get("normalized_receipt", {})
    tax = normalized_receipt.get("tax")
    expected_total = normalized_receipt.get("total")
    calculated_total = round(item_sum + (tax or 0), 2) if consolidated_items else None
    difference = round((expected_total or 0) - (calculated_total or 0), 2) if expected_total is not None and calculated_total is not None else None
    tolerance = 0.05 if expected_total is None else max(0.05, abs(expected_total) * 0.01)

    validation_status = "not_available"
    if difference is not None:
        validation_status = "matched" if abs(difference) <= tolerance else "mismatch"

    return {
        "vendor": normalized_receipt.get("vendor", ""),
        "date": normalized_receipt.get("date", ""),
        "invoice_number": normalized_receipt.get("invoice_number", ""),
        "currency": normalized_receipt.get("currency", ""),
        "items": consolidated_items,
        "item_sum": item_sum,
        "tax": tax,
        "reported_total": expected_total,
        "calculated_total": calculated_total,
        "total_validation": {
            "status": validation_status,
            "difference": difference,
            "tolerance": round(tolerance, 2),
        },
    }, duplicates


def _extract_transactions_from_tables(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    for table in ocr_result.get("tables", []):
        rows = table.get("rows", [])
        if len(rows) < 2 or not _looks_like_transaction_header(rows[0]):
            continue

        header_row = rows[0]
        date_col = _match_column(header_row, TRANSACTION_HEADER_ALIASES["date"])
        description_col = _match_column(header_row, TRANSACTION_HEADER_ALIASES["description"])
        debit_col = _match_column(header_row, TRANSACTION_HEADER_ALIASES["debit"])
        credit_col = _match_column(header_row, TRANSACTION_HEADER_ALIASES["credit"])
        amount_col = _match_column(header_row, TRANSACTION_HEADER_ALIASES["amount"])
        balance_col = _match_column(header_row, TRANSACTION_HEADER_ALIASES["balance"])

        for row_index, row in enumerate(rows[1:], start=2):
            normalized_row = [_normalize_text(cell) for cell in row]
            if not any(normalized_row):
                continue

            raw_date = normalized_row[date_col] if date_col is not None and date_col < len(normalized_row) else ""
            date = _extract_date(raw_date)
            description = normalized_row[description_col] if description_col is not None and description_col < len(normalized_row) else " ".join(normalized_row)
            debit = _parse_number_cell(normalized_row[debit_col]) if debit_col is not None and debit_col < len(normalized_row) else None
            credit = _parse_number_cell(normalized_row[credit_col]) if credit_col is not None and credit_col < len(normalized_row) else None
            amount = _parse_number_cell(normalized_row[amount_col]) if amount_col is not None and amount_col < len(normalized_row) else None
            balance = _parse_number_cell(normalized_row[balance_col]) if balance_col is not None and balance_col < len(normalized_row) else None

            if amount is None and (debit is not None or credit is not None):
                amount = round((credit or 0) - (debit or 0), 2)
            if not date and amount is None and balance is None:
                continue

            transactions.append(
                {
                    "date": date or raw_date,
                    "description": description,
                    "debit": debit,
                    "credit": credit,
                    "amount": amount,
                    "balance": balance,
                    "page_num": table.get("page_num"),
                    "table_id": table.get("table_id", ""),
                    "row_index": row_index,
                    "raw_cells": normalized_row,
                    "source": "table",
                }
            )
    return transactions


def _extract_balance_by_keywords(lines: list[dict[str, Any]], keywords: tuple[str, ...], *, reverse: bool = False) -> float | None:
    candidates = reversed(lines) if reverse else lines
    for line in candidates:
        lowered = _canonicalize(line["text"])
        if not any(keyword in lowered for keyword in keywords):
            continue
        values = _extract_money_values(line["text"])
        if values:
            return values[-1]
    return None


def _consolidate_bank_statement(ocr_result: dict[str, Any], lines: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    transactions, duplicates = _dedupe_records(_extract_transactions_from_tables(ocr_result), _transaction_key)
    opening_balance = _extract_balance_by_keywords(lines, ("opening balance", "beginning balance", "期初余额"))
    closing_balance = _extract_balance_by_keywords(lines, ("closing balance", "ending balance", "期末余额", "余额"), reverse=True)
    net_change = round(sum(transaction.get("amount") or 0 for transaction in transactions), 2)
    calculated_closing = round(opening_balance + net_change, 2) if opening_balance is not None else None
    difference = round((closing_balance or 0) - (calculated_closing or 0), 2) if closing_balance is not None and calculated_closing is not None else None
    validation_status = "not_available"
    if difference is not None:
        validation_status = "matched" if abs(difference) <= 0.05 else "mismatch"

    return {
        "transactions": transactions,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "net_change": net_change,
        "calculated_closing_balance": calculated_closing,
        "balance_validation": {
            "status": validation_status,
            "difference": difference,
            "tolerance": 0.05,
        },
    }, duplicates


def _merge_form_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    form_result = ocr_result.get("form_result", {})
    normalized_form = dict(form_result.get("normalized_form", {}))
    normalized_form["selected_options"] = sorted(set(normalized_form.get("selected_options", [])))
    return {
        "normalized_form": normalized_form,
        "fields": form_result.get("fields", {}),
        "raw_key_values": form_result.get("raw_key_values", []),
    }


def _merge_contract_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    contract_result = ocr_result.get("contract_schema_result", {})
    return {
        "status": contract_result.get("status", "skipped"),
        "normalized_contract": contract_result.get("normalized_contract", {}),
        "fields": contract_result.get("fields", {}),
    }


def _detect_consolidated_kind(ocr_result: dict[str, Any], lines: list[dict[str, Any]], bank_statement: dict[str, Any]) -> str:
    lowered_blob = "\n".join(_canonicalize(line["text"]) for line in lines)
    normalized_receipt = ocr_result.get("receipt_invoice_result", {}).get("normalized_receipt", {})
    if bank_statement["transactions"] or any(hint in lowered_blob for hint in BANK_STATEMENT_HINTS):
        return "bank_statement"
    if normalized_receipt.get("items") or normalized_receipt.get("total") is not None:
        return "invoice_receipt"
    if ocr_result.get("contract_schema_result", {}).get("status") == "ok":
        return "contract"
    if ocr_result.get("form_result", {}).get("status") != "skipped":
        return "form"
    if ocr_result.get("bundle_splitter_result", {}).get("analysis", {}).get("detected_bundle"):
        return "mixed_bundle"
    return "generic"


def _build_page_summaries(ocr_result: dict[str, Any], items: list[dict[str, Any]], transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    page_labels = {
        page.get("page_num"): page.get("label", "")
        for page in ocr_result.get("bundle_splitter_result", {}).get("page_classifications", [])
    }
    return [
        {
            "page_num": page.get("page_num"),
            "label": page_labels.get(page.get("page_num"), ""),
            "item_count": sum(1 for item in items if item.get("page_num") == page.get("page_num")),
            "transaction_count": sum(1 for transaction in transactions if transaction.get("page_num") == page.get("page_num")),
        }
        for page in ocr_result.get("pages", [])
    ]


def build_multi_page_consolidation_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """生成跨页合并结果，重点解决重复实体和 totals 对账。"""
    lines = _collect_lines(ocr_result)
    invoice_receipt, item_duplicates = _consolidate_invoice_items(ocr_result)
    bank_statement, transaction_duplicates = _consolidate_bank_statement(ocr_result, lines)
    consolidated_kind = _detect_consolidated_kind(ocr_result, lines, bank_statement)
    page_count = len(ocr_result.get("pages", []))
    segments = ocr_result.get("bundle_splitter_result", {}).get("segments", [])
    duplicates = item_duplicates + transaction_duplicates

    return {
        "schema_version": CONSOLIDATOR_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "status": "ok" if page_count > 1 or invoice_receipt["items"] or bank_statement["transactions"] else "skipped",
        "document_kind": consolidated_kind,
        "page_range": {
            "start_page": 1 if page_count else None,
            "end_page": page_count if page_count else None,
            "page_count": page_count,
        },
        "segments": segments,
        "pages": _build_page_summaries(ocr_result, invoice_receipt["items"], bank_statement["transactions"]),
        "consolidated": {
            "receipt_invoice": invoice_receipt,
            "bank_statement": bank_statement,
            "form": _merge_form_result(ocr_result),
            "contract": _merge_contract_result(ocr_result),
        },
        "duplicates": duplicates,
        "analysis": {
            "page_count": page_count,
            "segment_count": len(segments),
            "consolidated_item_count": len(invoice_receipt["items"]),
            "duplicate_item_count": len(item_duplicates),
            "transaction_count": len(bank_statement["transactions"]),
            "duplicate_transaction_count": len(transaction_duplicates),
            "total_check_status": invoice_receipt["total_validation"]["status"],
            "balance_check_status": bank_statement["balance_validation"]["status"],
        },
    }


def write_multi_page_consolidation_json(consolidation_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(consolidation_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
