from __future__ import annotations

"""Signature and handwriting review helpers.

这个模块专门负责：
1. 标记签名区域；
2. 标记手写候选区域；
3. 圈出需要人工复核的可疑字段和低置信度区域；
4. 输出专门给人工复核使用的叠框图。

这里继续拆独立文件，是为了避免把复核逻辑继续堆进 ocr_engine.py。
"""

from pathlib import Path
import json
import re
from typing import Any

from PIL import Image, ImageDraw

REVIEW_SCHEMA_VERSION = "1.0"
DEFAULT_REVIEW_LOW_CONFIDENCE = 85.0
SIGNATURE_LABEL_RE = re.compile(
    r"(signature|signed by|signature of|authorized signature|applicant signature|签名|签字|申请人签名|授权签字)",
    re.IGNORECASE,
)
REVIEW_FIELD_HINT_RE = re.compile(
    r"(name|date|address|phone|email|id|applicant|notes?|comments?|姓名|日期|地址|电话|手机|邮箱|证件|备注)",
    re.IGNORECASE,
)
IMPORTANT_FIELDS = ("name", "date", "phone", "address", "id_number")
LINE_NOISE_RE = re.compile(r"[?_]{2,}|[|]{2,}|[_]{3,}")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _bbox_area(box: dict[str, Any]) -> float:
    return max(0.0, float(box["width"])) * max(0.0, float(box["height"]))


def _expand_bbox(
    box: dict[str, Any],
    *,
    page_width: int,
    page_height: int,
    left: float = 0.0,
    top: float = 0.0,
    right: float = 0.0,
    bottom: float = 0.0,
) -> dict[str, Any]:
    expanded_left = max(0, int(box["left"] - left))
    expanded_top = max(0, int(box["top"] - top))
    expanded_right = min(page_width, int(box["right"] + right))
    expanded_bottom = min(page_height, int(box["bottom"] + bottom))
    return {
        "left": expanded_left,
        "top": expanded_top,
        "right": expanded_right,
        "bottom": expanded_bottom,
        "width": max(0, expanded_right - expanded_left),
        "height": max(0, expanded_bottom - expanded_top),
    }


