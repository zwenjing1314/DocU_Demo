from __future__ import annotations

"""Mixed document routing helpers.

这个模块专门负责：
1. 对混合文档做轻量分类；
2. 给文档分配 invoice / receipt / form / report / id 标签；
3. 生成后续处理链路，避免所有文档都走同一条业务抽取逻辑。

这里刻意拆成独立文件，是为了继续控制主流水线复杂度，
不把 ocr_engine.py 继续堆成超大模块。
"""

from collections import Counter
from pathlib import Path
import json
import re
from typing import Any, Callable

ROUTER_SCHEMA_VERSION = "1.0"
ROUTER_LABELS = ("invoice", "receipt", "form", "report", "id")
ROUTE_PIPELINES: dict[str, list[str]] = {
    "invoice": ["ocr_inspector", "layout_reader", "table_to_csv", "receipt_invoice_extractor"],
    "receipt": ["ocr_inspector", "layout_reader", "table_to_csv", "receipt_invoice_extractor"],
    "form": ["ocr_inspector", "layout_reader", "table_to_csv", "form_to_json"],
    "report": ["ocr_inspector", "layout_reader", "table_to_csv"],
    "id": ["ocr_inspector", "layout_reader", "table_to_csv", "form_to_json"],
}
SUPPORTED_ROUTER_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}
_CHECKBOX_MARKER_RE = re.compile(r"☑|☒|☐|□|✅|✔|✓|\[[xX ]\]|\([xX ]\)|○|◯")
_KEY_VALUE_RE = re.compile(r"^[^:：]{1,40}[:：]\s*\S+")
_DATE_OF_BIRTH_RE = re.compile(r"(?:date\s+of\s+birth|dob|出生日期|出生)")
_ITEM_HEADER_HINTS = ("item", "items", "description", "qty", "quantity", "price", "amount", "品名", "数量", "单价", "金额")
_TOTAL_HINTS = ("total", "grand total", "amount due", "合计", "总计", "应付")
_TAX_HINTS = ("tax", "vat", "gst", "税额", "税金", "增值税")
_INVOICE_HINTS = ("invoice", "tax invoice", "invoice no", "invoice #", "发票", "发票号码", "票据编号")
_RECEIPT_HINTS = ("receipt", "sales receipt", "收据", "小票")
_FORM_HINTS = ("form", "application", "registration", "applicant", "signature", "申请表", "登记表", "申请人", "签名")
_REPORT_HINTS = ("report", "summary", "introduction", "conclusion", "overview", "analysis", "报告", "摘要", "引言", "结论")
_ID_HINTS = (
    "identity card",
    "id card",
    "national id",
    "resident identity card",
    "passport",
    "driver license",
    "driver's license",
    "身份证",
    "身份证号",
    "居民身份证",
    "护照",
    "驾驶证",
    "签发机关",
    "nationality",
    "sex",
    "gender",
    "date of birth",
    "dob",
    "姓名",
    "出生日期",
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize(text: str) -> str:
    return _normalize_text(text).casefold()


def _collect_sorted_lines(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
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


def _collect_layout_items(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in ocr_result.get("pages", []):
        layout = page.get("layout", {})
        for item in layout.get("items", []):
            if item.get("text"):
                items.append(item)
    return items


def _has_any_keyword(text_blob: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text_blob for keyword in keywords)


def _count_keyword_hits(text_blob: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text_blob)


def _count_key_value_lines(lines: list[dict[str, Any]]) -> int:
    return sum(1 for line in lines if _KEY_VALUE_RE.search(line["text"]))


def _count_checkbox_markers(lines: list[dict[str, Any]]) -> int:
    return sum(len(_CHECKBOX_MARKER_RE.findall(line["text"])) for line in lines)


def _count_amount_lines(lines: list[dict[str, Any]]) -> int:
    return sum(1 for line in lines if re.search(r"\d[\d,.]*\s*$", line["text"]))


def _count_heading_items(layout_items: list[dict[str, Any]]) -> int:
    return sum(1 for item in layout_items if item.get("type") == "heading")


def _count_paragraph_items(layout_items: list[dict[str, Any]]) -> int:
    return sum(1 for item in layout_items if item.get("type") == "paragraph")


def _estimate_word_count(lines: list[dict[str, Any]]) -> int:
    return sum(len(line["text"].split()) for line in lines)


def _count_item_tables(tables: list[dict[str, Any]]) -> int:
    count = 0
    for table in tables:
        rows = table.get("rows", [])
        if not rows:
            continue
        header_text = " ".join(_canonicalize(cell) for cell in rows[0] if _normalize_text(cell))
        if any(hint in header_text for hint in _ITEM_HEADER_HINTS):
            count += 1
    return count


def _add_signal(
    *,
    scores: dict[str, float],
    matched_signals: list[dict[str, Any]],
    label: str,
    signal: str,
    weight: float,
) -> None:
    scores[label] += weight
    matched_signals.append(
        {
            "label": label,
            "signal": signal,
            "weight": round(weight, 2),
        }
    )


def _compute_feature_summary(ocr_result: dict[str, Any]) -> dict[str, Any]:
    lines = _collect_sorted_lines(ocr_result)
    layout_items = _collect_layout_items(ocr_result)
    text_blob = "\n".join(line["text"] for line in lines)
    lowered_blob = _canonicalize(text_blob)
    tables = ocr_result.get("tables", [])

    return {
        "lines": lines,
        "layout_items": layout_items,
        "text_blob": text_blob,
        "lowered_blob": lowered_blob,
        "page_count": len(ocr_result.get("pages", [])),
        "line_count": len(lines),
        "word_count": _estimate_word_count(lines),
        "table_count": len(tables),
        "item_table_count": _count_item_tables(tables),
        "key_value_line_count": _count_key_value_lines(lines),
        "checkbox_marker_count": _count_checkbox_markers(lines),
        "heading_count": _count_heading_items(layout_items),
        "paragraph_count": _count_paragraph_items(layout_items),
        "amount_line_count": _count_amount_lines(lines),
        "has_total_hint": _has_any_keyword(lowered_blob, _TOTAL_HINTS),
        "has_tax_hint": _has_any_keyword(lowered_blob, _TAX_HINTS),
    }


def _score_document_labels(ocr_result: dict[str, Any]) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    features = _compute_feature_summary(ocr_result)
    lowered_blob = features["lowered_blob"]
    scores = {label: 0.0 for label in ROUTER_LABELS}
    matched_signals: list[dict[str, Any]] = []

    # report 作为最弱的兜底基线，避免所有弱信号文档都被误判成其他类型。
    scores["report"] = 1.0

    invoice_keyword_hits = _count_keyword_hits(lowered_blob, _INVOICE_HINTS)
    receipt_keyword_hits = _count_keyword_hits(lowered_blob, _RECEIPT_HINTS)
    form_keyword_hits = _count_keyword_hits(lowered_blob, _FORM_HINTS)
    report_keyword_hits = _count_keyword_hits(lowered_blob, _REPORT_HINTS)
    id_keyword_hits = _count_keyword_hits(lowered_blob, _ID_HINTS)

    if invoice_keyword_hits:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="invoice",
            signal=f"invoice_keywords:{invoice_keyword_hits}",
            weight=8.0 + min(3.0, invoice_keyword_hits - 1),
        )
        scores["receipt"] -= 1.5

    if receipt_keyword_hits:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="receipt",
            signal=f"receipt_keywords:{receipt_keyword_hits}",
            weight=8.0 + min(2.0, receipt_keyword_hits - 1),
        )
        scores["invoice"] -= 1.5

    if form_keyword_hits:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="form",
            signal=f"form_keywords:{form_keyword_hits}",
            weight=5.5 + min(2.0, form_keyword_hits - 1),
        )

    if report_keyword_hits:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="report",
            signal=f"report_keywords:{report_keyword_hits}",
            weight=4.0 + min(2.0, report_keyword_hits - 1),
        )

    if id_keyword_hits:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="id",
            signal=f"id_keywords:{id_keyword_hits}",
            weight=8.0 + min(4.0, id_keyword_hits - 1),
        )

    if features["key_value_line_count"] >= 3:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="form",
            signal=f"key_value_lines:{features['key_value_line_count']}",
            weight=min(7.0, features["key_value_line_count"] * 1.4),
        )
        if features["page_count"] <= 2:
            _add_signal(
                scores=scores,
                matched_signals=matched_signals,
                label="id",
                signal="compact_key_value_layout",
                weight=1.5,
            )

    if features["checkbox_marker_count"] > 0:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="form",
            signal=f"checkbox_markers:{features['checkbox_marker_count']}",
            weight=min(4.0, 1.5 + features["checkbox_marker_count"] * 0.6),
        )

    if features["item_table_count"] > 0:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="invoice",
            signal=f"item_tables:{features['item_table_count']}",
            weight=4.0 + min(2.0, features["item_table_count"]),
        )
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="receipt",
            signal=f"item_tables:{features['item_table_count']}",
            weight=2.0,
        )

    if features["has_total_hint"]:
        _add_signal(scores=scores, matched_signals=matched_signals, label="invoice", signal="has_total_hint", weight=2.5)
        _add_signal(scores=scores, matched_signals=matched_signals, label="receipt", signal="has_total_hint", weight=2.5)
        scores["report"] -= 1.0

    if features["has_tax_hint"]:
        _add_signal(scores=scores, matched_signals=matched_signals, label="invoice", signal="has_tax_hint", weight=2.5)
        _add_signal(scores=scores, matched_signals=matched_signals, label="receipt", signal="has_tax_hint", weight=1.5)
        scores["report"] -= 0.5

    if features["amount_line_count"] >= 3 and features["page_count"] == 1:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="receipt",
            signal=f"money_lines:{features['amount_line_count']}",
            weight=min(3.0, features["amount_line_count"] * 0.5),
        )

    if features["heading_count"] >= 1:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="report",
            signal=f"heading_items:{features['heading_count']}",
            weight=min(4.0, 1.5 + features["heading_count"] * 0.7),
        )

    if features["paragraph_count"] >= 3:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="report",
            signal=f"paragraph_items:{features['paragraph_count']}",
            weight=min(5.0, 2.0 + features["paragraph_count"] * 0.5),
        )
        scores["id"] -= 1.5

    if features["page_count"] >= 2:
        _add_signal(scores=scores, matched_signals=matched_signals, label="report", signal="multi_page_document", weight=2.5)

    if features["word_count"] >= 120:
        _add_signal(scores=scores, matched_signals=matched_signals, label="report", signal="long_text_document", weight=2.5)
        scores["receipt"] -= 1.0
        scores["id"] -= 1.0

    if _DATE_OF_BIRTH_RE.search(lowered_blob):
        _add_signal(scores=scores, matched_signals=matched_signals, label="id", signal="date_of_birth_field", weight=3.0)

    form_analysis = ocr_result.get("form_analysis", {})
    if form_analysis.get("field_count", 0) >= 3:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="form",
            signal=f"form_fields:{form_analysis['field_count']}",
            weight=min(4.0, form_analysis["field_count"] * 0.8),
        )

    receipt_analysis = ocr_result.get("receipt_invoice_analysis", {})
    if receipt_analysis.get("line_item_count", 0) >= 1:
        _add_signal(
            scores=scores,
            matched_signals=matched_signals,
            label="invoice",
            signal=f"receipt_items:{receipt_analysis['line_item_count']}",
            weight=min(3.0, 1.0 + receipt_analysis["line_item_count"] * 0.5),
        )

    return scores, matched_signals, features


