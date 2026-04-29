from __future__ import annotations

"""OCR 核心流程。

这个模块负责：
1. 将 PDF 或图片整理成页面图片；
2. 调用 Tesseract OCR 获取词级结果；
3. 按行聚合，生成 line 级 bbox；
4. 绘制叠框图；
5. 导出 ocr.json、纯文本和按页 Markdown。
"""

from collections import Counter, defaultdict
import contextlib
import csv
from datetime import datetime, timezone
from functools import cmp_to_key
from html import escape as html_escape
import io
from pathlib import Path
from statistics import median
from typing import Any
import json
import math
import re
import shlex

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
import pymupdf
import pytesseract
from pytesseract import Output

from ocr_engine_4_json import build_form_to_json_result, build_skipped_form_result, write_form_json
from ocr_engine_5_receipt import (
    build_receipt_invoice_result,
    build_skipped_receipt_invoice_result,
    write_receipt_invoice_json,
)
from ocr_engine_6_router import build_mixed_document_router_result, write_document_router_json
from ocr_engine_7_bundle_splitter import build_bundle_splitter_result, write_bundle_splitter_json
from ocr_engine_8_review import (
    build_signature_handwriting_review_result,
    write_review_json,
    write_review_overlays,
)
from ocr_engine_9_query import build_query_extractor_result, write_query_json
from ocr_engine_10_contract_schema import build_contract_schema_result, write_contract_schema_json
from ocr_engine_11_consolidator import build_multi_page_consolidation_result, write_multi_page_consolidation_json
from ocr_engine_12_review_workbench import initialize_review_workbench_revisions
from ocr_engine_13_chunker import build_layout_aware_chunk_result, write_layout_chunks_json
from ocr_engine_14_direct_pdf_structurer import build_direct_pdf_structure_result, write_direct_pdf_structure_json
from ocr_engine_15_evidence_qa import build_evidence_qa_result, write_evidence_qa_json
from ocr_engine_16_complex_page_analyst import build_complex_page_analysis_result, write_complex_page_analysis_json
from ocr_engine_17_robustness_lab import build_robustness_lab_result, write_degradation_report_json
from ocr_engine_18_copilot import (
    build_document_ai_copilot_result,
    write_document_ai_copilot_json,
    write_document_ai_copilot_markdown,
)

# 默认 OCR 配置：
# --oem 3: 使用默认 OCR 引擎模式
# --psm 3: 自动页面分割，适合大多数整页文档
DEFAULT_TESSERACT_CONFIG = "--oem 3 --psm 3 -c preserve_interword_spaces=1"
DEFAULT_PREPROCESS_MODE = "clean"
DEFAULT_OCR_PADDING = 24
DEFAULT_ENABLE_ROTATED_TEXT = False
DEFAULT_SUPPLEMENTAL_MIN_CONFIDENCE = 45.0
DEFAULT_ROTATED_MIN_CONFIDENCE = 70.0
ROTATED_TEXT_ANGLES = (45, -45)
SUPPORTED_PREPROCESS_MODES = {"none", "clean", "binary"}
_CIRCLE_LIKE_TEXT = {"o", "O", "0", "○", "◯", "●", "◦"}
_LINE_ARTIFACT_CHARS = set("|_-=—–")
_LAYOUT_TOP_MARGIN_RATIO = 0.12
_LAYOUT_BOTTOM_MARGIN_RATIO = 0.10
_LAYOUT_FULL_WIDTH_RATIO = 0.68
_LAYOUT_HEADING_MAX_WORDS = 18
_TABLE_MIN_ROWS = 2
_TABLE_MIN_COLS = 2
_TABLE_ROW_GAP_RATIO = 2.2
_TABLE_COLUMN_TOLERANCE_RATIO = 0.025
_TABLE_CELL_GAP_RATIO = 1.7
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


# PDF 处理
def render_pdf_to_images(pdf_path: Path, images_dir: Path, dpi: int = 300) -> list[Path]:
    """将 PDF 的每一页渲染成 PNG 图片。"""
    images_dir.mkdir(parents=True, exist_ok=True)  # 创建目录及其所有必要的父级目录

    page_image_paths: list[Path] = []
    doc = pymupdf.open(pdf_path)
    try:
        zoom = dpi / 72.0   # 计算缩放比例（PDF默认72 DPI）
        matrix = pymupdf.Matrix(zoom, zoom)  # 创建变换矩阵

        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = images_dir / f"page_{page_index + 1:03d}.png"
            pix.save(str(image_path))
            page_image_paths.append(image_path)
    finally:
        doc.close()

    return page_image_paths  # 返回图片路径列表


# 单张图片处理
def render_image_to_page(image_path: Path, images_dir: Path) -> list[Path]:
    """把单张图片整理成与 PDF 页图一致的输出格式。"""
    images_dir.mkdir(parents=True, exist_ok=True)

    output_path = images_dir / "page_001.png"
    with Image.open(image_path) as image:
        # 根据图片的 EXIF 元数据中的方向标记（Orientation Tag），自动将图片旋转到正确的显示方向
        normalized = ImageOps.exif_transpose(image).convert("RGB")  # <class 'PIL.Image.Image'>
        normalized.save(output_path)

    return [output_path]


# 将处理 PDF 和 单张图片统一
def prepare_page_images(
        source_path: Path,
        images_dir: Path,
        source_kind: str,
        dpi: int = 300,
) -> list[Path]:
    """根据源文件类型准备统一的页图列表。"""
    if source_kind == "pdf":
        return render_pdf_to_images(source_path, images_dir=images_dir, dpi=dpi)
    if source_kind == "image":
        return render_image_to_page(source_path, images_dir=images_dir)
    raise ValueError(f"不支持的 source_kind: {source_kind}")


def _normalize_preprocess_mode(preprocess_mode: str) -> str:
    """
    # 示例数据流：
    输入: "BINARY"   → 结果: "binary"
    输入: "Clean"    → 结果: "clean"
    输入: "NONE"     → 结果: "none"
    输入: "ClEaN"    → 结果: "clean"
    """
    mode = (preprocess_mode or DEFAULT_PREPROCESS_MODE).strip().lower()
    return mode if mode in SUPPORTED_PREPROCESS_MODES else DEFAULT_PREPROCESS_MODE  # 如果 mode 是非法值，则返回默认值


