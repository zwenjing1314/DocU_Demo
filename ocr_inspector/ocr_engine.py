from __future__ import annotations

"""OCR 核心流程。

这个模块负责：
1. 将 PDF 或图片整理成页面图片；
2. 调用 Tesseract OCR 获取词级结果；
3. 按行聚合，生成 line 级 bbox；
4. 绘制叠框图；
5. 导出 ocr.json、纯文本和按页 Markdown。
"""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from PIL import Image, ImageDraw, ImageOps
import pymupdf
import pytesseract
from pytesseract import Output

# 默认 OCR 配置：
# --oem 3: 使用默认 OCR 引擎模式
# --psm 3: 自动页面分割，适合大多数整页文档
DEFAULT_TESSERACT_CONFIG = "--oem 3 --psm 3"


def render_pdf_to_images(pdf_path: Path, images_dir: Path, dpi: int = 200) -> list[Path]:
    """将 PDF 的每一页渲染成 PNG 图片。"""
    images_dir.mkdir(parents=True, exist_ok=True)

    page_image_paths: list[Path] = []
    doc = pymupdf.open(pdf_path)
    try:
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)

        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = images_dir / f"page_{page_index + 1:03d}.png"
            pix.save(str(image_path))
            page_image_paths.append(image_path)
    finally:
        doc.close()

    return page_image_paths


def render_image_to_page(image_path: Path, images_dir: Path) -> list[Path]:
    """把单张图片整理成与 PDF 页图一致的输出格式。"""
    images_dir.mkdir(parents=True, exist_ok=True)

    output_path = images_dir / "page_001.png"
    with Image.open(image_path) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        normalized.save(output_path)

    return [output_path]


def prepare_page_images(
    source_path: Path,
    images_dir: Path,
    source_kind: str,
    dpi: int = 200,
) -> list[Path]:
    """根据源文件类型准备统一的页图列表。"""
    if source_kind == "pdf":
        return render_pdf_to_images(source_path, images_dir=images_dir, dpi=dpi)
    if source_kind == "image":
        return render_image_to_page(source_path, images_dir=images_dir)
    raise ValueError(f"不支持的 source_kind: {source_kind}")


def _safe_float(value: Any, default: float = -1.0) -> float:
    """将 Tesseract 返回的 conf 等字段安全转成 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_words_from_tesseract_dict(data: dict[str, list[Any]], page_num: int) -> list[dict[str, Any]]:
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
                "block_num": int(data["block_num"][i]),
                "par_num": int(data["par_num"][i]),
                "line_num": int(data["line_num"][i]),
                "word_num": int(data["word_num"][i]),
            }
        )

    return words


def _group_words_to_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同一行的词聚合成 line 级结果。"""
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for word in words:
        key = (word["block_num"], word["par_num"], word["line_num"])
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
        image,
        lang=lang,
        config=tesseract_config,
        output_type=Output.DICT,
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
        "_image": image,
    }


def _build_page_markdown(page_result: dict[str, Any], source_file: str, source_kind: str) -> str:
    """生成按页导出的 Markdown 文本。"""
    text_body = page_result["text"].strip()
    escaped_text = text_body.replace("```", "'''")
    if not escaped_text:
        escaped_text = "_No text detected._"

    return "\n".join(
        [
            f"# OCR Page {page_result['page_num']}",
            "",
            "## Metadata",
            f"- Source file: `{source_file}`",
            f"- Source kind: `{source_kind}`",
            f"- Image size: `{page_result['image_width']} x {page_result['image_height']}`",
            f"- Word count: `{len(page_result['words'])}`",
            f"- Line count: `{len(page_result['lines'])}`",
            "",
            "## Artifacts",
            f"- [Page image](../pages/{page_result['image_path']})",
            f"- [Overlay image](../overlays/{page_result['overlay_path']})",
            f"- [Plain text](../texts/{page_result['text_path']})",
            "",
            "## OCR Text",
            "```text",
            escaped_text,
            "```",
            "",
        ]
    )


def run_ocr_pipeline(
    source_path: Path,
    output_dir: Path,
    source_kind: str = "pdf",
    lang: str = "eng",
    dpi: int = 200,
    tesseract_config: str = DEFAULT_TESSERACT_CONFIG,
    tesseract_cmd: str | None = None,
) -> dict[str, Any]:
    """运行完整 OCR 流程。"""
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    overlays_dir = output_dir / "overlays"
    texts_dir = output_dir / "texts"
    markdown_dir = output_dir / "markdown"

    for directory in (pages_dir, overlays_dir, texts_dir, markdown_dir):
        directory.mkdir(parents=True, exist_ok=True)

    page_image_paths = prepare_page_images(
        source_path,
        images_dir=pages_dir,
        source_kind=source_kind,
        dpi=dpi,
    )

    pages: list[dict[str, Any]] = []
    full_text_parts: list[str] = []

    for page_index, image_path in enumerate(page_image_paths, start=1):
        page_result = _ocr_image(
            image_path=image_path,
            page_num=page_index,
            lang=lang,
            tesseract_config=tesseract_config,
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
        markdown_path.write_text(
            _build_page_markdown(page_result, source_file=source_path.name, source_kind=source_kind),
            encoding="utf-8",
        )
        page_result["markdown_path"] = markdown_path.name

        full_text_parts.append(f"===== Page {page_index} =====\n{page_result['text']}\n")
        pages.append(page_result)

    full_text_path = output_dir / "full_text.txt"
    full_text_path.write_text("\n".join(full_text_parts), encoding="utf-8")

    ocr_result = {
        "source_file": source_path.name,
        "source_kind": source_kind,
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
        "markdown_dir": markdown_dir,
        "output_dir": output_dir,
    }
