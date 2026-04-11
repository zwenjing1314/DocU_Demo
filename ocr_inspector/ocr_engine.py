from __future__ import annotations

"""OCR 核心流程。

这个模块负责：
1. 将 PDF 渲染成页图；
2. 调用 Tesseract OCR 获取词级结果；
3. 按行聚合，生成 line 级 bbox；
4. 绘制叠框图；
5. 导出 ocr.json 和纯文本。
"""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from PIL import Image, ImageDraw
import pymupdf
import pytesseract
from pytesseract import Output

# 默认 OCR 配置：
# --oem 3: 使用默认 OCR 引擎模式
# --psm 3: 自动页面分割，适合大多数整页文档
DEFAULT_TESSERACT_CONFIG = "--oem 3 --psm 3"


def render_pdf_to_images(pdf_path: Path, images_dir: Path, dpi: int = 200) -> list[Path]:
    """将 PDF 的每一页渲染成 PNG 图片。

    Args:
        pdf_path: 输入 PDF 路径。
        images_dir: 页面图片输出目录。
        dpi: 渲染分辨率。越高越清晰，但速度和体积会增加。

    Returns:
        渲染后图片路径列表，顺序与 PDF 页码一致。
    """
    images_dir.mkdir(parents=True, exist_ok=True)

    page_image_paths: list[Path] = []
    doc = pymupdf.open(pdf_path)
    try:
        zoom = dpi / 72.0  # PDF 默认坐标系是 72 DPI
        matrix = pymupdf.Matrix(zoom, zoom)

        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = images_dir / f"page_{page_index + 1:03d}.png"
            pix.save(str(image_path))
            page_image_paths.append(image_path)
    finally:
        doc.close()

    return page_image_paths


def _safe_float(value: Any, default: float = -1.0) -> float:
    """将 Tesseract 返回的 conf 等字段安全转成 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_words_from_tesseract_dict(data: dict[str, list[Any]], page_num: int) -> list[dict[str, Any]]:
    """从 pytesseract.image_to_data 的字典结果中提取词级记录。"""
    words: list[dict[str, Any]] = []

    # 获取总条目数（所有层级的总和）
    # data["text"] 是一个列表，长度等于 TSV 的行数
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

        words.append(
            {
                "page_num": page_num,
                "text": text,
                "confidence": conf,
                "bbox": {
                    "left": left,
                    "top": top,
                    "width": width,
                    "height": height,
                    "right": left + width,
                    "bottom": top + height,
                },
                # 下面几个字段有助于后续按 block / paragraph / line 聚合。
                "block_num": int(data["block_num"][i]),
                "par_num": int(data["par_num"][i]),
                "line_num": int(data["line_num"][i]),
                "word_num": int(data["word_num"][i]),
            }
        )

    return words


def _group_words_to_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同一行的词聚合成 line 级结果。

    这里不直接用 Tesseract 的 line 级条目，是因为 line 级置信度经常不可用；
    我们自行对词级结果做聚合，得到更稳定的 line 文本与平均置信度。
    """
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        key = (word["block_num"], word["par_num"], word["line_num"])
        groups[key].append(word)

    lines: list[dict[str, Any]] = []
    for (_, _, _), items in groups.items():
        # 这里用 Tesseract 提供的 word_num（同一行内部的自然顺序）排序，
        # 可以避免因为 bbox.top 的细微抖动导致词序颠倒。
        # items = sorted(items, key=lambda x: x["word_num"])
        items = sorted(items, key=lambda x: x["word_num"])

        left = min(item["bbox"]["left"] for item in items)
        top = min(item["bbox"]["top"] for item in items)
        right = max(item["bbox"]["right"] for item in items)
        bottom = max(item["bbox"]["bottom"] for item in items)

        text = " ".join(item["text"] for item in items)
        confidences = [item["confidence"] for item in items if item["confidence"] >= 0]
        avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else -1.0

        # line 级别保留词列表，后续做错误分析或定位时会很方便。
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
                "words": [item["text"] for item in items],
            }
        )

    # 这里再次按页面阅读顺序排序。
    lines.sort(key=lambda x: (x["bbox"]["top"], x["bbox"]["left"]))
    return lines


