from __future__ import annotations

"""Layout Reader 基础能力。

第 2 步负责标题、段落、列表、页眉页脚过滤和阅读顺序恢复，最终输出层级 Markdown。
"""

from collections import defaultdict
from functools import cmp_to_key
import math
from pathlib import Path
from statistics import median
from typing import Any
import re

_LAYOUT_TOP_MARGIN_RATIO = 0.12
_LAYOUT_BOTTOM_MARGIN_RATIO = 0.10
_LAYOUT_FULL_WIDTH_RATIO = 0.68
_LAYOUT_HEADING_MAX_WORDS = 18
_LIST_BULLET_RE = re.compile(r"^(?P<marker>[-*•·●○◦])\s*")
_LIST_ORDERED_RE = re.compile(
    r"^(?P<marker>(?:\(\d+\)|\d+[.)]|[A-Za-z][.)]|[一二三四五六七八九十]+[、.]|（[一二三四五六七八九十]+）))\s*"
)
_NUMBERED_HEADING_RE = re.compile(r"^(?P<num>\d+(?:\.\d+){0,3})[.)]?\s+")
_CHINESE_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千0-9]+[章节部分篇]\s*")
_CHINESE_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+[、.]\s*")
_CHINESE_SUBSECTION_RE = re.compile(r"^（[一二三四五六七八九十]+）\s*")
_PAGE_NUMBER_RE = re.compile(
    r"^(?:page\s+\d+(?:\s+of\s+\d+)?|\d+|[-–—]?\s*\d+\s*[-–—]?|第\s*\d+\s*页)$",
    re.IGNORECASE,
)


def _normalize_layout_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize_margin_text(text: str) -> str:
    normalized = _normalize_layout_text(text).casefold()
    normalized = re.sub(r"\d+", "#", normalized)
    return normalized


def _safe_median(values: list[float], default: float) -> float:
    cleaned = [value for value in values if value > 0]
    if not cleaned:
        return default
    return float(median(cleaned))


def _is_probable_page_number_text(text: str) -> bool:
    normalized = _normalize_layout_text(text)
    if not normalized:
        return False
    return bool(_PAGE_NUMBER_RE.fullmatch(normalized.casefold()))


def _line_margin_zone(line: dict[str, Any], page_height: int) -> str | None:
    box = line["bbox"]
    if box["top"] <= page_height * _LAYOUT_TOP_MARGIN_RATIO:
        return "top"
    if box["bottom"] >= page_height * (1 - _LAYOUT_BOTTOM_MARGIN_RATIO):
        return "bottom"
    return None


def _detect_repeated_margin_keys(pages: list[dict[str, Any]]) -> set[tuple[str, str]]:
    occurrences: dict[tuple[str, str], set[int]] = defaultdict(set)
    if len(pages) < 2:
        return set()

    for page in pages:
        page_height = page["image_height"]
        for line in page["lines"]:
            zone = _line_margin_zone(line, page_height)
            text = _normalize_layout_text(line["text"])
            if not zone or not text or len(text) > 120:
                continue
            occurrences[(zone, _canonicalize_margin_text(text))].add(page["page_num"])

    repeat_threshold = max(2, math.ceil(len(pages) * 0.5))
    return {
        key
        for key, page_nums in occurrences.items()
        if len(page_nums) >= repeat_threshold
    }


def _is_margin_line(
        line: dict[str, Any],
        page_height: int,
        repeated_margin_keys: set[tuple[str, str]],
) -> bool:
    zone = _line_margin_zone(line, page_height)
    if not zone:
        return False

    text = _normalize_layout_text(line["text"])
    if not text:
        return True
    if _is_probable_page_number_text(text):
        return True
    return (zone, _canonicalize_margin_text(text)) in repeated_margin_keys


