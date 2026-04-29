from __future__ import annotations

"""Review workbench state and revision helpers.

第 12 步不重新做 OCR，而是把前面已经得到的结果整理成“可人工复核”的工作台数据：
1. 原图 / 叠框图；
2. 预测字段；
3. 低置信度和可疑区域队列；
4. 人工修订记录。

这个模块只负责数据组织和保存，Web 路由放在 app.py，页面放在 web/review_workbench.html。
"""

from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Any

REVIEW_WORKBENCH_SCHEMA_VERSION = "1.0"
DEFAULT_QUEUE_LIMIT = 120
LOW_CONFIDENCE_THRESHOLD = 85.0
REVISION_FILENAME = "review_workbench_revisions.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _url(output_base_url: str, relative_path: str) -> str:
    if not relative_path:
        return ""
    return f"{output_base_url.rstrip('/')}/{relative_path.lstrip('/')}"


def _empty_box() -> dict[str, Any]:
    return {}


def _safe_confidence(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 2)


def _field_id(source: str, field_path: str) -> str:
    compact = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", field_path)
    return f"{source}:{compact}"


def _append_field(
        fields: list[dict[str, Any]],
        *,
        source: str,
        field_path: str,
        label: str,
        value: Any,
        page_num: int | None = None,
        bbox: dict[str, Any] | None = None,
        confidence: Any = None,
        review_reason: str = "",
) -> None:
    normalized_value = _normalize_text(value)
    fields.append(
        {
            "field_id": _field_id(source, field_path),
            "source": source,
            "field_path": field_path,
            "label": label,
            "value": normalized_value,
            "original_value": normalized_value,
            "page_num": page_num,
            "bbox": bbox or _empty_box(),
            "confidence": _safe_confidence(confidence),
            "review_reason": review_reason,
        }
    )


