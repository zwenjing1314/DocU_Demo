from __future__ import annotations

"""Robustness Lab helpers.

第 17 步关注“同一批文档在退化输入下哪里先崩”：
1. 为页图生成扫描低对比、歪斜、阴影、弯曲、屏摄、模糊版本；
2. 汇总原始 OCR / layout / extraction / QA baseline 指标；
3. 给每个退化版本输出可解释的失败层归因。

默认模式不额外跑多轮完整 OCR，避免一次上传变得过慢；如果后续要做真实对照，
可以通过 degraded_page_evaluator 接入轻量 OCR probe。
"""

from collections import Counter
from pathlib import Path
import json
import math
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

ROBUSTNESS_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_PAGES = 3
LAYER_NAMES = ("ocr", "layout", "extraction", "reasoning")
DEGRADATION_VARIANTS = (
    "scan_low_contrast",
    "skew",
    "shadow",
    "curved",
    "screen_capture",
    "blur",
)

VARIANT_PROFILES: dict[str, dict[str, Any]] = {
    "scan_low_contrast": {
        "label": "scanned low-contrast copy",
        "topics": ["scan", "low_contrast"],
        "base_risk": {"ocr": 0.58, "layout": 0.35, "extraction": 0.42, "reasoning": 0.28},
        "diagnosis": "低对比扫描最先影响 OCR 字符边缘，后续字段抽取会因为漏词变脆。",
    },
    "skew": {
        "label": "skewed scan",
        "topics": ["skew", "rotation"],
        "base_risk": {"ocr": 0.44, "layout": 0.64, "extraction": 0.50, "reasoning": 0.30},
        "diagnosis": "歪斜会破坏行对齐和表格列关系，layout/table recovery 往往比纯文本问答更早出问题。",
    },
    "shadow": {
        "label": "uneven shadow",
        "topics": ["shadow", "lighting"],
        "base_risk": {"ocr": 0.62, "layout": 0.42, "extraction": 0.46, "reasoning": 0.32},
        "diagnosis": "阴影会让局部字块变暗，通常表现为低置信度词增多或关键字段漏识别。",
    },
    "curved": {
        "label": "curved page",
        "topics": ["curved", "page_warp"],
        "base_risk": {"ocr": 0.50, "layout": 0.68, "extraction": 0.55, "reasoning": 0.35},
        "diagnosis": "页面弯曲会让行基线和表格边界变形，主要风险在 layout 和结构化抽取。",
    },
    "screen_capture": {
        "label": "screen capture",
        "topics": ["screen_capture", "moire", "resampling"],
        "base_risk": {"ocr": 0.54, "layout": 0.38, "extraction": 0.43, "reasoning": 0.40},
        "diagnosis": "屏摄会引入重采样、色偏和摩尔纹，OCR 漏词会继续传导到问答证据不足。",
    },
    "blur": {
        "label": "motion/defocus blur",
        "topics": ["blur"],
        "base_risk": {"ocr": 0.74, "layout": 0.48, "extraction": 0.55, "reasoning": 0.42},
        "diagnosis": "模糊直接抹掉字符边缘，最典型的失败层是 OCR。",
    },
}

DegradedPageEvaluator = Callable[[dict[str, Any]], dict[str, Any]]


def _resampling(name: str, fallback: int) -> int:
    resampling = getattr(Image, "Resampling", None)
    return getattr(resampling, name, fallback) if resampling is not None else fallback


RESAMPLE_BICUBIC = _resampling("BICUBIC", Image.BICUBIC)
RESAMPLE_BILINEAR = _resampling("BILINEAR", Image.BILINEAR)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _risk_label(score: float) -> str:
    if score >= 0.70:
        return "high_risk"
    if score >= 0.45:
        return "medium_risk"
    return "low_risk"


def _most_fragile_layer(scores: dict[str, float]) -> str:
    if not scores:
        return "unknown"
    return max(LAYER_NAMES, key=lambda layer: scores.get(layer, 0.0))