def _compute_confidence(scores: dict[str, float], top_label: str) -> float:
    ordered = sorted(scores.values(), reverse=True)
    top_score = max(scores[top_label], 0.0)
    second_score = max(ordered[1], 0.0) if len(ordered) > 1 else 0.0
    if top_score <= 0:
        return 0.0
    margin = max(0.0, top_score - second_score)
    confidence = min(0.99, 0.45 + margin / max(top_score, 1.0) * 0.5)
    return round(confidence, 3)


def _build_dispatch_for_label(label: str) -> dict[str, Any]:
    stages = ROUTE_PIPELINES[label]
    downstream_processor = stages[-1] if len(stages) > 3 else ""
    invoked_processors = list(stages)
    skipped_processors = [
        processor
        for processor in ("form_to_json", "receipt_invoice_extractor")
        if processor not in invoked_processors
    ]
    return {
        "label": label,
        "stages": stages,
        "downstream_processor": downstream_processor,
        "invoked_processors": invoked_processors,
        "skipped_processors": skipped_processors,
    }


def build_mixed_document_router_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """基于 OCR / layout / table 等结果生成混合文档路由决策。"""
    scores, matched_signals, features = _score_document_labels(ocr_result)
    top_label = max(
        ROUTER_LABELS,
        key=lambda label: (scores[label], -ROUTER_LABELS.index(label)),
    )
    dispatch = _build_dispatch_for_label(top_label)

    return {
        "schema_version": ROUTER_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "label": top_label,
        "scores": {label: round(score, 2) for label, score in scores.items()},
        "matched_signals": matched_signals,
        "selected_pipeline": dispatch,
        "analysis": {
            "page_count": features["page_count"],
            "line_count": features["line_count"],
            "word_count": features["word_count"],
            "table_count": features["table_count"],
            "item_table_count": features["item_table_count"],
            "heading_count": features["heading_count"],
            "paragraph_count": features["paragraph_count"],
            "key_value_line_count": features["key_value_line_count"],
            "checkbox_marker_count": features["checkbox_marker_count"],
            "amount_line_count": features["amount_line_count"],
            "confidence": _compute_confidence(scores, top_label),
        },
    }