def _otsu_threshold(image: Image.Image) -> int:
    """用 Otsu 阈值给二值化模式自动选择分割点。"""
    histogram = image.histogram()
    total = sum(histogram)
    if total <= 0:
        return 180

    total_sum = sum(value * count for value, count in enumerate(histogram))
    # total_sum = Σ(灰度值 × 该灰度的像素数) = 0×histogram[0] + 1×histogram[1] + ... + 255×histogram[255]
    #
    # 物理意义：图像中所有像素的灰度值之和
    # 用途：后续用于快速计算前景的平均灰度（通过减法而非重新累加）
    #
    # 例如：如果有 100 个像素灰度为 50，贡献为 50×100 = 5000
    background_sum = 0.0  # 背景部分的灰度总和（累加器）
    background_weight = 0  # 背景部分的像素数量（累加器)
    best_threshold = 180  # 记录最佳阈值（初始化为默认值）
    best_variance = -1.0  # 记录最大类间方差（初始化为负数，确保第一次比较会更新）

    for value, count in enumerate(histogram):
        # value: 当前候选阈值（也是当前灰度级，0-255）
        # count: 灰度值为 value 的像素数量
        background_weight += count
        # background_weight = 灰度值 <= value 的所有像素数量
        # 随着 value 从 0 增加到 255，background_weight 逐渐增大
        #
        # 例如：
        # value=0 时: background_weight = histogram[0]
        # value=1 时: background_weight = histogram[0] + histogram[1]
        # value=2 时: background_weight = histogram[0] + histogram[1] + histogram[2]

        # 如果当前灰度级没有像素，跳过（避免除以零）
        if background_weight == 0:
            continue

        foreground_weight = total - background_weight
        # foreground_weight = 灰度值 > value 的像素数量
        #
        # 逻辑：总像素数 - 背景像素数 = 前景像素数
        #
        # 例如：total=10000, background_weight=3000
        #       → foreground_weight = 7000

        # 如果前景没有像素了，说明阈值已经太大，停止遍历
        if foreground_weight == 0:
            break   # 所有像素都被分到背景中，继续遍历没有意义

        background_sum += value * count
        # background_sum = 背景部分（灰度 <= value）所有像素的灰度值之和
        #
        # 例如：
        # value=0 时: background_sum = 0 × histogram[0]
        # value=1 时: background_sum = 0×histogram[0] + 1×histogram[1]
        # value=2 时: background_sum = 0×h[0] + 1×h[1] + 2×h[2]
        background_mean = background_sum / background_weight
        # background_mean (μ₀) = 背景部分的平均灰度值
        #
        # 例如：background_sum=150000, background_weight=3000
        #       → background_mean = 50.0（背景偏暗）
        foreground_mean = (total_sum - background_sum) / foreground_weight
        # foreground_mean (μ₁) = 前景部分的平均灰度值
        #
        # 技巧：利用 total_sum - background_sum 快速得到前景灰度总和
        # 而不是重新遍历前景像素累加
        #
        # 例如：total_sum=2000000, background_sum=150000
        #       → foreground_sum = 1850000
        #       → foreground_mean = 1850000 / 7000 ≈ 264.3（前景偏亮）
        between_variance = (
                background_weight
                * foreground_weight
                * (background_mean - foreground_mean)
                * (background_mean - foreground_mean)
        )
        # 公式：σ²_B = w₀ × w₁ × (μ₀ - μ₁)²
        #
        # 各项含义：
        # - w₀ (background_weight):     背景像素数量（权重）
        # - w₁ (foreground_weight):     前景像素数量（权重）
        # - μ₀ (background_mean):       背景平均灰度
        # - μ₁ (foreground_mean):       前景平均灰度
        # - (μ₀ - μ₁)²:                 两类均值差的平方
        #
        # 物理意义：
        # - 方差越大 → 背景和前景差异越明显 → 分割效果越好
        # - 方差越小 → 背景和前景混杂 → 分割效果差
        #
        # 直观理解：
        # 1. 如果背景和前景都很"纯净"（均值差异大），方差大 ✓
        # 2. 如果背景和前景都很"混杂"（均值接近），方差小 ✗
        # 3. 如果某一类像素很少（权重小），方差也会小（惩罚不平衡分割）

        if between_variance > best_variance:
            # 如果当前阈值的类间方差比之前记录的最大值还大
            best_variance = between_variance  # 更新最大方差
            best_threshold = value  # 更新最佳阈值

    return best_threshold
    # 例如：返回 128
    # 意味着：
    # - 灰度值 < 128 的像素 → 判定为"背景" → 二值化后变为黑色 (0)
    # - 灰度值 >= 128 的像素 → 判定为"前景" → 二值化后变为白色 (255)
    #
    # 这个阈值会被上层函数用于图像二值化：
    # grayscale.point(lambda pixel: 255 if pixel > threshold else 0)


def _preprocess_for_ocr(image: Image.Image, preprocess_mode: str) -> Image.Image:
    """生成 OCR 专用图片，保持尺寸不变，保证 bbox 能映射回原页图。"""
    mode = _normalize_preprocess_mode(preprocess_mode)
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    if mode == "none":
        return normalized

    grayscale = ImageOps.grayscale(normalized)
    grayscale = ImageOps.autocontrast(grayscale, cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.3)

    if mode == "binary":
        threshold = _otsu_threshold(grayscale)
        return grayscale.point(lambda pixel: 255 if pixel > threshold else 0).convert("RGB")

    return grayscale.filter(ImageFilter.SHARPEN).convert("RGB")


def _expand_for_ocr(image: Image.Image, padding: int) -> Image.Image:
    padding = max(0, int(padding))
    if padding <= 0:
        return image
    return ImageOps.expand(image, border=padding, fill="white")


def _config_with_psm(config: str, psm: int) -> str:
    """替换 Tesseract 配置中的 PSM，避免追加多个互相冲突的 --psm。"""
    tokens = shlex.split(config or "")
    cleaned: list[str] = []
    skip_next = False

    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == "--psm":
            skip_next = True
            continue
        if token.startswith("--psm="):
            continue
        cleaned.append(token)

    cleaned.extend(["--psm", str(psm)])
    return shlex.join(cleaned)


