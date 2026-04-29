from __future__ import annotations

"""OCR Inspector 基础能力。

第 1 步负责 PDF/图片转页图、Tesseract OCR、word/line bbox、置信度和叠框图。
从 ocr_engine.py 拆出来后，主流程仍然可以直接复用这些函数。
"""

from collections import Counter, defaultdict
import math
from pathlib import Path
from typing import Any
import shlex

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps
import pymupdf
import pytesseract
from pytesseract import Output

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