def _merge_boxes(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    left = min(first["left"], second["left"])
    top = min(first["top"], second["top"])
    right = max(first["right"], second["right"])
    bottom = max(first["bottom"], second["bottom"])
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def _bbox_iou(first: dict[str, Any], second: dict[str, Any]) -> float:
    left = max(first["left"], second["left"])
    top = max(first["top"], second["top"])
    right = min(first["right"], second["right"])
    bottom = min(first["bottom"], second["bottom"])
    if right <= left or bottom <= top:
        return 0.0

    intersection = (right - left) * (bottom - top)
    union = _bbox_area(first) + _bbox_area(second) - intersection
    return intersection / union if union > 0 else 0.0


def _build_page_label_map(bundle_result: dict[str, Any]) -> dict[int, str]:
    return {
        page["page_num"]: page.get("label", "")
        for page in bundle_result.get("page_classifications", [])
    }


def _collect_page_words(page: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        page.get("words", []),
        key=lambda item: (item["bbox"]["top"], item["bbox"]["left"]),
    )


def _collect_page_lines(page: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        page.get("lines", []),
        key=lambda item: (item["bbox"]["top"], item["bbox"]["left"]),
    )


def _line_words(line: dict[str, Any], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched_words: list[dict[str, Any]] = []
    for word in words:
        if (
            word.get("block_num") == line.get("block_num")
            and word.get("par_num") == line.get("par_num")
            and word.get("line_num") == line.get("line_num")
        ):
            matched_words.append(word)
            continue

        iou = _bbox_iou(line["bbox"], word["bbox"])
        if iou >= 0.08:
            matched_words.append(word)
    return matched_words


def _line_confidence(line: dict[str, Any], words: list[dict[str, Any]]) -> float | None:
    line_words = _line_words(line, words)
    valid_confidences = [word["confidence"] for word in line_words if word.get("confidence", -1) >= 0]
    if not valid_confidences:
        return None
    return round(sum(valid_confidences) / len(valid_confidences), 2)


def _low_confidence_line_candidate(
    *,
    line: dict[str, Any],
    words: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any] | None:
    text = _normalize_text(line.get("text", ""))
    if not text:
        return None

    line_words = _line_words(line, words)
    low_conf_words = [word for word in line_words if 0 <= word.get("confidence", -1) < threshold]
    if not low_conf_words:
        return None

    avg_confidence = _line_confidence(line, words)
    if avg_confidence is None:
        return None

    return {
        "text": text,
        "bbox": line["bbox"],
        "avg_confidence": avg_confidence,
        "low_conf_word_count": len(low_conf_words),
        "line_num": line.get("line_num"),
    }


def _find_field_anchor_line(
    field_name: str,
    detail: dict[str, Any],
    lines: list[dict[str, Any]],
) -> dict[str, Any] | None:
    field_page = detail.get("page_num")
    if field_page is None:
        return None

    label_text = _normalize_text(detail.get("label", ""))
    raw_value = _normalize_text(detail.get("raw_value", ""))
    value = _normalize_text(detail.get("value", ""))

    for line in lines:
        if line.get("page_num") != field_page:
            continue
        line_text = _normalize_text(line.get("text", ""))
        if not line_text:
            continue
        if label_text and label_text in line_text:
            return line
        if raw_value and raw_value in line_text:
            return line
        if value and value in line_text:
            return line
        if field_name.casefold() in line_text.casefold():
            return line
    return None


def _build_signature_regions(
    page: dict[str, Any],
    *,
    words: list[dict[str, Any]],
    low_confidence_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signature_regions: list[dict[str, Any]] = []
    page_width = int(page["image_width"])
    page_height = int(page["image_height"])

    for line in _collect_page_lines(page):
        text = _normalize_text(line.get("text", ""))
        if not text or not SIGNATURE_LABEL_RE.search(text):
            continue

        region_box = _expand_bbox(
            line["bbox"],
            page_width=page_width,
            page_height=page_height,
            left=16,
            top=16,
            right=max(180, page_width * 0.26),
            bottom=max(54, line["bbox"]["height"] * 2.5),
        )

        nearby_lines = [
            candidate
            for candidate in low_confidence_candidates
            if _bbox_iou(region_box, candidate["bbox"]) >= 0.04
        ]
        if nearby_lines:
            for candidate in nearby_lines:
                region_box = _merge_boxes(region_box, candidate["bbox"])

        signature_regions.append(
            {
                "page_num": page["page_num"],
                "bbox": region_box,
                "label_text": text,
                "review_reason": "signature area requires manual review",
                "linked_low_confidence_count": len(nearby_lines),
            }
        )

    return signature_regions


def _build_handwriting_regions(
    page: dict[str, Any],
    *,
    page_label: str,
    low_confidence_candidates: list[dict[str, Any]],
    signature_regions: list[dict[str, Any]],
    form_result: dict[str, Any],
    threshold: float,
) -> list[dict[str, Any]]:
    handwriting_regions: list[dict[str, Any]] = []
    signature_boxes = [region["bbox"] for region in signature_regions]
    detail_fields = form_result.get("fields", {})

    for candidate in low_confidence_candidates:
        text = candidate["text"]
        line_box = candidate["bbox"]
        if SIGNATURE_LABEL_RE.search(text):
            continue
        if any(_bbox_iou(line_box, signature_box) >= 0.35 for signature_box in signature_boxes):
            reason = "low-confidence text near signature area"
        elif page_label in {"form", "id"}:
            reason = "low-confidence line on form-like page"
        elif REVIEW_FIELD_HINT_RE.search(text):
            reason = "field-like low-confidence line"
        elif candidate["avg_confidence"] >= threshold - 8:
            continue
        else:
            reason = "generic handwriting candidate"

        linked_field = ""
        for field_name, detail in detail_fields.items():
            if detail.get("page_num") != page.get("page_num"):
                continue
            anchor_label = _normalize_text(detail.get("label", ""))
            anchor_value = _normalize_text(detail.get("value", ""))
            if anchor_label and anchor_label in text:
                linked_field = field_name
                break
            if anchor_value and anchor_value in text:
                linked_field = field_name
                break

        handwriting_regions.append(
            {
                "page_num": page["page_num"],
                "bbox": _expand_bbox(
                    line_box,
                    page_width=page["image_width"],
                    page_height=page["image_height"],
                    left=10,
                    top=8,
                    right=10,
                    bottom=8,
                ),
                "text": text,
                "avg_confidence": candidate["avg_confidence"],
                "linked_field": linked_field,
                "review_reason": reason,
            }
        )

    return handwriting_regions


def _field_value_looks_suspicious(field_name: str, value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return True
    if LINE_NOISE_RE.search(normalized):
        return True
    if field_name == "date":
        return not bool(re.search(r"\d{4}[-/年.]\d{1,2}", normalized))
    if field_name == "phone":
        digits = re.sub(r"\D", "", normalized)
        return len(digits) < 7
    if field_name == "id_number":
        alnum = re.sub(r"[^0-9A-Za-z]", "", normalized)
        return len(alnum) < 8
    return False


def _build_suspicious_fields(
    page: dict[str, Any],
    *,
    words: list[dict[str, Any]],
    form_result: dict[str, Any],
    threshold: float,
) -> list[dict[str, Any]]:
    suspicious_fields: list[dict[str, Any]] = []
    detail_fields = form_result.get("fields", {})
    page_lines = _collect_page_lines(page)

    for field_name in IMPORTANT_FIELDS:
        detail = detail_fields.get(field_name, {})
        if detail.get("page_num") not in {None, page["page_num"]}:
            continue

        anchor_line = _find_field_anchor_line(field_name, detail, page_lines)
        anchor_box = anchor_line["bbox"] if anchor_line else None
        value = detail.get("value", "")
        line_confidence = _line_confidence(anchor_line, words) if anchor_line else None

        if not value:
            if detail.get("page_num") == page["page_num"] or detail.get("page_num") is None:
                suspicious_fields.append(
                    {
                        "page_num": page["page_num"],
                        "field_name": field_name,
                        "value": "",
                        "avg_confidence": line_confidence,
                        "bbox": _expand_bbox(
                            anchor_box or {"left": 24, "top": 24, "right": 180, "bottom": 72, "width": 156, "height": 48},
                            page_width=page["image_width"],
                            page_height=page["image_height"],
                            left=8,
                            top=8,
                            right=8,
                            bottom=8,
                        ),
                        "review_reason": "important field is empty or missing",
                    }
                )
            continue

        if _field_value_looks_suspicious(field_name, value):
            suspicious_fields.append(
                {
                    "page_num": page["page_num"],
                    "field_name": field_name,
                    "value": value,
                    "avg_confidence": line_confidence,
                    "bbox": _expand_bbox(
                        anchor_box or {"left": 24, "top": 24, "right": 180, "bottom": 72, "width": 156, "height": 48},
                        page_width=page["image_width"],
                        page_height=page["image_height"],
                        left=8,
                        top=8,
                        right=8,
                        bottom=8,
                    ),
                    "review_reason": "field value pattern looks suspicious",
                }
            )
            continue

        if line_confidence is not None and line_confidence < threshold:
            suspicious_fields.append(
                {
                    "page_num": page["page_num"],
                    "field_name": field_name,
                    "value": value,
                    "avg_confidence": line_confidence,
                    "bbox": _expand_bbox(
                        anchor_box,
                        page_width=page["image_width"],
                        page_height=page["image_height"],
                        left=8,
                        top=8,
                        right=8,
                        bottom=8,
                    ),
                    "review_reason": "field line confidence is below review threshold",
                }
            )

    return suspicious_fields


def _dedupe_regions(regions: list[dict[str, Any]], *, iou_threshold: float = 0.7) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for region in regions:
        is_duplicate = False
        for existing in deduped:
            if _bbox_iou(region["bbox"], existing["bbox"]) < iou_threshold:
                continue
            if region.get("field_name") and existing.get("field_name") and region["field_name"] != existing["field_name"]:
                continue
            is_duplicate = True
            break
        if is_duplicate:
            continue
        deduped.append(region)
    return deduped


def build_signature_handwriting_review_result(
    ocr_result: dict[str, Any],
    *,
    form_result: dict[str, Any],
    bundle_result: dict[str, Any],
    low_confidence_threshold: float = DEFAULT_REVIEW_LOW_CONFIDENCE,
) -> dict[str, Any]:
    """生成签名区、手写区和可疑字段的人工复核结果。"""
    page_label_map = _build_page_label_map(bundle_result)
    review_pages: list[dict[str, Any]] = []

    for page in ocr_result.get("pages", []):
        page_num = page["page_num"]
        words = _collect_page_words(page)
        low_confidence_candidates = [
            candidate
            for line in _collect_page_lines(page)
            if (candidate := _low_confidence_line_candidate(line=line, words=words, threshold=low_confidence_threshold))
        ]

        signature_regions = _dedupe_regions(
            _build_signature_regions(
                page,
                words=words,
                low_confidence_candidates=low_confidence_candidates,
            )
        )
        handwriting_regions = _dedupe_regions(
            _build_handwriting_regions(
                page,
                page_label=page_label_map.get(page_num, ocr_result.get("document_label", "")),
                low_confidence_candidates=low_confidence_candidates,
                signature_regions=signature_regions,
                form_result=form_result,
                threshold=low_confidence_threshold,
            )
        )
        suspicious_fields = _dedupe_regions(
            _build_suspicious_fields(
                page,
                words=words,
                form_result=form_result,
                threshold=low_confidence_threshold,
            )
        )

        low_confidence_regions = [
            {
                "page_num": page_num,
                "bbox": candidate["bbox"],
                "text": candidate["text"],
                "avg_confidence": candidate["avg_confidence"],
            }
            for candidate in low_confidence_candidates
        ]

        review_pages.append(
            {
                "page_num": page_num,
                "page_label": page_label_map.get(page_num, ocr_result.get("document_label", "")),
                "signature_regions": signature_regions,
                "handwriting_regions": handwriting_regions,
                "suspicious_fields": suspicious_fields,
                "low_confidence_regions": low_confidence_regions,
                "review_overlay_path": "",
            }
        )

    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "pages": review_pages,
        "analysis": {
            "page_count": len(review_pages),
            "review_page_count": sum(
                1
                for page in review_pages
                if page["signature_regions"] or page["handwriting_regions"] or page["suspicious_fields"]
            ),
            "signature_region_count": sum(len(page["signature_regions"]) for page in review_pages),
            "handwriting_region_count": sum(len(page["handwriting_regions"]) for page in review_pages),
            "suspicious_field_count": sum(len(page["suspicious_fields"]) for page in review_pages),
            "low_confidence_region_count": sum(len(page["low_confidence_regions"]) for page in review_pages),
        },
    }


def _draw_region(draw: ImageDraw.ImageDraw, region: dict[str, Any], color: tuple[int, int, int], width: int) -> None:
    box = region["bbox"]
    draw.rectangle(
        [(box["left"], box["top"]), (box["right"], box["bottom"])],
        outline=color,
        width=width,
    )


def write_review_overlays(
    review_result: dict[str, Any],
    *,
    review_overlays_dir: Path,
    page_image_paths_by_page: dict[int, Path],
    output_dir: Path,
) -> None:
    """根据 review JSON 生成单独的人工复核叠框图。"""
    review_overlays_dir.mkdir(parents=True, exist_ok=True)

    for page_review in review_result.get("pages", []):
        page_num = page_review["page_num"]
        page_image_path = page_image_paths_by_page.get(page_num)
        if page_image_path is None or not page_image_path.exists():
            continue

        with Image.open(page_image_path) as image:
            canvas = image.convert("RGB")
            draw = ImageDraw.Draw(canvas)

            for region in page_review.get("signature_regions", []):
                _draw_region(draw, region, (217, 119, 6), 4)
            for region in page_review.get("handwriting_regions", []):
                _draw_region(draw, region, (37, 99, 235), 3)
            for region in page_review.get("suspicious_fields", []):
                _draw_region(draw, region, (220, 38, 38), 3)

            overlay_path = review_overlays_dir / f"page_{page_num:03d}_review.png"
            canvas.save(overlay_path)
            page_review["review_overlay_path"] = overlay_path.relative_to(output_dir).as_posix()


def write_review_json(review_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(review_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