def _group_lines_to_blocks(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float, int], list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        key = (
            line.get("source", "primary"),
            float(line.get("angle", 0.0)),
            line["block_num"],
        )
        groups[key].append(line)

    blocks: list[dict[str, Any]] = []
    for items in groups.values():
        ordered = sorted(items, key=lambda item: (item["bbox"]["top"], item["bbox"]["left"]))
        left = min(item["bbox"]["left"] for item in ordered)
        top = min(item["bbox"]["top"] for item in ordered)
        right = max(item["bbox"]["right"] for item in ordered)
        bottom = max(item["bbox"]["bottom"] for item in ordered)
        blocks.append(
            {
                "page_num": ordered[0]["page_num"],
                "bbox": {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                    "right": right,
                    "bottom": bottom,
                },
                "source": ordered[0].get("source", "primary"),
                "angle": ordered[0].get("angle", 0.0),
                "block_num": ordered[0]["block_num"],
                "lines": ordered,
            }
        )
    return blocks


def _block_is_full_width(block: dict[str, Any], page_width: int) -> bool:
    return block["bbox"]["width"] >= page_width * _LAYOUT_FULL_WIDTH_RATIO


def _detect_column_layout(
        blocks: list[dict[str, Any]],
        *,
        page_width: int,
        median_line_height: float,
) -> tuple[int, float]:
    narrow_blocks = [block for block in blocks if not _block_is_full_width(block, page_width)]
    if len(narrow_blocks) < 2:
        return 1, page_width / 2

    left_blocks = [block for block in narrow_blocks if block["bbox"]["right"] <= page_width * 0.68]
    right_blocks = [block for block in narrow_blocks if block["bbox"]["left"] >= page_width * 0.32]
    if not left_blocks or not right_blocks:
        return 1, page_width / 2

    left_edge = max(block["bbox"]["right"] for block in left_blocks)
    right_edge = min(block["bbox"]["left"] for block in right_blocks)
    if right_edge - left_edge < page_width * 0.08:
        return 1, page_width / 2

    overlap_tolerance = max(12.0, median_line_height * 0.8)
    has_vertical_overlap = any(
        not (
                left["bbox"]["bottom"] <= right["bbox"]["top"] + overlap_tolerance
                or right["bbox"]["bottom"] <= left["bbox"]["top"] + overlap_tolerance
        )
        for left in left_blocks
        for right in right_blocks
    )
    if not has_vertical_overlap:
        return 1, page_width / 2

    return 2, (left_edge + right_edge) / 2


def _block_column_index(block: dict[str, Any], *, page_width: int, column_boundary: float) -> int:
    if _block_is_full_width(block, page_width):
        return -1
    center_x = block["bbox"]["left"] + block["bbox"]["width"] / 2
    return 0 if center_x < column_boundary else 1


def _compare_blocks_for_reading(
        first: dict[str, Any],
        second: dict[str, Any],
        *,
        page_width: int,
        column_count: int,
        column_boundary: float,
        median_line_height: float,
) -> int:
    tolerance = max(8.0, median_line_height * 0.6)
    first_box = first["bbox"]
    second_box = second["bbox"]

    if first_box["bottom"] <= second_box["top"] + tolerance:
        return -1
    if second_box["bottom"] <= first_box["top"] + tolerance:
        return 1

    if column_count > 1:
        first_column = _block_column_index(first, page_width=page_width, column_boundary=column_boundary)
        second_column = _block_column_index(second, page_width=page_width, column_boundary=column_boundary)
        if first_column != second_column:
            if first_column == -1 or second_column == -1:
                if first_box["top"] != second_box["top"]:
                    return -1 if first_box["top"] < second_box["top"] else 1
            else:
                return -1 if first_column < second_column else 1

    if first_box["top"] != second_box["top"]:
        return -1 if first_box["top"] < second_box["top"] else 1
    if first_box["left"] != second_box["left"]:
        return -1 if first_box["left"] < second_box["left"] else 1
    return 0


def _join_segment_lines(lines: list[dict[str, Any]]) -> str:
    merged = ""
    for line in lines:
        text = _normalize_layout_text(line["text"])
        if not text:
            continue
        if not merged:
            merged = text
            continue
        if merged.endswith("-") and text[:1].islower():
            merged = merged[:-1] + text
            continue
        merged = f"{merged} {text}"
    return merged.strip()