def _draw_overlay(
    image: Image.Image,
    words: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    overlay_path: Path,
) -> None:
    """绘制叠框图。

    - 蓝色框：line 级 bbox
    - 红色框：word 级 bbox
    """
    canvas = image.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # 先画 line，粗一点，方便看整体结构。
    for line in lines:
        box = line["bbox"]
        draw.rectangle(
            [(box["left"], box["top"]), (box["right"], box["bottom"])],
            outline=(44, 123, 229),
            width=3,
        )

    # 再画 word，细一点，方便看局部。
    for word in words:
        box = word["bbox"]
        draw.rectangle(
            [(box["left"], box["top"]), (box["right"], box["bottom"])],
            outline=(220, 53, 69),
            width=1,
        )

    canvas.save(overlay_path)


def _ocr_image(
    image_path: Path,
    page_num: int,
    lang: str,
    tesseract_config: str,
) -> dict[str, Any]:
    """对单页图片执行 OCR，并返回页面级结构化结果。"""
    image = Image.open(image_path).convert("RGB")

    data = pytesseract.image_to_data(
        image,  # PIL Image 对象或文件路径
        lang=lang,  # 识别语言，如 'eng', 'chi_sim'
        config=tesseract_config,  # Tesseract 配置字符串
        output_type=Output.DICT,  # 输出格式
    )

    words = _extract_words_from_tesseract_dict(data, page_num=page_num)
    lines = _group_words_to_lines(words)
    page_text = "\n".join(line["text"] for line in lines)

    return {
        "page_num": page_num,
        "image_width": image.width,
        "image_height": image.height,
        "words": words,
        "lines": lines,
        "text": page_text,
        # 把 image 对象一起返回，便于后面直接绘制叠框图。
        "_image": image,
    }


def run_ocr_pipeline(
    pdf_path: Path,
    output_dir: Path,
    lang: str = "eng",
    dpi: int = 200,
    tesseract_config: str = DEFAULT_TESSERACT_CONFIG,
    tesseract_cmd: str | None = None,
) -> dict[str, Any]:
    """运行完整 OCR 流程。

    Args:
        pdf_path: 输入 PDF 文件。
        output_dir: 当前任务的输出目录。
        lang: Tesseract 语言，如 eng / chi_sim / eng+chi_sim。
        dpi: PDF 渲染 DPI。
        tesseract_config: Tesseract 额外配置。
        tesseract_cmd: 若 tesseract 不在 PATH 中，可手动指定路径。

    Returns:
        一个包含输出文件信息和结构化 OCR 结果的字典。
    """
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    overlays_dir = output_dir / "overlays"
    texts_dir = output_dir / "texts"
    pages_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    page_image_paths = render_pdf_to_images(pdf_path, images_dir=pages_dir, dpi=dpi)

    pages: list[dict[str, Any]] = []
    full_text_parts: list[str] = []

    for page_index, image_path in enumerate(page_image_paths, start=1):
        page_result = _ocr_image(
            image_path=image_path,  # PIL Image 对象或文件路径
            page_num=page_index,
            lang=lang,
            tesseract_config=tesseract_config,  # Tesseract 配置字符串
        )

        overlay_path = overlays_dir / f"page_{page_index:03d}_overlay.png"
        _draw_overlay(
            image=page_result["_image"],
            words=page_result["words"],
            lines=page_result["lines"],
            overlay_path=overlay_path,
        )

        text_path = texts_dir / f"page_{page_index:03d}.txt"
        text_path.write_text(page_result["text"], encoding="utf-8")

        full_text_parts.append(f"===== Page {page_index} =====\n{page_result['text']}\n")

        # image 对象不能序列化，导出 JSON 前要删掉。
        page_result.pop("_image", None)
        page_result["image_path"] = str(image_path.name)
        page_result["overlay_path"] = str(overlay_path.name)
        page_result["text_path"] = str(text_path.name)
        pages.append(page_result)

    full_text_path = output_dir / "full_text.txt"
    full_text_path.write_text("\n".join(full_text_parts), encoding="utf-8")

    ocr_result = {
        "source_file": pdf_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "dpi": dpi,
            "lang": lang,
            "tesseract_config": tesseract_config,
            "tesseract_cmd": pytesseract.pytesseract.tesseract_cmd,
        },
        "page_count": len(pages),
        "pages": pages,
    }

    json_path = output_dir / "ocr.json"
    json_path.write_text(
        json.dumps(ocr_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ocr_result": ocr_result,
        "ocr_json_path": json_path,
        "full_text_path": full_text_path,
        "output_dir": output_dir,
    }