def _safe_float(value: Any, default: float = -1.0) -> float:
    """将 Tesseract 返回的 conf 等字段安全转成 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _axis_bbox(
        left: float,
        top: float,
        right: float,
        bottom: float,
        image_size: tuple[int, int],
) -> dict[str, Any] | None:
    image_width, image_height = image_size
    clipped_left = max(0, min(image_width, int(round(left))))
    clipped_top = max(0, min(image_height, int(round(top))))
    clipped_right = max(0, min(image_width, int(round(right))))
    clipped_bottom = max(0, min(image_height, int(round(bottom))))

    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return None

    return {
        "left": clipped_left,
        "top": clipped_top,
        "width": clipped_right - clipped_left,
        "height": clipped_bottom - clipped_top,
        "right": clipped_right,
        "bottom": clipped_bottom,
    }


def _bbox_from_points(
        points: list[tuple[float, float]],
        image_size: tuple[int, int],
) -> dict[str, Any] | None:
    bbox = _axis_bbox(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
        image_size,
    )
    if not bbox:
        return None

    image_width, image_height = image_size
    bbox["points"] = [
        {
            "x": max(0, min(image_width, int(round(x)))),
            "y": max(0, min(image_height, int(round(y)))),
        }
        for x, y in points
    ]
    return bbox


def _map_padded_bbox(
        left: int,
        top: int,
        width: int,
        height: int,
        *,
        padding: int,
        image_size: tuple[int, int],
) -> dict[str, Any] | None:
    return _axis_bbox(
        left - padding,
        top - padding,
        left + width - padding,
        top + height - padding,
        image_size,
    )


def _rotated_point_to_original(
        x: float,
        y: float,
        *,
        rotated_size: tuple[int, int],
        original_size: tuple[int, int],
        angle: float,
) -> tuple[float, float]:
    """把旋转后图片上的点反算回原图坐标。"""
    rotated_width, rotated_height = rotated_size
    original_width, original_height = original_size
    theta = math.radians(angle)
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)

    dx = x - rotated_width / 2
    dy = y - rotated_height / 2
    original_x = cos_theta * dx - sin_theta * dy + original_width / 2
    original_y = sin_theta * dx + cos_theta * dy + original_height / 2
    return original_x, original_y


def _map_rotated_bbox(
        left: int,
        top: int,
        width: int,
        height: int,
        *,
        padding: int,
        rotated_size: tuple[int, int],
        original_size: tuple[int, int],
        angle: float,
) -> dict[str, Any] | None:
    unpadded_left = left - padding
    unpadded_top = top - padding
    unpadded_right = left + width - padding
    unpadded_bottom = top + height - padding
    rotated_points = [
        (unpadded_left, unpadded_top),
        (unpadded_right, unpadded_top),
        (unpadded_right, unpadded_bottom),
        (unpadded_left, unpadded_bottom),
    ]
    original_points = [
        _rotated_point_to_original(
            x,
            y,
            rotated_size=rotated_size,
            original_size=original_size,
            angle=angle,
        )
        for x, y in rotated_points
    ]
    return _bbox_from_points(original_points, original_size)


def _extract_words_from_tesseract_dict(
        data: dict[str, list[Any]],
        page_num: int,
        *,
        bbox_mapper: Any,
        source: str,
        angle: float = 0.0,
        min_confidence: float = -1.0,
) -> list[dict[str, Any]]:
    """从 pytesseract.image_to_data 的字典结果中提取词级记录。"""
    words: list[dict[str, Any]] = []
    total_items = len(data.get("text", []))

    for i in range(total_items):
        text = str(data["text"][i]).strip()
        if not text:
            continue

        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        conf = _safe_float(data["conf"][i])
        if min_confidence >= 0 and conf < min_confidence:
            continue

        bbox = bbox_mapper(left, top, width, height)
        if not bbox:
            continue

        words.append(
            {
                "page_num": page_num,
                "text": text,
                "confidence": conf,
                "bbox": bbox,
                "block_num": int(data["block_num"][i]),
                "par_num": int(data["par_num"][i]),
                "line_num": int(data["line_num"][i]),
                "word_num": int(data["word_num"][i]),
                "source": source,
                "angle": angle,
            }
        )

    return words


def _group_words_to_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同一行的词聚合成 line 级结果。"""
    groups: dict[tuple[str, float, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        key = (
            word.get("source", "primary"),
            float(word.get("angle", 0.0)),
            word["block_num"],
            word["par_num"],
            word["line_num"],
        )
        groups[key].append(word)

    lines: list[dict[str, Any]] = []
    for items in groups.values():
        items = sorted(items, key=lambda x: x["word_num"])

        left = min(item["bbox"]["left"] for item in items)
        top = min(item["bbox"]["top"] for item in items)
        right = max(item["bbox"]["right"] for item in items)
        bottom = max(item["bbox"]["bottom"] for item in items)

        text = " ".join(item["text"] for item in items)
        confidences = [item["confidence"] for item in items if item["confidence"] >= 0]
        avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else -1.0

        lines.append(
            {
                "page_num": items[0]["page_num"],
                "text": text,
                "confidence": avg_conf,
                "bbox": {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                    "right": right,
                    "bottom": bottom,
                },
                "block_num": items[0]["block_num"],
                "par_num": items[0]["par_num"],
                "line_num": items[0]["line_num"],
                "source": items[0].get("source", "primary"),
                "angle": items[0].get("angle", 0.0),
                "words": [item["text"] for item in items],
            }
        )

    lines.sort(key=lambda x: (x["bbox"]["top"], x["bbox"]["left"]))
    return lines


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


def _words_to_text(words: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for word in words:
        text = _normalize_layout_text(word["text"])
        if not text:
            continue
        if parts and parts[-1].endswith("-") and text[:1].islower():
            parts[-1] = parts[-1][:-1] + text
            continue
        parts.append(text)
    return " ".join(parts).strip()


def _bbox_from_word_items(words: list[dict[str, Any]]) -> dict[str, Any]:
    left = min(word["bbox"]["left"] for word in words)
    top = min(word["bbox"]["top"] for word in words)
    right = max(word["bbox"]["right"] for word in words)
    bottom = max(word["bbox"]["bottom"] for word in words)
    return {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
        "right": right,
        "bottom": bottom,
    }


def _build_table_segment(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "text": _words_to_text(words),
        "bbox": _bbox_from_word_items(words),
        "word_count": len(words),
    }


def _build_candidate_table_row(words: list[dict[str, Any]], default_gap: float) -> dict[str, Any]:
    ordered = sorted(words, key=lambda word: word["bbox"]["left"])
    word_heights = [word["bbox"]["height"] for word in ordered]
    gap_threshold = max(
        18.0,
        _safe_median(word_heights, default_gap) * _TABLE_CELL_GAP_RATIO,
    )

    segments: list[dict[str, Any]] = []
    current_segment_words: list[dict[str, Any]] = []
    for word in ordered:
        if not current_segment_words:
            current_segment_words = [word]
            continue

        previous = current_segment_words[-1]
        gap = word["bbox"]["left"] - previous["bbox"]["right"]
        if gap <= gap_threshold:
            current_segment_words.append(word)
            continue

        segment = _build_table_segment(current_segment_words)
        if segment["text"]:
            segments.append(segment)
        current_segment_words = [word]

    if current_segment_words:
        segment = _build_table_segment(current_segment_words)
        if segment["text"]:
            segments.append(segment)

    row_bbox = _bbox_from_word_items(ordered)
    return {
        "bbox": row_bbox,
        "segments": segments,
        "cell_count": len(segments),
        "avg_height": sum(word_heights) / len(word_heights),
    }


def _group_words_to_candidate_table_rows(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable_words = [word for word in words if _normalize_layout_text(word["text"])]
    if not usable_words:
        return []

    usable_words.sort(key=lambda word: (word["bbox"]["top"], word["bbox"]["left"]))
    median_height = _safe_median(
        [word["bbox"]["height"] for word in usable_words],
        18.0,
    )
    row_tolerance = max(8.0, median_height * 0.75)

    rows: list[dict[str, Any]] = []
    current_row_words: list[dict[str, Any]] = []
    for word in usable_words:
        if not current_row_words:
            current_row_words = [word]
            continue

        current_bbox = _bbox_from_word_items(current_row_words)
        current_center = current_bbox["top"] + current_bbox["height"] / 2
        word_box = word["bbox"]
        word_center = word_box["top"] + word_box["height"] / 2
        vertical_overlap = min(current_bbox["bottom"], word_box["bottom"]) - max(
            current_bbox["top"],
            word_box["top"],
        )
        overlaps_enough = vertical_overlap >= min(current_bbox["height"], word_box["height"]) * 0.35
        if overlaps_enough or abs(word_center - current_center) <= max(row_tolerance, current_bbox["height"] * 0.7):
            current_row_words.append(word)
            continue

        rows.append(_build_candidate_table_row(current_row_words, median_height))
        current_row_words = [word]

    if current_row_words:
        rows.append(_build_candidate_table_row(current_row_words, median_height))

    return [row for row in rows if row["segments"]]


def _horizontal_overlap_amount(first: dict[str, Any], second: dict[str, Any]) -> float:
    return max(0.0, min(first["right"], second["right"]) - max(first["left"], second["left"]))


def _rows_share_table_structure(
        first: dict[str, Any],
        second: dict[str, Any],
        *,
        page_width: int,
        median_height: float,
) -> bool:
    gap = second["bbox"]["top"] - first["bbox"]["bottom"]
    if gap > max(24.0, median_height * _TABLE_ROW_GAP_RATIO):
        return False

    tolerance = max(
        18.0,
        page_width * _TABLE_COLUMN_TOLERANCE_RATIO,
        median_height * 1.4,
    )
    first_centers = [
        segment["bbox"]["left"] + segment["bbox"]["width"] / 2
        for segment in first["segments"]
    ]
    second_centers = [
        segment["bbox"]["left"] + segment["bbox"]["width"] / 2
        for segment in second["segments"]
    ]
    aligned_columns = sum(
        1
        for current in second_centers
        if any(abs(current - previous) <= tolerance for previous in first_centers)
    )
    return aligned_columns >= min(2, len(first_centers), len(second_centers))


def _cluster_table_columns(
        rows: list[dict[str, Any]],
        *,
        page_width: int,
        median_height: float,
) -> list[dict[str, Any]]:
    tolerance = max(
        18.0,
        page_width * _TABLE_COLUMN_TOLERANCE_RATIO,
        median_height * 1.4,
    )
    columns: list[dict[str, Any]] = []

    # 这里用“按 x 方向聚类”的方式恢复列结构，避免简单按最大列数硬切导致错列。
    for segment in sorted(
            (segment for row in rows for segment in row["segments"]),
            key=lambda item: (item["bbox"]["left"] + item["bbox"]["right"]) / 2,
    ):
        segment_box = segment["bbox"]
        segment_center = segment_box["left"] + segment_box["width"] / 2
        best_column: dict[str, Any] | None = None
        best_distance = float("inf")

        for column in columns:
            overlap = _horizontal_overlap_amount(segment_box, column)
            in_band = column["left"] - tolerance <= segment_center <= column["right"] + tolerance
            if overlap < min(segment_box["width"], column["width"]) * 0.18 and not in_band:
                continue

            distance = abs(segment_center - column["center"])
            if distance < best_distance:
                best_distance = distance
                best_column = column

        if best_column is None:
            columns.append(
                {
                    "left": segment_box["left"],
                    "right": segment_box["right"],
                    "width": segment_box["width"],
                    "center": segment_center,
                    "count": 1,
                }
            )
            continue

        best_column["left"] = min(best_column["left"], segment_box["left"])
        best_column["right"] = max(best_column["right"], segment_box["right"])
        best_column["width"] = best_column["right"] - best_column["left"]
        best_column["count"] += 1
        best_column["center"] = (
            best_column["center"] * (best_column["count"] - 1) + segment_center
        ) / best_column["count"]

    ordered_columns = sorted(columns, key=lambda column: column["center"])
    merged_columns: list[dict[str, Any]] = []
    for column in ordered_columns:
        if merged_columns and column["left"] <= merged_columns[-1]["right"] + tolerance * 0.35:
            previous = merged_columns[-1]
            combined_count = previous["count"] + column["count"]
            previous["left"] = min(previous["left"], column["left"])
            previous["right"] = max(previous["right"], column["right"])
            previous["width"] = previous["right"] - previous["left"]
            previous["center"] = (
                previous["center"] * previous["count"] + column["center"] * column["count"]
            ) / combined_count
            previous["count"] = combined_count
            continue
        merged_columns.append(dict(column))

    return merged_columns


def _assign_segment_to_table_column(segment: dict[str, Any], columns: list[dict[str, Any]]) -> int:
    segment_box = segment["bbox"]
    best_index: int | None = None
    best_overlap = -1.0

    for index, column in enumerate(columns):
        overlap = _horizontal_overlap_amount(segment_box, column)
        if overlap >= min(segment_box["width"], column["width"]) * 0.18 and overlap > best_overlap:
            best_overlap = overlap
            best_index = index

    if best_index is not None:
        return best_index

    segment_center = segment_box["left"] + segment_box["width"] / 2
    return min(
        range(len(columns)),
        key=lambda index: abs(segment_center - columns[index]["center"]),
    )


def _normalize_table_matrix(rows: list[list[str]]) -> list[list[str]]:
    normalized = [
        [_normalize_layout_text(cell) for cell in row]
        for row in rows
    ]
    normalized = [row for row in normalized if any(row)]
    if not normalized:
        return []

    max_cols = max(len(row) for row in normalized)
    padded = [row + [""] * (max_cols - len(row)) for row in normalized]
    used_indices = [
        index
        for index in range(max_cols)
        if any(row[index] for row in padded)
    ]
    if not used_indices:
        return []

    return [
        [row[index] for index in used_indices]
        for row in padded
    ]


def _build_table_from_candidate_rows(
        rows: list[dict[str, Any]],
        *,
        page_num: int,
        page_width: int,
        page_height: int,
        source: str,
) -> dict[str, Any] | None:
    if len(rows) < _TABLE_MIN_ROWS:
        return None

    median_height = _safe_median([row["avg_height"] for row in rows], 18.0)
    columns = _cluster_table_columns(
        rows,
        page_width=page_width,
        median_height=median_height,
    )
    if len(columns) < _TABLE_MIN_COLS:
        return None

    matrix: list[list[str]] = []
    for row in rows:
        grid = [""] * len(columns)
        for segment in row["segments"]:
            column_index = _assign_segment_to_table_column(segment, columns)
            segment_text = _normalize_layout_text(segment["text"])
            if not segment_text:
                continue
            if grid[column_index]:
                grid[column_index] = f"{grid[column_index]} {segment_text}"
            else:
                grid[column_index] = segment_text
        matrix.append(grid)

    matrix = _normalize_table_matrix(matrix)
    if len(matrix) < _TABLE_MIN_ROWS or len(matrix[0]) < _TABLE_MIN_COLS:
        return None

    filled_cell_count = sum(1 for row in matrix for cell in row if cell)
    total_cell_count = len(matrix) * len(matrix[0])
    multi_cell_row_count = sum(1 for row in matrix if sum(1 for cell in row if cell) >= 2)
    if total_cell_count <= 0:
        return None
    if filled_cell_count / total_cell_count < 0.35 or multi_cell_row_count < 2:
        return None

    bbox = _axis_bbox(
        min(row["bbox"]["left"] for row in rows),
        min(row["bbox"]["top"] for row in rows),
        max(row["bbox"]["right"] for row in rows),
        max(row["bbox"]["bottom"] for row in rows),
        (page_width, page_height),
    )
    if not bbox:
        return None

    return {
        "page_num": page_num,
        "source": source,
        "row_count": len(matrix),
        "col_count": len(matrix[0]),
        "bbox": bbox,
        "rows": matrix,
    }


def _detect_tables_from_ocr_page(page_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _group_words_to_candidate_table_rows(page_result["words"])
        if row["cell_count"] >= _TABLE_MIN_COLS
    ]
    if len(rows) < _TABLE_MIN_ROWS:
        return []

    median_height = _safe_median([row["avg_height"] for row in rows], 18.0)
    blocks: list[list[dict[str, Any]]] = []
    current_block: list[dict[str, Any]] = []

    for row in rows:
        if not current_block:
            current_block = [row]
            continue

        if _rows_share_table_structure(
                current_block[-1],
                row,
                page_width=page_result["image_width"],
                median_height=median_height,
        ):
            current_block.append(row)
            continue

        blocks.append(current_block)
        current_block = [row]

    if current_block:
        blocks.append(current_block)

    tables: list[dict[str, Any]] = []
    for block in blocks:
        table = _build_table_from_candidate_rows(
            block,
            page_num=page_result["page_num"],
            page_width=page_result["image_width"],
            page_height=page_result["image_height"],
            source="ocr_layout",
        )
        if table is not None:
            tables.append(table)

    return tables


def _extract_tables_from_pdf_page(page: pymupdf.Page, page_num: int) -> list[dict[str, Any]]:
    table_buffer = io.StringIO()
    with contextlib.redirect_stdout(table_buffer):
        try:
            finder = page.find_tables()
        except Exception:  # noqa: BLE001
            return []

    extracted_tables: list[dict[str, Any]] = []
    for table in finder.tables:
        rows = _normalize_table_matrix(table.extract())
        if len(rows) < _TABLE_MIN_ROWS:
            continue
        if len(rows[0]) < _TABLE_MIN_COLS:
            continue

        non_empty_cells = sum(1 for row in rows for cell in row if cell)
        if non_empty_cells < 4:
            continue

        bbox = _axis_bbox(
            table.bbox[0],
            table.bbox[1],
            table.bbox[2],
            table.bbox[3],
            (
                max(1, int(round(page.rect.width))),
                max(1, int(round(page.rect.height))),
            ),
        )
        if not bbox:
            continue

        extracted_tables.append(
            {
                "page_num": page_num,
                "source": "pdf_text",
                "row_count": len(rows),
                "col_count": len(rows[0]),
                "bbox": bbox,
                "rows": rows,
            }
        )

    return extracted_tables


def _render_table_matrix_html(rows: list[list[str]]) -> str:
    html_rows: list[str] = []
    for row_index, row in enumerate(rows):
        cell_tag = "th" if row_index == 0 else "td"
        cells_html = "".join(
            f"<{cell_tag}>{html_escape(cell) if cell else '&nbsp;'}</{cell_tag}>"
            for cell in row
        )
        html_rows.append(f"<tr>{cells_html}</tr>")
    return "\n".join(html_rows)


def _write_table_csv(rows: list[list[str]], output_path: Path) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerows(rows)
    output_path.write_text(buffer.getvalue(), encoding="utf-8")


def _write_table_html(table: dict[str, Any], output_path: Path, *, source_file: str) -> None:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html_escape(table["table_id"])} - OCR Table Export</title>
  <style>
    :root {{
      --bg: #f4efe5;
      --panel: rgba(255, 250, 242, 0.94);
      --ink: #2b241f;
      --muted: #6b6258;
      --line: #dbcdbd;
      --accent: #b65a32;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(182, 90, 50, 0.18), transparent 32%),
        linear-gradient(180deg, #f7f1e8 0%, #efe5d6 100%);
    }}
    main {{
      width: min(1080px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(91, 67, 44, 0.08);
      padding: 24px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border: 1px solid #efe4d6;
      vertical-align: top;
      text-align: left;
      white-space: pre-wrap;
    }}
    th {{
      background: #fcf4eb;
    }}
    a {{
      color: var(--accent);
    }}
    code {{
      font-family: "SFMono-Regular", monospace;
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <div class="eyebrow">OCR Inspector</div>
      <h1>{html_escape(table["table_id"])}</h1>
      <p>源文件 <code>{html_escape(source_file)}</code>，第 <code>{table["page_num"]}</code> 页，来源 <code>{html_escape(table["source"])}</code>。</p>
      <p>行数 <code>{table["row_count"]}</code>，列数 <code>{table["col_count"]}</code>。<a href="{html_escape(table["csv_path"])}" target="_blank">下载 CSV</a></p>
    </section>
    <section class="panel">
      <table>
        <tbody>
          {_render_table_matrix_html(table["rows"])}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _write_tables_index(tables: list[dict[str, Any]], output_path: Path, *, source_file: str) -> None:
    cards = []
    for table in tables:
        preview_rows = _render_table_matrix_html(table["rows"][: min(4, len(table["rows"]))])
        cards.append(
            f"""
      <article class="panel">
        <h2>{html_escape(table["table_id"])}</h2>
        <p>第 <code>{table["page_num"]}</code> 页，来源 <code>{html_escape(table["source"])}</code>，共 <code>{table["row_count"]}</code> 行 <code>{table["col_count"]}</code> 列。</p>
        <p><a href="{html_escape(table["csv_path"])}" target="_blank">CSV</a> · <a href="{html_escape(table["html_path"])}" target="_blank">HTML</a></p>
        <table>
          <tbody>
            {preview_rows}
          </tbody>
        </table>
      </article>
"""
        )

    body = "\n".join(cards) if cards else """
      <section class="panel">
        <p>当前任务没有检测到可导出的表格。</p>
      </section>
"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OCR Table Exports</title>
  <style>
    :root {{
      --bg: #f4efe5;
      --panel: rgba(255, 250, 242, 0.94);
      --ink: #2b241f;
      --line: #dbcdbd;
      --accent: #b65a32;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(182, 90, 50, 0.18), transparent 32%),
        linear-gradient(180deg, #f7f1e8 0%, #efe5d6 100%);
    }}
    main {{
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(91, 67, 44, 0.08);
      padding: 24px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      margin-top: 14px;
    }}
    th, td {{
      padding: 10px 12px;
      border: 1px solid #efe4d6;
      vertical-align: top;
      text-align: left;
      white-space: pre-wrap;
    }}
    th {{
      background: #fcf4eb;
    }}
    a {{
      color: var(--accent);
    }}
    code {{
      font-family: "SFMono-Regular", monospace;
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <div class="eyebrow">OCR Inspector</div>
      <h1>表格导出索引</h1>
      <p>源文件 <code>{html_escape(source_file)}</code>。当前共导出 <code>{len(tables)}</code> 张表。</p>
    </section>
    {body}
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _draw_overlay(
        image: Image.Image,
        words: list[dict[str, Any]],
        lines: list[dict[str, Any]],
        overlay_path: Path,
) -> None:
    """绘制叠框图。"""
    canvas = image.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # for line in lines:
    #     box = line["bbox"]
    #     draw.rectangle(
    #         [(box["left"], box["top"]), (box["right"], box["bottom"])],
    #         outline=(44, 123, 229),
    #         width=3,
    #     )

    for word in words:
        box = word["bbox"]
        outline = (220, 53, 69)
        if word.get("angle"):
            outline = (13, 110, 253)
        elif word.get("source") != "primary":
            outline = (25, 135, 84)

        points = box.get("points")
        if points:
            polygon = [(point["x"], point["y"]) for point in points]
            draw.line(polygon + [polygon[0]], fill=outline, width=2)
            continue

        draw.rectangle(
            [(box["left"], box["top"]), (box["right"], box["bottom"])],
            outline=outline,
            width=1,
        )

    canvas.save(overlay_path)


def _bbox_iou(first: dict[str, Any], second: dict[str, Any]) -> float:
    left = max(first["left"], second["left"])
    top = max(first["top"], second["top"])
    right = min(first["right"], second["right"])
    bottom = min(first["bottom"], second["bottom"])
    if right <= left or bottom <= top:
        return 0.0

    intersection = (right - left) * (bottom - top)
    first_area = first["width"] * first["height"]
    second_area = second["width"] * second["height"]
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _normalized_text_for_match(text: str) -> str:
    stripped = text.strip().casefold()
    alnum_only = "".join(character for character in stripped if character.isalnum())
    return alnum_only or stripped


def _merge_words(
        base_words: list[dict[str, Any]],
        supplemental_words: list[dict[str, Any]],
        *,
        iou_threshold: float,
        replace_margin: float,
) -> list[dict[str, Any]]:
    """合并补扫结果，尽量只补漏，不让重复词抬高词数。"""
    merged = list(base_words)

    for candidate in supplemental_words:
        candidate_text = _normalized_text_for_match(candidate["text"])
        duplicate_index: int | None = None
        duplicate_has_same_text = False

        for index, existing in enumerate(merged):
            iou = _bbox_iou(existing["bbox"], candidate["bbox"])
            if iou < iou_threshold:
                continue

            existing_text = _normalized_text_for_match(existing["text"])
            same_text = bool(candidate_text and existing_text) and (
                    candidate_text == existing_text
                    or candidate_text in existing_text
                    or existing_text in candidate_text
            )
            if same_text or iou >= 0.75:
                duplicate_index = index
                duplicate_has_same_text = same_text
                break

        if duplicate_index is None:
            merged.append(candidate)
            continue

        existing = merged[duplicate_index]
        candidate_confidence = candidate["confidence"]
        existing_confidence = existing["confidence"]
        if (
                candidate_confidence >= 0
                and candidate_confidence >= existing_confidence + replace_margin
                and (duplicate_has_same_text or existing_confidence < 35)
        ):
            merged[duplicate_index] = candidate

    return merged


def _word_line_key(word: dict[str, Any]) -> tuple[str, float, int, int, int]:
    return (
        word.get("source", "primary"),
        float(word.get("angle", 0.0)),
        word["block_num"],
        word["par_num"],
        word["line_num"],
    )


def _artifact_reason(
        word: dict[str, Any],
        line_counts: Counter[tuple[str, float, int, int, int]],
) -> str | None:
    text = str(word["text"]).strip()
    if not text:
        return "empty_text"

    box = word["bbox"]
    width = max(1, int(box["width"]))
    height = max(1, int(box["height"]))
    aspect_ratio = width / height
    confidence = float(word["confidence"])
    is_isolated = line_counts[_word_line_key(word)] <= 1

    if (
            is_isolated
            and text in _CIRCLE_LIKE_TEXT
            and 0.6 <= aspect_ratio <= 1.7
            and max(width, height) >= 8
            and confidence < 80
    ):
        return "circle_like_graphic"

    if (
            set(text) <= _LINE_ARTIFACT_CHARS
            and (aspect_ratio >= 5 or aspect_ratio <= 0.2)
            and confidence < 75
    ):
        return "table_or_shape_line"

    return None


def _filter_graphic_artifacts(
        words: list[dict[str, Any]],
        *,
        enabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not enabled:
        return words, []

    line_counts = Counter(_word_line_key(word) for word in words)
    kept_words: list[dict[str, Any]] = []
    rejected_words: list[dict[str, Any]] = []

    for word in words:
        reason = _artifact_reason(word, line_counts)
        if not reason:
            kept_words.append(word)
            continue

        rejected_word = dict(word)
        rejected_word["rejected_reason"] = reason
        rejected_words.append(rejected_word)

    return kept_words, rejected_words


def _should_keep_rotated_word(word: dict[str, Any]) -> bool:
    text = str(word["text"]).strip()
    confidence = float(word["confidence"])
    alnum_count = sum(1 for character in text if character.isalnum())
    if alnum_count >= 2 and confidence >= DEFAULT_ROTATED_MIN_CONFIDENCE:
        return True
    if text in _CIRCLE_LIKE_TEXT:
        return False
    return bool(len(text) == 1 and text.isalnum() and confidence >= 85)


def _run_tesseract_pass(
        image: Image.Image,
        *,
        page_num: int,
        lang: str,
        config: str,
        padding: int,
        source: str,
        original_size: tuple[int, int] | None = None,
        angle: float = 0.0,
        min_confidence: float = -1.0,
) -> list[dict[str, Any]]:
    padded_image = _expand_for_ocr(image, padding)
    try:
        data = pytesseract.image_to_data(
            padded_image,
            lang=lang,
            config=config,
            output_type=Output.DICT,
        )
    finally:
        if padded_image is not image:
            padded_image.close()

    if angle:
        if original_size is None:
            raise ValueError("旋转 OCR pass 必须提供 original_size。")

        def bbox_mapper(left: int, top: int, width: int, height: int) -> dict[str, Any] | None:
            return _map_rotated_bbox(
                left,
                top,
                width,
                height,
                padding=padding,
                rotated_size=image.size,
                original_size=original_size,
                angle=angle,
            )

    else:

        def bbox_mapper(left: int, top: int, width: int, height: int) -> dict[str, Any] | None:
            return _map_padded_bbox(
                left,
                top,
                width,
                height,
                padding=padding,
                image_size=image.size,
            )

    return _extract_words_from_tesseract_dict(
        data,
        page_num=page_num,
        bbox_mapper=bbox_mapper,
        source=source,
        angle=angle,
        min_confidence=min_confidence,
    )


def _ocr_image(
        image_path: Path,
        page_num: int,
        lang: str,
        tesseract_config: str,
        preprocess_mode: str,
        ocr_padding: int,
        enable_sparse_fallback: bool,
        enable_rotated_text: bool,
        suppress_graphic_artifacts: bool,
) -> dict[str, Any]:
    """对单页图片执行 OCR，并返回页面级结构化结果。"""
    image = Image.open(image_path).convert("RGB")
    ocr_image = _preprocess_for_ocr(image, preprocess_mode)
    normalized_padding = max(0, int(ocr_padding))
    pass_stats: list[dict[str, Any]] = []

    words = _run_tesseract_pass(
        ocr_image,
        page_num=page_num,
        lang=lang,
        config=tesseract_config,
        padding=normalized_padding,
        source="primary",
    )
    pass_stats.append(
        {
            "name": "primary",
            "angle": 0,
            "config": tesseract_config,
            "raw_word_count": len(words),
            "added_word_count": len(words),
        }
    )

    if enable_sparse_fallback:
        sparse_config = _config_with_psm(tesseract_config, 11)
        sparse_words = _run_tesseract_pass(
            ocr_image,
            page_num=page_num,
            lang=lang,
            config=sparse_config,
            padding=normalized_padding,
            source="sparse_fallback",
            min_confidence=DEFAULT_SUPPLEMENTAL_MIN_CONFIDENCE,
        )
        before_count = len(words)
        words = _merge_words(
            words,
            sparse_words,
            iou_threshold=0.55,
            replace_margin=8,
        )
        pass_stats.append(
            {
                "name": "sparse_fallback",
                "angle": 0,
                "config": sparse_config,
                "min_confidence": DEFAULT_SUPPLEMENTAL_MIN_CONFIDENCE,
                "raw_word_count": len(sparse_words),
                "added_word_count": max(0, len(words) - before_count),
            }
        )

    if enable_rotated_text:
        rotated_config = _config_with_psm(tesseract_config, 11)
        for angle in ROTATED_TEXT_ANGLES:
            rotated_image = ocr_image.rotate(angle, expand=True, fillcolor="white")
            try:
                rotated_words = _run_tesseract_pass(
                    rotated_image,
                    page_num=page_num,
                    lang=lang,
                    config=rotated_config,
                    padding=normalized_padding,
                    source="rotated_text",
                    original_size=ocr_image.size,
                    angle=angle,
                    min_confidence=DEFAULT_ROTATED_MIN_CONFIDENCE,
                )
            finally:
                rotated_image.close()

            rotated_words = [word for word in rotated_words if _should_keep_rotated_word(word)]
            before_count = len(words)
            words = _merge_words(
                words,
                rotated_words,
                iou_threshold=0.35,
                replace_margin=12,
            )
            pass_stats.append(
                {
                    "name": "rotated_text",
                    "angle": angle,
                    "config": rotated_config,
                    "min_confidence": DEFAULT_ROTATED_MIN_CONFIDENCE,
                    "raw_word_count": len(rotated_words),
                    "added_word_count": max(0, len(words) - before_count),
                }
            )

    words, rejected_words = _filter_graphic_artifacts(
        words,
        enabled=suppress_graphic_artifacts,
    )
    words.sort(
        key=lambda word: (
            word["bbox"]["top"],
            word["bbox"]["left"],
            0 if word.get("source") == "primary" else 1,
        )
    )

    lines = _group_words_to_lines(words)
    page_text = "\n".join(line["text"] for line in lines)
    ocr_image.close()

    return {
        "page_num": page_num,
        "image_width": image.width,
        "image_height": image.height,
        "words": words,
        "rejected_words": rejected_words,
        "lines": lines,
        "text": page_text,
        "diagnostics": {
            "preprocess_mode": _normalize_preprocess_mode(preprocess_mode),
            "ocr_padding": normalized_padding,
            "sparse_fallback_enabled": enable_sparse_fallback,
            "rotated_text_enabled": enable_rotated_text,
            "graphic_artifact_filter_enabled": suppress_graphic_artifacts,
            "pass_stats": pass_stats,
            "rejected_word_count": len(rejected_words),
        },
        "_image": image,
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


def run_ocr_pipeline(
        source_path: Path,
        output_dir: Path,
        source_kind: str = "pdf",
        lang: str = "eng",
        dpi: int = 300,
        tesseract_config: str = DEFAULT_TESSERACT_CONFIG,
        tesseract_cmd: str | None = None,
        preprocess_mode: str = DEFAULT_PREPROCESS_MODE,
        ocr_padding: int = DEFAULT_OCR_PADDING,
        enable_sparse_fallback: bool = True,
        enable_rotated_text: bool = DEFAULT_ENABLE_ROTATED_TEXT,
        suppress_graphic_artifacts: bool = True,
) -> dict[str, Any]:
    """运行完整 OCR 流程。"""
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    overlays_dir = output_dir / "overlays"
    texts_dir = output_dir / "texts"
    markdown_dir = output_dir / "markdown"
    tables_dir = output_dir / "tables"
    review_overlays_dir = output_dir / "review_overlays"
    robustness_lab_dir = output_dir / "robustness_lab"

    for directory in (pages_dir, overlays_dir, texts_dir, markdown_dir, tables_dir, review_overlays_dir, robustness_lab_dir):
        directory.mkdir(parents=True, exist_ok=True)

    page_image_paths = prepare_page_images(
        source_path,
        images_dir=pages_dir,
        source_kind=source_kind,
        dpi=dpi,
    )

    pages: list[dict[str, Any]] = []
    page_image_paths_by_page_num: dict[int, Path] = {}
    full_text_parts: list[str] = []

    for page_index, image_path in enumerate(page_image_paths, start=1):
        page_image_paths_by_page_num[page_index] = image_path
        page_result = _ocr_image(
            image_path=image_path,
            page_num=page_index,
            lang=lang,
            tesseract_config=tesseract_config,
            preprocess_mode=preprocess_mode,
            ocr_padding=ocr_padding,
            enable_sparse_fallback=enable_sparse_fallback,
            enable_rotated_text=enable_rotated_text,
            suppress_graphic_artifacts=suppress_graphic_artifacts,
        )

        image = page_result["_image"]
        overlay_path = overlays_dir / f"page_{page_index:03d}_overlay.png"
        _draw_overlay(
            image=image,
            words=page_result["words"],
            lines=page_result["lines"],
            overlay_path=overlay_path,
        )
        image.close()

        text_path = texts_dir / f"page_{page_index:03d}.txt"
        text_path.write_text(page_result["text"], encoding="utf-8")

        page_result.pop("_image", None)
        page_result["image_path"] = image_path.name
        page_result["overlay_path"] = overlay_path.name
        page_result["text_path"] = text_path.name

        markdown_path = markdown_dir / f"page_{page_index:03d}.md"
        page_result["markdown_path"] = markdown_path.name

        full_text_parts.append(f"===== Page {page_index} =====\n{page_result['text']}\n")
        pages.append(page_result)

    full_text_path = output_dir / "full_text.txt"
    full_text_path.write_text("\n".join(full_text_parts), encoding="utf-8")

    layout_analysis = _analyze_document_layout(pages)
    page_layouts_by_page_num = {
        page_layout["page_num"]: page_layout
        for page_layout in layout_analysis["pages"]
    }

    source_tables_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if source_kind == "pdf":
        pdf_document = pymupdf.open(source_path)
        try:
            for page_index, pdf_page in enumerate(pdf_document, start=1):
                source_tables_by_page[page_index] = _extract_tables_from_pdf_page(pdf_page, page_index)
        finally:
            pdf_document.close()

    all_tables: list[dict[str, Any]] = []
    table_source_counts: Counter[str] = Counter()
    for page in pages:
        page_layout = page_layouts_by_page_num.get(page["page_num"], {})
        detected_tables = [dict(table) for table in source_tables_by_page.get(page["page_num"], [])]
        if not detected_tables:
            detected_tables = _detect_tables_from_ocr_page(page)

        page_table_summaries: list[dict[str, Any]] = []
        for table_index, detected_table in enumerate(detected_tables, start=1):
            table_payload = dict(detected_table)
            table_id = f"page_{page['page_num']:03d}_table_{table_index:02d}"
            csv_filename = f"{table_id}.csv"
            html_filename = f"{table_id}.html"

            table_payload["table_id"] = table_id
            table_payload["csv_path"] = csv_filename
            table_payload["html_path"] = html_filename
            _write_table_csv(table_payload["rows"], tables_dir / csv_filename)
            _write_table_html(
                table_payload,
                tables_dir / html_filename,
                source_file=source_path.name,
            )

            table_source_counts[table_payload["source"]] += 1
            all_tables.append(table_payload)
            page_table_summaries.append(
                {
                    "table_id": table_id,
                    "source": table_payload["source"],
                    "row_count": table_payload["row_count"],
                    "col_count": table_payload["col_count"],
                    "csv_path": csv_filename,
                    "html_path": html_filename,
                }
            )

        # 优先使用 PDF 原生抽表，扫描件和图片页再回退到 OCR 词框恢复。
        page["layout"] = {
            "items": page_layout.get("items", []),
            "stats": page_layout.get("stats", {}),
        }
        page["tables"] = page_table_summaries
        markdown_path = markdown_dir / page["markdown_path"]
        markdown_path.write_text(
            _build_page_markdown(
                page,
                source_file=source_path.name,
                source_kind=source_kind,
                content_markdown=page_layout.get("content_markdown", ""),
                layout_stats=page_layout.get("stats", {}),
                table_summaries=page_table_summaries,
            ),
            encoding="utf-8",
        )

    _write_tables_index(
        all_tables,
        tables_dir / "index.html",
        source_file=source_path.name,
    )

    document_markdown_path = output_dir / "document.md"
    document_markdown_path.write_text(
        _build_document_markdown(source_path.name, layout_analysis["pages"]),
        encoding="utf-8",
    )

    ocr_result = {
        "source_file": source_path.name,
        "source_kind": source_kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "dpi": dpi,
            "lang": lang,
            "tesseract_config": tesseract_config,
            "tesseract_cmd": pytesseract.pytesseract.tesseract_cmd,
            "preprocess_mode": _normalize_preprocess_mode(preprocess_mode),
            "ocr_padding": max(0, int(ocr_padding)),
            "sparse_fallback": enable_sparse_fallback,
            "rotated_text": enable_rotated_text,
            "graphic_artifact_filter": suppress_graphic_artifacts,
            "layout_reader": {
                "enabled": True,
                "topics": ["title", "paragraph", "list", "header_footer", "reading_order"],
            },
            "table_to_csv": {
                "enabled": True,
                "formats": ["csv", "html"],
                "detectors": ["pdf_text", "ocr_layout"],
            },
            "form_to_json": {
                "enabled": True,
                "topics": ["key_value", "checkbox", "field_normalization"],
            },
            "receipt_invoice_extractor": {
                "enabled": True,
                "topics": ["vendor", "date", "tax", "total", "line_items"],
            },
            "mixed_document_router": {
                "enabled": True,
                "labels": ["invoice", "receipt", "form", "report", "id"],
                "dispatch_after": "table_to_csv",
            },
            "bundle_splitter": {
                "enabled": True,
                "topics": ["page_range_detection", "subdocument_export"],
                "dispatch_after": "mixed_document_router",
            },
            "signature_handwriting_review": {
                "enabled": True,
                "topics": ["signature_region", "handwriting_region", "suspicious_fields"],
                "dispatch_after": "form_to_json",
            },
            "query_extractor": {
                "enabled": True,
                "topics": ["query_based_extraction", "nl_field_lookup"],
                "dispatch_after": "signature_handwriting_review",
            },
            "custom_schema_extractor": {
                "enabled": True,
                "vertical": "contract",
                "fields": [
                    "contract_title",
                    "contract_number",
                    "party_a",
                    "party_b",
                    "signing_date",
                    "effective_date",
                    "end_date",
                    "total_amount",
                ],
            },
            "multi_page_consolidator": {
                "enabled": True,
                "topics": ["cross_page_aggregation", "deduplication", "field_merge", "total_validation"],
                "dispatch_after": "custom_schema_extractor",
            },
            "review_workbench": {
                "enabled": True,
                "topics": ["human_review", "revision_history", "low_confidence_queue"],
                "dispatch_after": "multi_page_consolidator",
            },
            "layout_aware_chunker": {
                "enabled": True,
                "topics": ["layout_based_chunking", "heading_context", "table_context", "rag_chunks"],
                "dispatch_after": "multi_page_consolidator",
            },
            "direct_pdf_structurer": {
                "enabled": True,
                "topics": ["direct_pdf_understanding", "strict_schema_output"],
                "dispatch_after": "layout_aware_chunker",
            },
            "evidence_grounded_multi_page_qa": {
                "enabled": True,
                "topics": ["multi_page_qa", "evidence_pages", "traceable_answers"],
                "dispatch_after": "direct_pdf_structurer",
            },
            "complex_page_analyst": {
                "enabled": True,
                "selected_domain": "chart_qa",
                "topics": ["complex_element_parsing", "chart_qa", "error_explanation"],
                "dispatch_after": "evidence_grounded_multi_page_qa",
            },
            "robustness_lab": {
                "enabled": True,
                "topics": ["scan", "skew", "shadow", "curved_page", "screen_capture", "blur"],
                "dispatch_after": "complex_page_analyst",
            },
            "document_ai_copilot": {
                "enabled": True,
                "topics": ["end_to_end_pipeline", "demo_handoff", "json_markdown_export"],
                "dispatch_after": "robustness_lab",
            },
        },
        "page_count": len(pages),
        "layout_analysis": layout_analysis["stats"],
        "table_analysis": {
            "table_count": len(all_tables),
            "pdf_text_table_count": table_source_counts.get("pdf_text", 0),
            "ocr_layout_table_count": table_source_counts.get("ocr_layout", 0),
        },
        "tables": all_tables,
        "pages": pages,
    }

    # 路由决策放在 table 之后、业务抽取之前，
    # 这样分类时可以复用版面和表格信息，同时避免所有文档继续走同一条链。
    router_result = build_mixed_document_router_result(ocr_result)
    ocr_result["document_label"] = router_result["label"]
    ocr_result["document_router_result"] = router_result

    # Bundle Splitter 复用页级路由结果，对扫描包 / 拼接 PDF 做起止页识别和导出。
    bundle_result = build_bundle_splitter_result(
        ocr_result,
        source_path=source_path,
        output_dir=output_dir,
        source_kind=source_kind,
    )
    ocr_result["bundle_splitter_result"] = bundle_result

    bundle_json_path = output_dir / "bundle_splitter.json"
    write_bundle_splitter_json(bundle_result, bundle_json_path)

    selected_label = router_result["label"]
    form_json_path = output_dir / "form.json"
    receipt_json_path = output_dir / "receipt_invoice.json"
    is_multi_document_bundle = bundle_result["analysis"].get("segment_count", 0) > 1

    if is_multi_document_bundle:
        # 一旦识别到多文档 bundle，就不要再把整包当成单一业务文档抽取，
        # 避免把 form / invoice / receipt 混在一起时输出误导性的顶层结果。
        form_result = build_skipped_form_result(
            ocr_result["source_file"],
            "multi-document bundle detected; see bundle_splitter_result",
        )
        receipt_result = build_skipped_receipt_invoice_result(
            ocr_result["source_file"],
            "multi-document bundle detected; see bundle_splitter_result",
        )
    elif selected_label in {"form", "id"}:
        # form / id 优先走键值对和字段标准化链路。
        form_result = build_form_to_json_result(ocr_result)
        receipt_result = build_skipped_receipt_invoice_result(
            ocr_result["source_file"],
            f"document routed to {selected_label} chain",
        )
    elif selected_label in {"invoice", "receipt"}:
        # receipt / invoice 优先走票据 schema 和明细行恢复链路。
        receipt_result = build_receipt_invoice_result(ocr_result)
        form_result = build_skipped_form_result(
            ocr_result["source_file"],
            f"document routed to {selected_label} chain",
        )
    else:
        # report 暂时只保留基础 OCR / layout / table 输出，业务抽取写空占位结果。
        form_result = build_skipped_form_result(
            ocr_result["source_file"],
            "document routed to report chain",
        )
        receipt_result = build_skipped_receipt_invoice_result(
            ocr_result["source_file"],
            "document routed to report chain",
        )

    ocr_result["form_result"] = form_result
    ocr_result["form_analysis"] = form_result["analysis"]
    ocr_result["receipt_invoice_result"] = receipt_result
    ocr_result["receipt_invoice_analysis"] = receipt_result["analysis"]

    write_form_json(form_result, form_json_path)
    write_receipt_invoice_json(receipt_result, receipt_json_path)

    # 复核结果的目标是“圈出人工该看哪里”，而不是强行给出不可靠的最终识别值。
    review_result = build_signature_handwriting_review_result(
        ocr_result,
        form_result=form_result,
        bundle_result=bundle_result,
    )
    write_review_overlays(
        review_result,
        review_overlays_dir=review_overlays_dir,
        page_image_paths_by_page=page_image_paths_by_page_num,
        output_dir=output_dir,
    )
    ocr_result["signature_handwriting_review_result"] = review_result

    review_json_path = output_dir / "signature_handwriting_review.json"
    write_review_json(review_result, review_json_path)

    # Query Extractor 在 OCR 完成后构建一份统一索引，后续自然语言提问直接复用它。
    query_result = build_query_extractor_result(ocr_result)
    ocr_result["query_extractor_result"] = query_result

    query_json_path = output_dir / "query_extractor.json"
    write_query_json(query_result, query_json_path)

    # Custom Schema Extractor 这里只做一个垂直：合同 8 字段。
    contract_schema_result = build_contract_schema_result(
        ocr_result,
        query_result=query_result,
    )
    ocr_result["contract_schema_result"] = contract_schema_result

    contract_schema_json_path = output_dir / "contract_schema.json"
    write_contract_schema_json(contract_schema_result, contract_schema_json_path)

    # Multi-page Consolidator 汇总前面业务结果，专门处理跨页去重和 totals 对账。
    multi_page_consolidation_result = build_multi_page_consolidation_result(ocr_result)
    ocr_result["multi_page_consolidation_result"] = multi_page_consolidation_result

    multi_page_consolidation_json_path = output_dir / "multi_page_consolidation.json"
    write_multi_page_consolidation_json(multi_page_consolidation_result, multi_page_consolidation_json_path)

    # Layout-aware Chunker 将标题链、表格上下文和页码保留下来，供 RAG 检索使用。
    layout_chunk_result = build_layout_aware_chunk_result(ocr_result)
    ocr_result["layout_chunk_result"] = layout_chunk_result

    layout_chunks_json_path = output_dir / "layout_chunks.json"
    write_layout_chunks_json(layout_chunk_result, layout_chunks_json_path)

    # Direct PDF Structurer 直接读取 PDF 原生结构，并输出严格 JSON schema。
    direct_pdf_structure_result = build_direct_pdf_structure_result(
        source_path=source_path,
        source_kind=source_kind,
        ocr_result=ocr_result,
    )
    ocr_result["direct_pdf_structure_result"] = direct_pdf_structure_result

    direct_pdf_structure_json_path = output_dir / "direct_pdf_structure.json"
    write_direct_pdf_structure_json(direct_pdf_structure_result, direct_pdf_structure_json_path)

    # Evidence-grounded QA 先建立证据索引，提问接口必须基于这些 evidence units 返回页码。
    evidence_qa_result = build_evidence_qa_result(ocr_result)
    ocr_result["evidence_qa_result"] = evidence_qa_result

    evidence_qa_json_path = output_dir / "evidence_qa.json"
    write_evidence_qa_json(evidence_qa_result, evidence_qa_json_path)

    # Complex Page Analyst 这里选择图表问答做深，基于表格/布局证据回答并解释错误边界。
    complex_page_analysis_result = build_complex_page_analysis_result(ocr_result)
    ocr_result["complex_page_analysis_result"] = complex_page_analysis_result

    complex_page_analysis_json_path = output_dir / "complex_page_analysis.json"
    write_complex_page_analysis_json(complex_page_analysis_result, complex_page_analysis_json_path)

    # Review Workbench 首次写一个空修订记录，后续人工保存时在同一文件中追加批次。
    initialize_review_workbench_revisions(output_dir, source_file=ocr_result["source_file"])

    # 业务链路落定后再更新一次路由结果，让输出里能看到实际分发计划和最终标签。
    router_result = build_mixed_document_router_result(ocr_result)
    ocr_result["document_label"] = router_result["label"]
    ocr_result["document_router_result"] = router_result

    router_json_path = output_dir / "document_router.json"
    write_document_router_json(router_result, router_json_path)

    # Robustness Lab 复用最终 OCR/抽取/问答指标，生成退化页图并判断最可能崩掉的层。
    robustness_lab_result = build_robustness_lab_result(
        ocr_result,
        page_image_paths_by_page=page_image_paths_by_page_num,
        output_dir=output_dir,
    )
    ocr_result["robustness_lab_result"] = robustness_lab_result

    degradation_report_json_path = output_dir / "degradation_report.json"
    write_degradation_report_json(robustness_lab_result, degradation_report_json_path)

    # End-to-End Copilot 不重新跑算法，而是把 1-17 的产物串成一条产品级演示链路。
    document_ai_copilot_result = build_document_ai_copilot_result(ocr_result)
    ocr_result["document_ai_copilot_result"] = document_ai_copilot_result

    document_ai_copilot_json_path = output_dir / "document_ai_copilot.json"
    document_ai_copilot_markdown_path = output_dir / "document_ai_copilot.md"
    write_document_ai_copilot_json(document_ai_copilot_result, document_ai_copilot_json_path)
    write_document_ai_copilot_markdown(document_ai_copilot_result, document_ai_copilot_markdown_path)

    json_path = output_dir / "ocr.json"
    json_path.write_text(
        json.dumps(ocr_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ocr_result": ocr_result,
        "ocr_json_path": json_path,
        "full_text_path": full_text_path,
        "document_markdown_path": document_markdown_path,
        "form_json_path": form_json_path,
        "receipt_json_path": receipt_json_path,
        "router_json_path": router_json_path,
        "bundle_json_path": bundle_json_path,
        "review_json_path": review_json_path,
        "query_json_path": query_json_path,
        "contract_schema_json_path": contract_schema_json_path,
        "multi_page_consolidation_json_path": multi_page_consolidation_json_path,
        "layout_chunks_json_path": layout_chunks_json_path,
        "direct_pdf_structure_json_path": direct_pdf_structure_json_path,
        "evidence_qa_json_path": evidence_qa_json_path,
        "complex_page_analysis_json_path": complex_page_analysis_json_path,
        "degradation_report_json_path": degradation_report_json_path,
        "document_ai_copilot_json_path": document_ai_copilot_json_path,
        "document_ai_copilot_markdown_path": document_ai_copilot_markdown_path,
        "markdown_dir": markdown_dir,
        "tables_dir": tables_dir,
        "robustness_lab_dir": robustness_lab_dir,
        "output_dir": output_dir,
    }