def _build_segment_from_lines(
        lines: list[dict[str, Any]],
        *,
        page_width: int,
        page_height: int,
        column_index: int,
) -> dict[str, Any]:
    left = min(line["bbox"]["left"] for line in lines)
    top = min(line["bbox"]["top"] for line in lines)
    right = max(line["bbox"]["right"] for line in lines)
    bottom = max(line["bbox"]["bottom"] for line in lines)
    heights = [line["bbox"]["height"] for line in lines]
    text = _join_segment_lines(lines)
    center_x = (left + right) / 2
    return {
        "page_num": lines[0]["page_num"],
        "text": text,
        "line_count": len(lines),
        "word_count": sum(len(line.get("words", [])) for line in lines),
        "bbox": {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
            "right": right,
            "bottom": bottom,
        },
        "avg_height": sum(heights) / len(heights),
        "max_height": max(heights),
        "column_index": column_index,
        "is_centered": abs(center_x - (page_width / 2)) <= page_width * 0.12,
        "near_top": top <= page_height * 0.22,
        "par_num": lines[0]["par_num"],
        "lines": lines,
    }


def _match_list_prefix(text: str) -> dict[str, Any] | None:
    normalized = _normalize_layout_text(text)
    for pattern, ordered in ((_LIST_BULLET_RE, False), (_LIST_ORDERED_RE, True)):
        match = pattern.match(normalized)
        if not match:
            continue
        marker = match.group("marker")
        content = normalized[match.end():].strip()
        if not content:
            return None
        return {
            "ordered": ordered,
            "marker": marker,
            "content": content,
        }
    return None


def _extract_heading_level_from_numbering(text: str) -> int | None:
    normalized = _normalize_layout_text(text)
    match = _NUMBERED_HEADING_RE.match(normalized)
    if match:
        dot_count = match.group("num").count(".")
        return min(4, 2 + dot_count)
    if _CHINESE_CHAPTER_RE.match(normalized):
        return 1
    if _CHINESE_SECTION_RE.match(normalized):
        return 2
    if _CHINESE_SUBSECTION_RE.match(normalized):
        return 3
    return None