def write_document_router_json(router_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(router_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def route_documents_in_folder(
    source_dir: Path,
    output_root: Path,
    *,
    pipeline_runner: Callable[..., dict[str, Any]] | None = None,
    supported_extensions: set[str] | None = None,
    **pipeline_kwargs: Any,
) -> dict[str, Any]:
    """批量处理一个混合文件夹，并汇总每个文档的标签与分发链路。

    这里允许注入 pipeline_runner，方便后续接 CLI / Web / 单元测试，
    也避免在模块导入阶段引入不必要的循环依赖。
    """
    if pipeline_runner is None:
        from ocr_engine import run_ocr_pipeline

        pipeline_runner = run_ocr_pipeline

    extensions = supported_extensions or SUPPORTED_ROUTER_EXTENSIONS
    output_root.mkdir(parents=True, exist_ok=True)

    routed_documents: list[dict[str, Any]] = []
    label_counter: Counter[str] = Counter()

    for source_path in sorted(source_dir.iterdir()):
        if not source_path.is_file() or source_path.suffix.lower() not in extensions:
            continue

        source_kind = "pdf" if source_path.suffix.lower() == ".pdf" else "image"
        document_output_dir = output_root / source_path.stem
        pipeline_result = pipeline_runner(
            source_path=source_path,
            output_dir=document_output_dir,
            source_kind=source_kind,
            **pipeline_kwargs,
        )
        router_result = pipeline_result.get("ocr_result", {}).get("document_router_result", {})
        label = router_result.get("label", "report")
        label_counter[label] += 1
        routed_documents.append(
            {
                "source_file": source_path.name,
                "label": label,
                "selected_pipeline": router_result.get("selected_pipeline", {}),
                "output_dir": str(document_output_dir),
            }
        )

    summary = {
        "schema_version": ROUTER_SCHEMA_VERSION,
        "source_dir": str(source_dir),
        "document_count": len(routed_documents),
        "label_counts": dict(sorted(label_counter.items())),
        "documents": routed_documents,
    }
    write_document_router_json(summary, output_root / "mixed_router_index.json")
    return summary
