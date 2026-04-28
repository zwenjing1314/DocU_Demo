from __future__ import annotations

"""Form to JSON helpers.

这个模块专门负责：
1. 从 OCR 结果中抽取表单里的键值对；
2. 解析 checkbox / 选项勾选；
3. 把常见基础字段标准化为固定 JSON。

之所以单独拆文件，是为了避免继续把 ocr_engine.py 做成一个过大的“全能模块”。
"""

from pathlib import Path
import json
import re
from typing import Any

FORM_SCHEMA_VERSION = "1.0"
FORM_FIELD_ORDER = (
    "name",
    "date",
    "address",
    "phone",
    "email",
    "id_number",
    "gender",
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("applicant name", "full name", "name", "申请人姓名", "申请人", "姓名"),
    "date": ("registration date", "application date", "date", "日期", "登记日期", "申请日期"),
    "address": ("mailing address", "residential address", "contact address", "address", "联系地址", "通讯地址", "住址", "地址"),
    "phone": ("mobile phone", "telephone", "phone", "mobile", "tel", "联系电话", "手机号", "手机", "电话"),
    "email": ("e-mail", "email", "邮箱", "电子邮箱"),
    "id_number": ("id number", "id no", "identification number", "身份证号", "身份证", "证件号"),
    "gender": ("sex", "gender", "性别"),
}

CHECKED_MARKERS = {"☑", "☒", "✅", "✔", "✓", "[x]", "[X]", "(x)", "(X)"}
UNCHECKED_MARKERS = {"☐", "□", "[ ]", "( )", "(  )", "[  ]", "○", "◯"}
CHECKBOX_MARKER_RE = re.compile(r"☑|☒|☐|□|✅|✔|✓|\[[xX ]\]|\([xX ]\)|○|◯")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
MOBILE_RE = re.compile(r"1[3-9]\d{9}")
PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{6,}\d")
CN_ID_RE = re.compile(r"\b\d{17}[0-9Xx]\b|\b\d{15}\b")
DATE_PATTERNS = (
    re.compile(r"(?P<year>\d{4})\s*[年/\-.]\s*(?P<month>\d{1,2})\s*[月/\-.]\s*(?P<day>\d{1,2})\s*日?"),
    re.compile(r"(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})\s*/\s*(?P<year>\d{4})"),
)
CANONICAL_GENDER = {
    "男": "male",
    "male": "male",
    "m": "male",
    "女": "female",
    "female": "female",
    "f": "female",
}
OPTION_LABEL_NORMALIZATION = {
    "男": "male",
    "male": "male",
    "女": "female",
    "female": "female",
    "是": "yes",
    "yes": "yes",
    "否": "no",
    "no": "no",
}