def _build_segments_from_block(
        block: dict[str, Any],
        *,
        page_width: int,
        page_height: int,
        median_line_height: float,
        column_index: int,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_lines: list[dict[str, Any]] = []

    for line in block["lines"]:
        text = _normalize_layout_text(line["text"])
        if not text:
            continue
        if not current_lines:
            current_lines = [line]
            continue

        previous = current_lines[-1]
        vertical_gap = line["bbox"]["top"] - previous["bbox"]["bottom"]
        first_line = current_lines[0]
        same_paragraph = line["par_num"] == previous["par_num"]
        starts_new_list = _match_list_prefix(text) is not None
        current_is_list = _match_list_prefix(first_line["text"]) is not None
        indent_delta = abs(line["bbox"]["left"] - first_line["bbox"]["left"])
        max_gap = max(median_line_height * 1.1, previous["bbox"]["height"] * 0.9 + 6)

        if starts_new_list and current_lines:
            segments.append(
                _build_segment_from_lines(
                    current_lines,
                    page_width=page_width,
                    page_height=page_height,
                    column_index=column_index,
                )
            )
            current_lines = [line]
            continue

        should_merge = (
                same_paragraph
                and vertical_gap <= max_gap
                and (indent_delta <= page_width * 0.04 or current_is_list)
        )
        if should_merge:
            current_lines.append(line)
            continue

        segments.append(
            _build_segment_from_lines(
                current_lines,
                page_width=page_width,
                page_height=page_height,
                column_index=column_index,
            )
        )
        current_lines = [line]

    if current_lines:
        segments.append(
            _build_segment_from_lines(
                current_lines,
                page_width=page_width,
                page_height=page_height,
                column_index=column_index,
            )
        )

    return segments


def _classify_segment(
        segment: dict[str, Any],
        *,
        base_line_height: float,
        is_first_document_segment: bool,
) -> dict[str, Any] | None:
    text = _normalize_layout_text(segment["text"])
    if not text:
        return None

    line_height_ratio = segment["avg_height"] / max(base_line_height, 1.0)
    word_count = segment["word_count"] or len(text.split())
    short_candidate = (
            segment["line_count"] <= 2
            and word_count <= _LAYOUT_HEADING_MAX_WORDS
            and len(text) <= 120
    )
    ends_like_sentence = text.endswith((".", "!", "?", "。", "！", "？", ";", "；"))
    heading_level = _extract_heading_level_from_numbering(text)

    if heading_level and short_candidate and not ends_like_sentence:
        return {
            "type": "heading",
            "level": heading_level,
            "text": text,
            "page_num": segment["page_num"],
        }

    if short_candidate and not ends_like_sentence:
        if is_first_document_segment and segment["near_top"] and (
                line_height_ratio >= 1.35 or segment["is_centered"]
        ):
            return {
                "type": "heading",
                "level": 1,
                "text": text,
                "page_num": segment["page_num"],
            }
        if line_height_ratio >= 1.7:
            return {
                "type": "heading",
                "level": 2,
                "text": text,
                "page_num": segment["page_num"],
            }
        if line_height_ratio >= 1.4:
            return {
                "type": "heading",
                "level": 3,
                "text": text,
                "page_num": segment["page_num"],
            }

    list_match = _match_list_prefix(text)
    if list_match:
        return {
            "type": "list_item",
            "ordered": list_match["ordered"],
            "text": list_match["content"],
            "page_num": segment["page_num"],
        }

    return {
        "type": "paragraph",
        "text": text,
        "page_num": segment["page_num"],
    }


def _render_layout_items_to_markdown(items: list[dict[str, Any]]) -> str:
    if not items:
        return "_No structured text detected._"

    lines: list[str] = []
    previous_type: str | None = None

    for item in items:
        item_type = item["type"]
        if item_type == "heading":
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{'#' * item['level']} {item['text']}")
            lines.append("")
        elif item_type == "list_item":
            if previous_type not in {"list_item"} and lines and lines[-1] != "":
                lines.append("")
            prefix = "1." if item.get("ordered") else "-"
            lines.append(f"{prefix} {item['text']}")
        else:
            if previous_type == "list_item":
                lines.append("")
            elif lines and lines[-1] != "":
                lines.append("")
            lines.append(item["text"])
            lines.append("")

        previous_type = item_type

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) or "_No structured text detected._"