def _collect_form_fields(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    form_result = ocr_result.get("form_result", {})
    for field_name, detail in form_result.get("fields", {}).items():
        _append_field(
            fields,
            source="form",
            field_path=f"form.fields.{field_name}",
            label=field_name,
            value=detail.get("value", ""),
            page_num=detail.get("page_num"),
            bbox=detail.get("bbox", {}),
            review_reason="form normalized field",
        )

    selected_options = form_result.get("normalized_form", {}).get("selected_options", [])
    if selected_options:
        _append_field(
            fields,
            source="form",
            field_path="form.normalized_form.selected_options",
            label="selected_options",
            value=", ".join(selected_options),
            review_reason="selected checkbox options",
        )
    return fields


def _collect_contract_fields(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    contract_result = ocr_result.get("contract_schema_result", {})
    for field_name, detail in contract_result.get("fields", {}).items():
        _append_field(
            fields,
            source="contract",
            field_path=f"contract.fields.{field_name}",
            label=field_name,
            value=detail.get("value", ""),
            page_num=detail.get("page_num"),
            bbox=detail.get("bbox", {}),
            confidence=detail.get("confidence"),
            review_reason="contract schema field",
        )
    return fields


def _collect_receipt_fields(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    normalized_receipt = ocr_result.get("receipt_invoice_result", {}).get("normalized_receipt", {})
    for field_name in ("vendor", "date", "invoice_number", "currency", "subtotal", "tax", "total"):
        if field_name not in normalized_receipt:
            continue
        _append_field(
            fields,
            source="receipt_invoice",
            field_path=f"receipt_invoice.normalized_receipt.{field_name}",
            label=field_name,
            value=normalized_receipt.get(field_name),
            review_reason="receipt / invoice scalar field",
        )
    return fields


def _collect_consolidation_fields(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    consolidated = ocr_result.get("multi_page_consolidation_result", {}).get("consolidated", {})
    receipt = consolidated.get("receipt_invoice", {})
    statement = consolidated.get("bank_statement", {})

    for field_name in ("item_sum", "tax", "reported_total", "calculated_total"):
        if field_name in receipt:
            _append_field(
                fields,
                source="multi_page_consolidation",
                field_path=f"multi_page_consolidation.receipt_invoice.{field_name}",
                label=field_name,
                value=receipt.get(field_name),
                review_reason="cross-page total candidate",
            )

    for field_name in ("opening_balance", "closing_balance", "net_change", "calculated_closing_balance"):
        if field_name in statement:
            _append_field(
                fields,
                source="multi_page_consolidation",
                field_path=f"multi_page_consolidation.bank_statement.{field_name}",
                label=field_name,
                value=statement.get(field_name),
                review_reason="cross-page balance candidate",
            )
    return fields


def _collect_predicted_fields(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    fields = [
        *_collect_form_fields(ocr_result),
        *_collect_contract_fields(ocr_result),
        *_collect_receipt_fields(ocr_result),
        *_collect_consolidation_fields(ocr_result),
    ]
    return sorted(fields, key=lambda item: (item.get("page_num") is None, item.get("page_num") or 999999, item["source"], item["label"]))


def _queue_item(
        *,
        queue_type: str,
        page_num: int | None,
        text: str,
        bbox: dict[str, Any] | None,
        confidence: Any = None,
        field_path: str = "",
        reason: str,
        source: str,
) -> dict[str, Any]:
    return {
        "queue_id": _field_id(source, f"{queue_type}.{page_num}.{field_path}.{text}")[:160],
        "type": queue_type,
        "page_num": page_num,
        "text": _normalize_text(text),
        "bbox": bbox or _empty_box(),
        "confidence": _safe_confidence(confidence),
        "field_path": field_path,
        "review_reason": reason,
        "source": source,
        "status": "pending",
    }


def _collect_review_regions(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    review_result = ocr_result.get("signature_handwriting_review_result", {})
    for page in review_result.get("pages", []):
        page_num = page.get("page_num")
        for index, region in enumerate(page.get("suspicious_fields", []), start=1):
            field_name = region.get("field_name", "")
            queue.append(
                _queue_item(
                    queue_type="suspicious_field",
                    page_num=page_num,
                    text=region.get("value") or field_name,
                    bbox=region.get("bbox"),
                    confidence=region.get("avg_confidence"),
                    field_path=f"form.fields.{field_name}" if field_name else "",
                    reason=region.get("review_reason", "suspicious field requires review"),
                    source=f"review.suspicious_fields.{index}",
                )
            )

        for index, region in enumerate(page.get("handwriting_regions", []), start=1):
            queue.append(
                _queue_item(
                    queue_type="handwriting_region",
                    page_num=page_num,
                    text=region.get("text", ""),
                    bbox=region.get("bbox"),
                    confidence=region.get("avg_confidence"),
                    field_path=region.get("linked_field", ""),
                    reason=region.get("review_reason", "handwriting candidate requires review"),
                    source=f"review.handwriting_regions.{index}",
                )
            )

        for index, region in enumerate(page.get("signature_regions", []), start=1):
            queue.append(
                _queue_item(
                    queue_type="signature_region",
                    page_num=page_num,
                    text=region.get("label_text", ""),
                    bbox=region.get("bbox"),
                    reason=region.get("review_reason", "signature area requires review"),
                    source=f"review.signature_regions.{index}",
                )
            )

        for index, region in enumerate(page.get("low_confidence_regions", []), start=1):
            queue.append(
                _queue_item(
                    queue_type="low_confidence_region",
                    page_num=page_num,
                    text=region.get("text", ""),
                    bbox=region.get("bbox"),
                    confidence=region.get("avg_confidence"),
                    reason="low-confidence OCR region",
                    source=f"review.low_confidence_regions.{index}",
                )
            )
    return queue


def _collect_low_confidence_words(ocr_result: dict[str, Any], threshold: float) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for page in ocr_result.get("pages", []):
        for index, word in enumerate(page.get("words", []), start=1):
            confidence = word.get("confidence", -1)
            if not (0 <= confidence < threshold):
                continue
            queue.append(
                _queue_item(
                    queue_type="low_confidence_word",
                    page_num=page.get("page_num"),
                    text=word.get("text", ""),
                    bbox=word.get("bbox"),
                    confidence=confidence,
                    reason="word confidence is below review threshold",
                    source=f"ocr.words.{index}",
                )
            )
    return queue


def _collect_review_queue(ocr_result: dict[str, Any], *, threshold: float, limit: int) -> list[dict[str, Any]]:
    queue = [*_collect_review_regions(ocr_result), *_collect_low_confidence_words(ocr_result, threshold)]
    queue.sort(key=lambda item: (item.get("page_num") or 999999, item.get("confidence") is None, item.get("confidence") or 999, item["type"]))
    return queue[:limit]


def _build_pages(ocr_result: dict[str, Any], output_base_url: str) -> list[dict[str, Any]]:
    review_pages_by_num = {
        page.get("page_num"): page
        for page in ocr_result.get("signature_handwriting_review_result", {}).get("pages", [])
    }
    pages: list[dict[str, Any]] = []
    for page in ocr_result.get("pages", []):
        page_num = page.get("page_num")
        review_page = review_pages_by_num.get(page_num, {})
        pages.append(
            {
                "page_num": page_num,
                "image_url": _url(output_base_url, f"pages/{page.get('image_path', '')}"),
                "overlay_url": _url(output_base_url, f"overlays/{page.get('overlay_path', '')}"),
                "review_overlay_url": _url(output_base_url, review_page.get("review_overlay_path", "")),
                "text_url": _url(output_base_url, f"texts/{page.get('text_path', '')}"),
                "image_width": page.get("image_width"),
                "image_height": page.get("image_height"),
                "text": page.get("text", ""),
                "review_counts": {
                    "signature_region_count": len(review_page.get("signature_regions", [])),
                    "handwriting_region_count": len(review_page.get("handwriting_regions", [])),
                    "suspicious_field_count": len(review_page.get("suspicious_fields", [])),
                    "low_confidence_region_count": len(review_page.get("low_confidence_regions", [])),
                },
            }
        )
    return pages


def load_review_workbench_revisions(output_dir: Path) -> dict[str, Any]:
    return _read_json(
        output_dir / REVISION_FILENAME,
        {
            "schema_version": REVIEW_WORKBENCH_SCHEMA_VERSION,
            "revision_batches": [],
            "latest_revisions": {},
            "analysis": {
                "revision_batch_count": 0,
                "revision_count": 0,
            },
        },
    )


def initialize_review_workbench_revisions(output_dir: Path, *, source_file: str = "") -> dict[str, Any]:
    """确保复核记录文件存在；后续人工保存时会在这个文件上追加批次。"""
    revision_path = output_dir / REVISION_FILENAME
    if revision_path.exists():
        return load_review_workbench_revisions(output_dir)

    payload = {
        "schema_version": REVIEW_WORKBENCH_SCHEMA_VERSION,
        "source_file": source_file,
        "created_at": _now_iso(),
        "revision_batches": [],
        "latest_revisions": {},
        "analysis": {
            "revision_batch_count": 0,
            "revision_count": 0,
            "latest_revision_count": 0,
        },
    }
    _write_json(revision_path, payload)
    return payload


def build_review_workbench_state(
        *,
        job_id: str,
        output_dir: Path,
        output_base_url: str,
        low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
        queue_limit: int = DEFAULT_QUEUE_LIMIT,
) -> dict[str, Any]:
    """读取 OCR 输出，整理成前端复核台需要的一份状态 JSON。"""
    ocr_json_path = output_dir / "ocr.json"
    if not ocr_json_path.exists():
        raise FileNotFoundError(f"missing ocr.json for job {job_id}")

    ocr_result = _read_json(ocr_json_path)
    predicted_fields = _collect_predicted_fields(ocr_result)
    review_queue = _collect_review_queue(
        ocr_result,
        threshold=low_confidence_threshold,
        limit=queue_limit,
    )
    revisions = load_review_workbench_revisions(output_dir)

    return {
        "schema_version": REVIEW_WORKBENCH_SCHEMA_VERSION,
        "job_id": job_id,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "page_count": ocr_result.get("page_count", len(ocr_result.get("pages", []))),
        "output_base_url": output_base_url,
        "pages": _build_pages(ocr_result, output_base_url),
        "predicted_fields": predicted_fields,
        "review_queue": review_queue,
        "revisions": revisions,
        "analysis": {
            "predicted_field_count": len(predicted_fields),
            "review_queue_count": len(review_queue),
            "revision_count": revisions.get("analysis", {}).get("revision_count", 0),
            "low_confidence_threshold": low_confidence_threshold,
        },
    }


def _sanitize_revision(raw_revision: dict[str, Any]) -> dict[str, Any]:
    field_id = _normalize_text(raw_revision.get("field_id", ""))
    field_path = _normalize_text(raw_revision.get("field_path", ""))
    if not field_id and not field_path:
        raise ValueError("revision requires field_id or field_path")

    return {
        "field_id": field_id or _field_id("manual", field_path),
        "field_path": field_path,
        "source": _normalize_text(raw_revision.get("source", "")),
        "page_num": raw_revision.get("page_num"),
        "old_value": _normalize_text(raw_revision.get("old_value", "")),
        "new_value": _normalize_text(raw_revision.get("new_value", "")),
        "note": _normalize_text(raw_revision.get("note", "")),
        "review_status": _normalize_text(raw_revision.get("review_status", "corrected")) or "corrected",
    }


def save_review_workbench_revisions(
        *,
        job_id: str,
        output_dir: Path,
        payload: dict[str, Any],
) -> dict[str, Any]:
    """保存一次人工复核提交，保留历史批次并更新每个字段的最新修订值。"""
    raw_revisions = payload.get("revisions", [])
    if not isinstance(raw_revisions, list):
        raise ValueError("revisions must be a list")

    revisions = [_sanitize_revision(revision) for revision in raw_revisions if isinstance(revision, dict)]
    if not revisions:
        raise ValueError("at least one revision is required")

    existing = load_review_workbench_revisions(output_dir)
    revision_batches = list(existing.get("revision_batches", []))
    latest_revisions = dict(existing.get("latest_revisions", {}))
    batch = {
        "batch_id": f"batch_{len(revision_batches) + 1:04d}",
        "job_id": job_id,
        "saved_at": _now_iso(),
        "reviewer": _normalize_text(payload.get("reviewer", "")) or "manual_reviewer",
        "note": _normalize_text(payload.get("note", "")),
        "revisions": revisions,
    }

    revision_batches.append(batch)
    for revision in revisions:
        latest_revisions[revision["field_id"]] = {
            **revision,
            "saved_at": batch["saved_at"],
            "batch_id": batch["batch_id"],
        }

    saved_payload = {
        "schema_version": REVIEW_WORKBENCH_SCHEMA_VERSION,
        "job_id": job_id,
        "updated_at": batch["saved_at"],
        "revision_batches": revision_batches,
        "latest_revisions": latest_revisions,
        "analysis": {
            "revision_batch_count": len(revision_batches),
            "revision_count": sum(len(item.get("revisions", [])) for item in revision_batches),
            "latest_revision_count": len(latest_revisions),
        },
    }
    _write_json(output_dir / REVISION_FILENAME, saved_payload)
    return saved_payload