def _collect_confidences(ocr_result: dict[str, Any]) -> list[float]:
    confidences: list[float] = []
    for page in ocr_result.get("pages", []):
        for word in page.get("words", []):
            confidence = _safe_float(word.get("confidence"), -1.0)
            if confidence >= 0:
                confidences.append(confidence)
    return confidences


def _baseline_metrics(ocr_result: dict[str, Any]) -> dict[str, Any]:
    pages = ocr_result.get("pages", [])
    confidences = _collect_confidences(ocr_result)
    word_count = sum(len(page.get("words", [])) for page in pages)
    low_confidence_word_count = sum(1 for confidence in confidences if confidence < 85.0)
    layout_items = [
        item
        for page in pages
        for item in page.get("layout", {}).get("items", [])
    ]
    form_analysis = ocr_result.get("form_analysis", {})
    receipt_analysis = ocr_result.get("receipt_invoice_analysis", {})
    contract_analysis = ocr_result.get("contract_schema_result", {}).get("analysis", {})
    consolidation_analysis = ocr_result.get("multi_page_consolidation_result", {}).get("analysis", {})
    chunk_analysis = ocr_result.get("layout_chunk_result", {}).get("analysis", {})
    direct_pdf_analysis = ocr_result.get("direct_pdf_structure_result", {}).get("analysis", {})
    evidence_analysis = ocr_result.get("evidence_qa_result", {}).get("analysis", {})
    query_analysis = ocr_result.get("query_extractor_result", {}).get("analysis", {})
    chart_analysis = ocr_result.get("complex_page_analysis_result", {}).get("analysis", {})

    return {
        "ocr": {
            "page_count": _safe_int(ocr_result.get("page_count"), len(pages)),
            "word_count": word_count,
            "line_count": sum(len(page.get("lines", [])) for page in pages),
            "average_confidence": round(_mean(confidences), 3),
            "low_confidence_word_count": low_confidence_word_count,
            "low_confidence_ratio": round(low_confidence_word_count / max(1, len(confidences)), 4),
            "rejected_word_count": sum(len(page.get("rejected_words", [])) for page in pages),
        },
        "layout": {
            "layout_item_count": len(layout_items),
            "heading_count": sum(1 for item in layout_items if item.get("type") == "heading"),
            "layout_table_item_count": sum(1 for item in layout_items if item.get("type") == "table"),
            "layout_chunk_count": _safe_int(chunk_analysis.get("chunk_count")),
            "table_chunk_count": _safe_int(chunk_analysis.get("table_chunk_count")),
            "native_text_page_count": _safe_int(direct_pdf_analysis.get("native_text_page_count")),
        },
        "extraction": {
            "table_count": len(ocr_result.get("tables", [])),
            "form_field_count": _safe_int(form_analysis.get("field_count")),
            "selected_option_count": _safe_int(form_analysis.get("selected_option_count")),
            "receipt_line_item_count": _safe_int(receipt_analysis.get("line_item_count")),
            "contract_field_count": _safe_int(contract_analysis.get("field_count")),
            "consolidated_item_count": _safe_int(consolidation_analysis.get("consolidated_item_count")),
            "transaction_count": _safe_int(consolidation_analysis.get("transaction_count")),
        },
        "reasoning": {
            "query_candidate_count": _safe_int(query_analysis.get("candidate_count")),
            "evidence_unit_count": _safe_int(evidence_analysis.get("unit_count")),
            "evidence_query_count": _safe_int(evidence_analysis.get("query_history_count")),
            "chart_candidate_count": _safe_int(chart_analysis.get("chart_candidate_count")),
            "chart_qa_ready": bool(chart_analysis.get("qa_ready", False)),
        },
    }