def _analyze_document_layout(pages: list[dict[str, Any]]) -> dict[str, Any]:
    repeated_margin_keys = _detect_repeated_margin_keys(pages)
    content_lines_by_page: dict[int, list[dict[str, Any]]] = {}
    filtered_margin_lines_by_page: dict[int, list[dict[str, Any]]] = {}
    all_content_line_heights: list[float] = []

    for page in pages:
        page_height = page["image_height"]
        content_lines: list[dict[str, Any]] = []
        filtered_margin_lines: list[dict[str, Any]] = []
        for line in page["lines"]:
            if _is_margin_line(line, page_height, repeated_margin_keys):
                filtered_margin_lines.append(line)
                continue
            content_lines.append(line)
            all_content_line_heights.append(line["bbox"]["height"])

        content_lines_by_page[page["page_num"]] = content_lines
        filtered_margin_lines_by_page[page["page_num"]] = filtered_margin_lines

    base_line_height = _safe_median(all_content_line_heights, 18.0)
    page_layouts: list[dict[str, Any]] = []
    document_items: list[dict[str, Any]] = []
    is_first_document_segment = True

    for page in pages:
        page_num = page["page_num"]
        content_lines = content_lines_by_page[page_num]
        filtered_margin_lines = filtered_margin_lines_by_page[page_num]
        blocks = _group_lines_to_blocks(content_lines)
        column_count, column_boundary = _detect_column_layout(
            blocks,
            page_width=page["image_width"],
            median_line_height=base_line_height,
        )
        ordered_blocks = sorted(
            blocks,
            key=cmp_to_key(
                lambda first, second: _compare_blocks_for_reading(
                    first,
                    second,
                    page_width=page["image_width"],
                    column_count=column_count,
                    column_boundary=column_boundary,
                    median_line_height=base_line_height,
                )
            ),
        )

        page_items: list[dict[str, Any]] = []
        for block in ordered_blocks:
            column_index = (
                0
                if column_count == 1
                else _block_column_index(
                    block,
                    page_width=page["image_width"],
                    column_boundary=column_boundary,
                )
            )
            segments = _build_segments_from_block(
                block,
                page_width=page["image_width"],
                page_height=page["image_height"],
                median_line_height=base_line_height,
                column_index=column_index,
            )
            for segment in segments:
                item = _classify_segment(
                    segment,
                    base_line_height=base_line_height,
                    is_first_document_segment=is_first_document_segment,
                )
                if item is None:
                    continue
                page_items.append(item)
                document_items.append(item)
                is_first_document_segment = False

        page_layouts.append(
            {
                "page_num": page_num,
                "items": page_items,
                "content_markdown": _render_layout_items_to_markdown(page_items),
                "stats": {
                    "column_count": column_count,
                    "filtered_margin_line_count": len(filtered_margin_lines),
                },
            }
        )

    document_markdown = _render_layout_items_to_markdown(document_items)
    return {
        "pages": page_layouts,
        "document_markdown": document_markdown,
        "stats": {
            "base_line_height": round(base_line_height, 2),
            "repeated_margin_key_count": len(repeated_margin_keys),
        },
    }


def _build_page_markdown(
        page_result: dict[str, Any],
        source_file: str,
        source_kind: str,
        *,
        content_markdown: str,
        layout_stats: dict[str, Any],
        table_summaries: list[dict[str, Any]],
) -> str:
    """生成按页导出的 Markdown 文本。"""
    structured_body = content_markdown.strip() or "_No structured text detected._"
    artifact_lines = [
        f"- [Page image](../pages/{page_result['image_path']})",
        f"- [Overlay image](../overlays/{page_result['overlay_path']})",
        f"- [Plain text](../texts/{page_result['text_path']})",
    ]
    for table in table_summaries:
        artifact_lines.append(
            f"- [Table {table['table_id']} CSV](../tables/{table['csv_path']}) · "
            f"[HTML](../tables/{table['html_path']})"
        )

    return "\n".join(
        [
            f"# OCR Page {page_result['page_num']}",
            "",
            "## Metadata",
            f"- Source file: `{source_file}`",
            f"- Source kind: `{source_kind}`",
            f"- Image size: `{page_result['image_width']} x {page_result['image_height']}`",
            f"- Word count: `{len(page_result['words'])}`",
            f"- Rejected graphic-like word count: `{len(page_result.get('rejected_words', []))}`",
            f"- Line count: `{len(page_result['lines'])}`",
            f"- Filtered header/footer line count: `{layout_stats.get('filtered_margin_line_count', 0)}`",
            f"- Detected column count: `{layout_stats.get('column_count', 1)}`",
            f"- Detected table count: `{len(table_summaries)}`",
            "",
            "## Artifacts",
            *artifact_lines,
            "",
            "## Structured OCR Markdown",
            "",
            structured_body,
            "",
        ]
    )


def _build_document_markdown(source_file: str, page_layouts: list[dict[str, Any]]) -> str:
    content_parts: list[str] = []
    for page_layout in page_layouts:
        page_body = page_layout["content_markdown"].strip()
        if not page_body or page_body == "_No structured text detected._":
            continue
        if content_parts:
            content_parts.extend(["", "<!-- page-break -->", ""])
        content_parts.append(page_body)

    body = "\n".join(content_parts).strip()
    if not body:
        return f"# {Path(source_file).stem}\n\n_No structured text detected._\n"
    if not body.startswith("#"):
        body = f"# {Path(source_file).stem}\n\n{body}"
    return f"{body}\n"