SORTED_ALIAS_PAIRS = sorted(
    (
        (field_name, alias)
        for field_name, aliases in FIELD_ALIASES.items()
        for alias in aliases
    ),
    key=lambda item: len(item[1]),
    reverse=True,
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize_label_text(text: str) -> str:
    normalized = _normalize_text(text).casefold()
    return re.sub(r"[\s:：_\-.·/\\|()\[\]{}]+", "", normalized)


def _strip_value_noise(value: str) -> str:
    cleaned = _normalize_text(value)
    cleaned = cleaned.lstrip(":：-—_.,;| ")
    cleaned = re.sub(r"[_]{2,}", " ", cleaned)
    return _normalize_text(cleaned)


def _match_alias_prefix(text: str) -> tuple[str, str] | None:
    normalized_text = _normalize_text(text)
    lowered = normalized_text.casefold()

    for field_name, alias in SORTED_ALIAS_PAIRS:
        alias_lower = alias.casefold()
        if not lowered.startswith(alias_lower):
            continue

        remainder = normalized_text[len(alias):]
        return field_name, remainder
    return None


def _extract_inline_field_candidate(text: str) -> tuple[str, str] | None:
    matched = _match_alias_prefix(text)
    if not matched:
        return None

    field_name, remainder = matched
    remainder = re.sub(r"^[\s:：_\-.·|]+", "", remainder)
    remainder = _strip_value_noise(remainder)
    if not remainder:
        return None
    return field_name, remainder


def _is_label_only_text(text: str) -> tuple[str, str] | None:
    matched = _match_alias_prefix(text)
    if not matched:
        return None

    field_name, remainder = matched
    if _canonicalize_label_text(remainder):
        return None
    return field_name, _normalize_text(text)


def _looks_like_known_label(text: str) -> bool:
    normalized = _canonicalize_label_text(text)
    if not normalized:
        return False
    return any(normalized == _canonicalize_label_text(alias) for _, alias in SORTED_ALIAS_PAIRS)


def _line_vertical_overlap(first: dict[str, Any], second: dict[str, Any]) -> float:
    return max(
        0.0,
        min(first["bbox"]["bottom"], second["bbox"]["bottom"]) - max(first["bbox"]["top"], second["bbox"]["top"]),
    )


def _collect_continuation_lines(seed_line: dict[str, Any], lines: list[dict[str, Any]]) -> str:
    """给地址这类可能换行的字段拼接紧随其后的连续文本。"""
    parts = [_normalize_text(seed_line["text"])]
    seed_box = seed_line["bbox"]
    current_bottom = seed_box["bottom"]

    for candidate in lines:
        if candidate["page_num"] != seed_line["page_num"] or candidate["order"] <= seed_line["order"]:
            continue
        candidate_box = candidate["bbox"]
        if candidate_box["top"] - current_bottom > max(20, seed_box["height"] * 1.4):
            break
        if abs(candidate_box["left"] - seed_box["left"]) > max(48, seed_box["width"] * 0.2):
            break
        if _looks_like_known_label(candidate["text"]):
            break
        parts.append(_normalize_text(candidate["text"]))
        current_bottom = candidate_box["bottom"]

    return _normalize_text(" ".join(parts))


def _find_spatial_value(field_name: str, label_line: dict[str, Any], lines: list[dict[str, Any]]) -> tuple[str, str] | None:
    same_row_candidates: list[tuple[float, dict[str, Any]]] = []
    below_candidates: list[tuple[float, dict[str, Any]]] = []
    label_box = label_line["bbox"]
    label_center = label_box["top"] + label_box["height"] / 2

    for candidate in lines:
        if candidate["page_num"] != label_line["page_num"] or candidate["order"] == label_line["order"]:
            continue
        if not _normalize_text(candidate["text"]):
            continue
        if _looks_like_known_label(candidate["text"]):
            continue

        candidate_box = candidate["bbox"]
        vertical_overlap = _line_vertical_overlap(label_line, candidate)
        candidate_center = candidate_box["top"] + candidate_box["height"] / 2

        if candidate_box["left"] >= label_box["right"] - 8 and (
                vertical_overlap >= min(label_box["height"], candidate_box["height"]) * 0.35
                or abs(candidate_center - label_center) <= max(12.0, label_box["height"] * 0.7)
        ):
            same_row_candidates.append((candidate_box["left"] - label_box["right"], candidate))
            continue

        if (
                candidate_box["top"] >= label_box["bottom"]
                and candidate_box["top"] - label_box["bottom"] <= max(80.0, label_box["height"] * 4.0)
                and candidate_box["left"] <= label_box["left"] + max(48.0, label_box["width"] * 0.4)
        ):
            below_candidates.append((candidate_box["top"] - label_box["bottom"], candidate))

    if same_row_candidates:
        best_line = min(same_row_candidates, key=lambda item: item[0])[1]
        value = _normalize_text(best_line["text"])
        if field_name == "address":
            value = _collect_continuation_lines(best_line, lines)
        return value, "right_neighbor"

    if below_candidates:
        best_line = min(below_candidates, key=lambda item: item[0])[1]
        value = _normalize_text(best_line["text"])
        if field_name == "address":
            value = _collect_continuation_lines(best_line, lines)
        return value, "below_neighbor"

    return None


def _normalize_date(value: str) -> str:
    cleaned = _strip_value_noise(value)
    for pattern in DATE_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return cleaned


def _normalize_name(value: str) -> str:
    return _strip_value_noise(value)


def _normalize_address(value: str) -> str:
    cleaned = _strip_value_noise(value)
    return re.sub(r"\s{2,}", " ", cleaned)


def _normalize_phone(value: str) -> str:
    cleaned = _strip_value_noise(value)
    mobile_match = MOBILE_RE.search(cleaned)
    if mobile_match:
        return mobile_match.group(0)

    phone_match = PHONE_RE.search(cleaned)
    if phone_match:
        return _normalize_text(phone_match.group(0))
    return cleaned


def _normalize_email(value: str) -> str:
    cleaned = _strip_value_noise(value)
    email_match = EMAIL_RE.search(cleaned)
    return email_match.group(0) if email_match else cleaned


def _normalize_id_number(value: str) -> str:
    cleaned = _strip_value_noise(value)
    id_match = CN_ID_RE.search(cleaned)
    return id_match.group(0).upper() if id_match else cleaned


def _normalize_gender(value: str) -> str:
    cleaned = _strip_value_noise(value).casefold()
    return CANONICAL_GENDER.get(cleaned, _strip_value_noise(value))


NORMALIZERS = {
    "name": _normalize_name,
    "date": _normalize_date,
    "address": _normalize_address,
    "phone": _normalize_phone,
    "email": _normalize_email,
    "id_number": _normalize_id_number,
    "gender": _normalize_gender,
}


def _normalize_checkbox_label(label: str) -> str:
    cleaned = _strip_value_noise(label)
    return OPTION_LABEL_NORMALIZATION.get(cleaned.casefold(), cleaned)


def _marker_is_checked(marker: str) -> bool:
    if marker in CHECKED_MARKERS:
        return True
    if marker in UNCHECKED_MARKERS:
        return False
    compact = marker.replace(" ", "")
    return compact in {"[x]", "[X]", "(x)", "(X)"}


def _extract_checkboxes_from_line(line: dict[str, Any]) -> list[dict[str, Any]]:
    text = _normalize_text(line["text"])
    matches = list(CHECKBOX_MARKER_RE.finditer(text))
    if not matches:
        return []

    items: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        marker = match.group(0)
        label = _strip_value_noise(text[match.end():next_start])
        if not label:
            continue
        items.append(
            {
                "label": label,
                "normalized_label": _normalize_checkbox_label(label),
                "checked": _marker_is_checked(marker),
                "marker": marker,
                "page_num": line["page_num"],
                "source_text": text,
            }
        )
    return items


def _collect_line_entries(ocr_result: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    order = 0
    for page in ocr_result.get("pages", []):
        for line in sorted(
                page.get("lines", []),
                key=lambda item: (item["bbox"]["top"], item["bbox"]["left"]),
        ):
            text = _normalize_text(line.get("text", ""))
            if not text:
                continue
            entries.append(
                {
                    "page_num": page["page_num"],
                    "order": order,
                    "text": text,
                    "bbox": line["bbox"],
                }
            )
            order += 1
    return entries


def _maybe_store_field(
        detail_fields: dict[str, dict[str, Any]],
        field_name: str,
        raw_value: str,
        *,
        page_num: int,
        source: str,
        label_text: str,
) -> None:
    cleaned_value = _strip_value_noise(raw_value)
    if not cleaned_value:
        return

    normalized_value = NORMALIZERS.get(field_name, _strip_value_noise)(cleaned_value)
    existing = detail_fields.get(field_name)
    source_priority = {
        "inline": 0,
        "right_neighbor": 1,
        "below_neighbor": 2,
        "checkbox": 3,
    }
    if existing and source_priority.get(existing["source"], 99) <= source_priority.get(source, 99):
        return

    detail_fields[field_name] = {
        "value": normalized_value,
        "raw_value": cleaned_value,
        "page_num": page_num,
        "source": source,
        "label": label_text,
    }


def build_form_to_json_result(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """从 OCR 结果中抽取固定表单 JSON。"""
    lines = _collect_line_entries(ocr_result)
    detail_fields: dict[str, dict[str, Any]] = {}
    raw_key_values: list[dict[str, Any]] = []
    checkbox_items: list[dict[str, Any]] = []

    for line in lines:
        inline_candidate = _extract_inline_field_candidate(line["text"])
        if inline_candidate:
            field_name, raw_value = inline_candidate
            _maybe_store_field(
                detail_fields,
                field_name,
                raw_value,
                page_num=line["page_num"],
                source="inline",
                label_text=line["text"],
            )
            raw_key_values.append(
                {
                    "field": field_name,
                    "raw_value": _strip_value_noise(raw_value),
                    "page_num": line["page_num"],
                    "source": "inline",
                    "label_text": line["text"],
                }
            )
        else:
            label_only = _is_label_only_text(line["text"])
            if label_only:
                field_name, label_text = label_only
                spatial_value = _find_spatial_value(field_name, line, lines)
                if spatial_value:
                    raw_value, source = spatial_value
                    _maybe_store_field(
                        detail_fields,
                        field_name,
                        raw_value,
                        page_num=line["page_num"],
                        source=source,
                        label_text=label_text,
                    )
                    raw_key_values.append(
                        {
                            "field": field_name,
                            "raw_value": _strip_value_noise(raw_value),
                            "page_num": line["page_num"],
                            "source": source,
                            "label_text": label_text,
                        }
                    )

        checkbox_items.extend(_extract_checkboxes_from_line(line))

    selected_options = sorted(
        {
            item["normalized_label"]
            for item in checkbox_items
            if item["checked"] and item["normalized_label"]
        }
    )

    if "gender" not in detail_fields:
        for option in selected_options:
            if option in {"male", "female"}:
                detail_fields["gender"] = {
                    "value": option,
                    "raw_value": option,
                    "page_num": checkbox_items[0]["page_num"] if checkbox_items else 1,
                    "source": "checkbox",
                    "label": option,
                }
                break

    normalized_form = {
        "name": detail_fields.get("name", {}).get("value", ""),
        "date": detail_fields.get("date", {}).get("value", ""),
        "address": detail_fields.get("address", {}).get("value", ""),
        "phone": detail_fields.get("phone", {}).get("value", ""),
        "email": detail_fields.get("email", {}).get("value", ""),
        "id_number": detail_fields.get("id_number", {}).get("value", ""),
        "gender": detail_fields.get("gender", {}).get("value", ""),
        "selected_options": selected_options,
    }

    filled_fields = sum(1 for field_name in FORM_FIELD_ORDER if normalized_form[field_name])
    return {
        "schema_version": FORM_SCHEMA_VERSION,
        "document_type": "form",
        "source_file": ocr_result.get("source_file", ""),
        "normalized_form": normalized_form,
        "fields": {
            field_name: detail_fields.get(
                field_name,
                {
                    "value": "",
                    "raw_value": "",
                    "page_num": None,
                    "source": "",
                    "label": "",
                },
            )
            for field_name in FORM_FIELD_ORDER
        },
        "checkboxes": checkbox_items,
        "raw_key_values": raw_key_values,
        "analysis": {
            "field_count": filled_fields,
            "selected_option_count": len(selected_options),
            "checkbox_count": len(checkbox_items),
        },
    }


def write_form_json(form_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(form_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