def _scan_low_contrast(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    degraded = ImageEnhance.Contrast(gray).enhance(0.72)
    degraded = ImageEnhance.Brightness(degraded).enhance(1.08).convert("RGB")

    # 用确定性小噪点模拟扫描灰尘，避免测试结果受随机数影响。
    draw = ImageDraw.Draw(degraded, "RGBA")
    step_x = max(10, degraded.width // 120)
    step_y = max(10, degraded.height // 150)
    for y in range(0, degraded.height, step_y):
        for x in range(0, degraded.width, step_x):
            seed = (x * 37 + y * 17) % 31
            if seed in {0, 7}:
                color = (30, 30, 30, 42) if seed == 0 else (255, 255, 255, 58)
                draw.ellipse((x, y, x + 1, y + 1), fill=color)
    return degraded


def _skew(image: Image.Image) -> Image.Image:
    return image.rotate(
        3.4,
        resample=RESAMPLE_BICUBIC,
        expand=False,
        fillcolor=(255, 255, 255),
    )


def _shadow(image: Image.Image) -> Image.Image:
    width, height = image.size
    gradient = Image.new("L", (width, 1))
    gradient.putdata([int(150 * (x / max(1, width - 1))) for x in range(width)])
    mask = gradient.resize((width, height)).point(lambda pixel: int(pixel * 0.52))
    dark_layer = Image.new("RGB", image.size, (42, 38, 34))
    shadowed = Image.composite(dark_layer, image, mask)

    draw = ImageDraw.Draw(shadowed, "RGBA")
    draw.ellipse(
        (int(width * 0.15), int(height * 0.05), int(width * 1.1), int(height * 0.7)),
        fill=(30, 28, 24, 26),
    )
    return shadowed


def _curved(image: Image.Image) -> Image.Image:
    width, height = image.size
    canvas = Image.new("RGB", image.size, (255, 255, 255))
    strip_width = max(4, width // 170)
    amplitude = max(6, min(30, height // 90))

    # 按竖条做正弦位移，近似书页弯曲造成的行基线变形。
    for left in range(0, width, strip_width):
        right = min(width, left + strip_width)
        offset = int(math.sin((left / max(1, width)) * math.pi * 2.0) * amplitude)
        strip = image.crop((left, 0, right, height))
        canvas.paste(strip, (left, offset))
    return canvas.filter(ImageFilter.SMOOTH)


def _screen_capture(image: Image.Image) -> Image.Image:
    width, height = image.size
    small_size = (max(1, int(width * 0.78)), max(1, int(height * 0.78)))
    degraded = image.resize(small_size, RESAMPLE_BILINEAR).resize(image.size, RESAMPLE_BILINEAR)
    degraded = ImageEnhance.Contrast(degraded).enhance(0.90)
    degraded = Image.blend(degraded, Image.new("RGB", image.size, (232, 240, 255)), 0.08)

    draw = ImageDraw.Draw(degraded, "RGBA")
    line_gap = max(5, height // 180)
    for y in range(0, height, line_gap):
        alpha = 18 if (y // line_gap) % 2 == 0 else 8
        draw.line((0, y, width, y), fill=(60, 80, 120, alpha))
    return degraded


def _blur(image: Image.Image) -> Image.Image:
    return ImageEnhance.Contrast(image.filter(ImageFilter.GaussianBlur(radius=1.8))).enhance(0.92)


DEGRADERS = {
    "scan_low_contrast": _scan_low_contrast,
    "skew": _skew,
    "shadow": _shadow,
    "curved": _curved,
    "screen_capture": _screen_capture,
    "blur": _blur,
}


def _visual_metrics(image: Image.Image) -> dict[str, float | int]:
    gray = ImageOps.grayscale(image)
    gray_stat = ImageStat.Stat(gray)
    edge_stat = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
    return {
        "width": image.width,
        "height": image.height,
        "brightness": round(gray_stat.mean[0], 3),
        "contrast": round(gray_stat.stddev[0], 3),
        "edge_strength": round(edge_stat.mean[0], 3),
    }


def _visual_metrics_for_path(image_path: Path) -> dict[str, float | int]:
    with Image.open(image_path) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        return _visual_metrics(normalized)


def _average_visual_metrics(metrics: list[dict[str, float | int]]) -> dict[str, float]:
    if not metrics:
        return {}
    numeric_keys = {
        key
        for metric in metrics
        for key, value in metric.items()
        if isinstance(value, (int, float))
    }
    return {
        key: round(_mean([float(metric.get(key, 0.0)) for metric in metrics]), 3)
        for key in sorted(numeric_keys)
    }


def _selected_page_paths(page_image_paths_by_page: dict[int, Path], max_pages: int) -> list[tuple[int, Path]]:
    selected: list[tuple[int, Path]] = []
    for page_num, path in sorted(page_image_paths_by_page.items()):
        if len(selected) >= max(1, max_pages):
            break
        if Path(path).exists():
            selected.append((int(page_num), Path(path)))
    return selected


def _generate_degraded_pages(
        *,
        variant: str,
        selected_pages: list[tuple[int, Path]],
        lab_dir: Path,
        output_dir: Path,
        degraded_page_evaluator: DegradedPageEvaluator | None,
        baseline_metrics: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, float | int]], list[dict[str, Any]], list[str]]:
    generated_pages: list[dict[str, Any]] = []
    visual_metrics: list[dict[str, float | int]] = []
    probe_metrics: list[dict[str, Any]] = []
    probe_errors: list[str] = []
    variant_dir = lab_dir / variant
    variant_dir.mkdir(parents=True, exist_ok=True)
    degrader = DEGRADERS[variant]

    for page_num, page_path in selected_pages:
        with Image.open(page_path) as source_image:
            source_rgb = ImageOps.exif_transpose(source_image).convert("RGB")
            degraded = degrader(source_rgb)
            output_path = variant_dir / f"page_{page_num:03d}.png"
            degraded.save(output_path)

        page_payload = {
            "page_num": page_num,
            "image_path": _relative_path(output_path, output_dir),
            "width": degraded.width,
            "height": degraded.height,
        }
        generated_pages.append(page_payload)
        visual_metrics.append(_visual_metrics(degraded))

        if degraded_page_evaluator is not None:
            try:
                probe_metrics.append(
                    degraded_page_evaluator(
                        {
                            "variant": variant,
                            "page_num": page_num,
                            "image_path": output_path,
                            "relative_image_path": page_payload["image_path"],
                            "baseline_metrics": baseline_metrics,
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                probe_errors.append(f"page {page_num}: {exc}")
        degraded.close()

    return generated_pages, visual_metrics, probe_metrics, probe_errors


def _loss_ratio(baseline: float, current: float) -> float:
    if baseline <= 0:
        return 0.0
    return _clamp((baseline - current) / baseline)


def _proxy_layer_scores(
        *,
        profile: dict[str, Any],
        baseline_visual: dict[str, float],
        degraded_visual: dict[str, float],
) -> dict[str, float]:
    base_risk = profile["base_risk"]
    contrast_loss = _loss_ratio(baseline_visual.get("contrast", 0.0), degraded_visual.get("contrast", 0.0))
    edge_loss = _loss_ratio(baseline_visual.get("edge_strength", 0.0), degraded_visual.get("edge_strength", 0.0))
    brightness_shift = _clamp(abs(degraded_visual.get("brightness", 0.0) - baseline_visual.get("brightness", 0.0)) / 120.0)

    # 先估 OCR/layout，再让抽取和问答继承前两层风险，模拟错误向后传播。
    ocr_score = _clamp(base_risk["ocr"] + contrast_loss * 0.24 + edge_loss * 0.26 + brightness_shift * 0.08)
    layout_score = _clamp(base_risk["layout"] + edge_loss * 0.20 + brightness_shift * 0.04)
    extraction_score = _clamp(base_risk["extraction"] + ocr_score * 0.12 + layout_score * 0.14)
    reasoning_score = _clamp(base_risk["reasoning"] + extraction_score * 0.12 + ocr_score * 0.06)
    return {
        "ocr": round(ocr_score, 3),
        "layout": round(layout_score, 3),
        "extraction": round(extraction_score, 3),
        "reasoning": round(reasoning_score, 3),
    }


def _aggregate_probe_metrics(probe_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    if not probe_metrics:
        return {}

    aggregated: dict[str, Any] = {"page_probe_count": len(probe_metrics)}
    for layer in LAYER_NAMES:
        layer_values = [
            probe.get(layer, {})
            for probe in probe_metrics
            if isinstance(probe.get(layer, {}), dict)
        ]
        if not layer_values:
            continue
        keys = {
            key
            for values in layer_values
            for key, value in values.items()
            if isinstance(value, (int, float))
        }
        aggregated[layer] = {
            key: round(_mean([float(values.get(key, 0.0)) for values in layer_values]), 3)
            for key in sorted(keys)
        }
    return aggregated


def _scores_with_probe(
        *,
        baseline_metrics: dict[str, Any],
        probe_aggregate: dict[str, Any],
        proxy_scores: dict[str, float],
) -> dict[str, float]:
    if not probe_aggregate:
        return proxy_scores

    page_probe_count = max(1, _safe_int(probe_aggregate.get("page_probe_count"), 1))
    baseline_page_count = max(1, _safe_int(baseline_metrics.get("ocr", {}).get("page_count"), 1))

    ocr_baseline = baseline_metrics.get("ocr", {})
    ocr_probe = probe_aggregate.get("ocr", {})
    word_loss = _loss_ratio(
        _safe_float(ocr_baseline.get("word_count")) / baseline_page_count,
        _safe_float(ocr_probe.get("word_count")) / page_probe_count,
    )
    confidence_loss = _loss_ratio(
        _safe_float(ocr_baseline.get("average_confidence")),
        _safe_float(ocr_probe.get("average_confidence")),
    )

    layout_baseline = baseline_metrics.get("layout", {})
    layout_probe = probe_aggregate.get("layout", {})
    layout_loss = max(
        _loss_ratio(
            _safe_float(layout_baseline.get("layout_item_count")) / baseline_page_count,
            _safe_float(layout_probe.get("layout_item_count")) / page_probe_count,
        ),
        _loss_ratio(
            _safe_float(layout_baseline.get("table_chunk_count")) / baseline_page_count,
            _safe_float(layout_probe.get("table_count")) / page_probe_count,
        ),
    )

    extraction_baseline = baseline_metrics.get("extraction", {})
    extraction_probe = probe_aggregate.get("extraction", {})
    extraction_loss = _loss_ratio(
        _safe_float(extraction_baseline.get("table_count")) / baseline_page_count,
        _safe_float(extraction_probe.get("table_count")) / page_probe_count,
    )

    reasoning_baseline = baseline_metrics.get("reasoning", {})
    reasoning_probe = probe_aggregate.get("reasoning", {})
    reasoning_loss = _loss_ratio(
        _safe_float(reasoning_baseline.get("evidence_unit_count")) / baseline_page_count,
        _safe_float(reasoning_probe.get("evidence_unit_count")) / page_probe_count,
    )

    actual_scores = {
        "ocr": max(proxy_scores["ocr"], word_loss, confidence_loss),
        "layout": max(proxy_scores["layout"], layout_loss),
        "extraction": max(proxy_scores["extraction"], extraction_loss),
        "reasoning": max(proxy_scores["reasoning"], reasoning_loss),
    }
    return {layer: round(_clamp(score), 3) for layer, score in actual_scores.items()}


def _comparison_payload(
        *,
        scores: dict[str, float],
        baseline_metrics: dict[str, Any],
        probe_aggregate: dict[str, Any],
) -> dict[str, Any]:
    return {
        layer: {
            "risk_score": scores[layer],
            "risk_label": _risk_label(scores[layer]),
            "baseline": baseline_metrics.get(layer, {}),
            "probe": probe_aggregate.get(layer, {}) if probe_aggregate else {},
        }
        for layer in LAYER_NAMES
    }


def build_robustness_lab_result(
        ocr_result: dict[str, Any],
        *,
        page_image_paths_by_page: dict[int, Path],
        output_dir: Path,
        max_pages: int = DEFAULT_MAX_PAGES,
        degraded_page_evaluator: DegradedPageEvaluator | None = None,
) -> dict[str, Any]:
    """生成退化图片和 degradation report。"""
    lab_dir = output_dir / "robustness_lab"
    lab_dir.mkdir(parents=True, exist_ok=True)

    selected_pages = _selected_page_paths(page_image_paths_by_page, max_pages=max_pages)
    baseline_metrics = _baseline_metrics(ocr_result)
    baseline_visual = _average_visual_metrics(
        [_visual_metrics_for_path(page_path) for _, page_path in selected_pages]
    )

    variants: list[dict[str, Any]] = []
    for variant in DEGRADATION_VARIANTS:
        profile = VARIANT_PROFILES[variant]
        generated_pages, visual_metrics, probe_metrics, probe_errors = _generate_degraded_pages(
            variant=variant,
            selected_pages=selected_pages,
            lab_dir=lab_dir,
            output_dir=output_dir,
            degraded_page_evaluator=degraded_page_evaluator,
            baseline_metrics=baseline_metrics,
        )
        degraded_visual = _average_visual_metrics(visual_metrics)
        proxy_scores = _proxy_layer_scores(
            profile=profile,
            baseline_visual=baseline_visual,
            degraded_visual=degraded_visual,
        )
        probe_aggregate = _aggregate_probe_metrics(probe_metrics)
        scores = _scores_with_probe(
            baseline_metrics=baseline_metrics,
            probe_aggregate=probe_aggregate,
            proxy_scores=proxy_scores,
        )
        likely_failure_layer = _most_fragile_layer(scores)

        variants.append(
            {
                "variant": variant,
                "label": profile["label"],
                "topics": profile["topics"],
                "generated_pages": generated_pages,
                "visual_metrics": degraded_visual,
                "comparison_mode": "ocr_probe_plus_visual_proxy" if probe_aggregate else "visual_proxy",
                "comparison": _comparison_payload(
                    scores=scores,
                    baseline_metrics=baseline_metrics,
                    probe_aggregate=probe_aggregate,
                ),
                "layer_risk_scores": scores,
                "likely_failure_layer": likely_failure_layer,
                "diagnosis": profile["diagnosis"],
                "probe_errors": probe_errors,
            }
        )

    generated_page_count = sum(len(variant["generated_pages"]) for variant in variants)
    layer_risk_scores = {
        layer: round(_mean([variant["layer_risk_scores"][layer] for variant in variants]), 3)
        for layer in LAYER_NAMES
    }
    failure_distribution = Counter(variant["likely_failure_layer"] for variant in variants)

    return {
        "schema_version": ROBUSTNESS_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", ""),
        "status": "ok" if generated_page_count else "no_page_images",
        "lab_dir": _relative_path(lab_dir, output_dir),
        "baseline_metrics": baseline_metrics,
        "baseline_visual_metrics": baseline_visual,
        "variants": variants,
        "analysis": {
            "variant_count": len(variants),
            "generated_page_count": generated_page_count,
            "evaluated_page_count": len(selected_pages),
            "evaluation_mode": "ocr_probe_plus_visual_proxy" if degraded_page_evaluator else "visual_proxy",
            "layer_risk_scores": layer_risk_scores,
            "most_fragile_layer": _most_fragile_layer(layer_risk_scores),
            "failure_distribution": dict(sorted(failure_distribution.items())),
        },
        "demo_scope": {
            "does": [
                "generates degraded document page images for robustness comparison",
                "compares OCR, layout, extraction, and QA risk under each degradation",
                "marks the most likely failing layer for each variant",
            ],
            "does_not": [
                "run a full expensive re-OCR pipeline for every variant by default",
                "prove benchmark-grade accuracy without a labeled gold set",
            ],
        },
    }


def write_degradation_report_json(result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
