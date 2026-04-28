from __future__ import annotations

"""Bundle splitter helpers.

这个模块专门负责：
1. 对多文档 PDF / 扫描包做页级分析；
2. 识别每个子文档的起止页；
3. 导出切分后的子 PDF 和 JSON 段信息。

这里继续拆独立文件，是为了避免把 bundle 识别逻辑再塞回 ocr_engine.py。
"""

from collections import Counter
from pathlib import Path
import json
import re
from typing import Any

import pymupdf

from ocr_engine_6_router import build_mixed_document_router_result

BUNDLE_SPLITTER_SCHEMA_VERSION = "1.0"
SHORT_DOCUMENT_LABELS = {"invoice", "receipt", "form", "id"}
STARTER_KEYWORDS_BY_LABEL: dict[str, tuple[str, ...]] = {
    "invoice": ("invoice", "tax invoice", "invoice no", "invoice #", "发票", "发票号码"),
    "receipt": ("receipt", "sales receipt", "收据", "小票"),
    "form": ("application form", "registration form", "application", "form", "申请表", "登记表", "申请人"),
    "report": ("report", "analysis report", "summary", "overview", "annual report", "quarterly report", "报告", "分析报告", "摘要"),
    "id": ("identity card", "id card", "passport", "resident identity card", "身份证", "居民身份证", "护照"),
}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _canonicalize(text: str) -> str:
    return _normalize_text(text).casefold()


def _sorted_page_lines(page: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        page.get("lines", []),
        key=lambda item: (item["bbox"]["top"], item["bbox"]["left"]),
    )


def _page_tables(ocr_result: dict[str, Any], page_num: int) -> list[dict[str, Any]]:
    return [
        dict(table)
        for table in ocr_result.get("tables", [])
        if table.get("page_num") == page_num
    ]


def _build_page_level_ocr_result(ocr_result: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """构造单页视角的 OCR 结果，复用现有文档路由模块做页级分类。"""
    return {
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": ocr_result.get("source_kind", "pdf"),
        "pages": [page],
        "tables": _page_tables(ocr_result, page.get("page_num", 1)),
    }


def _top_page_text(page: dict[str, Any], *, limit: int = 4) -> str:
    lines = _sorted_page_lines(page)[:limit]
    return "\n".join(_normalize_text(line.get("text", "")) for line in lines if _normalize_text(line.get("text", "")))


def _count_keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _build_page_classification(ocr_result: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    page_router = build_mixed_document_router_result(_build_page_level_ocr_result(ocr_result, page))
    top_text = _top_page_text(page)
    lowered_top_text = _canonicalize(top_text)
    label = page_router["label"]
    starter_hits = _count_keyword_hits(lowered_top_text, STARTER_KEYWORDS_BY_LABEL.get(label, ()))
    analysis = page_router.get("analysis", {})

    start_signal_score = 0.0
    if starter_hits:
        start_signal_score += 2.5 + min(1.5, (starter_hits - 1) * 0.5)
    if label in {"form", "id"} and analysis.get("key_value_line_count", 0) >= 3:
        start_signal_score += 1.5
    if label in {"invoice", "receipt"} and (
        analysis.get("item_table_count", 0) > 0 or analysis.get("amount_line_count", 0) >= 2
    ):
        start_signal_score += 1.0
    if label == "report" and starter_hits and analysis.get("heading_count", 0) >= 1:
        start_signal_score += 1.0
    if analysis.get("confidence", 0.0) >= 0.75:
        start_signal_score += 0.5

    return {
        "page_num": page.get("page_num", 1),
        "label": label,
        "confidence": analysis.get("confidence", 0.0),
        "top_text": top_text,
        "top_text_keyword_hits": starter_hits,
        "start_signal_score": round(start_signal_score, 2),
        "analysis": analysis,
        "matched_signals": page_router.get("matched_signals", []),
    }


def _should_start_new_segment(
    previous_page: dict[str, Any],
    current_page: dict[str, Any],
    current_segment: dict[str, Any],
) -> bool:
    """根据页标签变化和起始页信号判断是否需要切出新的子文档。"""
    boundary_score = 0.0

    if current_page["label"] != previous_page["label"]:
        boundary_score += 2.5

    if current_page["start_signal_score"] >= 2.5:
        boundary_score += 1.5
    if current_page["start_signal_score"] >= 3.5:
        boundary_score += 1.0

    # 发票 / 收据 / 表单 / 证件类通常是短文档，若下一页再次出现明显首页信号，优先拆段。
    if current_page["label"] in SHORT_DOCUMENT_LABELS and current_page["start_signal_score"] >= 2.5:
        boundary_score += 1.5

    # 同类 report 更容易跨页连续，默认压低切分倾向，除非它出现很强的首页标题信号。
    if previous_page["label"] == current_page["label"] == "report":
        boundary_score -= 2.0
        if current_page["top_text_keyword_hits"] and current_segment["page_count"] >= 2:
            boundary_score += 2.5

    if previous_page["label"] == current_page["label"] and current_page["start_signal_score"] < 2.5:
        boundary_score -= 1.5

    if current_page["confidence"] < 0.45 and current_page["label"] == previous_page["label"]:
        boundary_score -= 1.0

    return boundary_score >= 3.0


def _finalize_segment(segment: dict[str, Any]) -> dict[str, Any]:
    label_counter = Counter(page["label"] for page in segment["pages"])
    dominant_label = max(
        label_counter,
        key=lambda label: (label_counter[label], sum(page["confidence"] for page in segment["pages"] if page["label"] == label)),
    )
    confidences = [page["confidence"] for page in segment["pages"]]
    segment["label"] = dominant_label
    segment["confidence"] = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    segment["page_labels"] = [page["label"] for page in segment["pages"]]
    segment["page_count"] = segment["end_page"] - segment["start_page"] + 1
    return segment


def _build_segments(page_classifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not page_classifications:
        return []

    segments: list[dict[str, Any]] = []
    current_segment = {
        "start_page": page_classifications[0]["page_num"],
        "end_page": page_classifications[0]["page_num"],
        "pages": [page_classifications[0]],
        "page_count": 1,
    }

    for previous_page, current_page in zip(page_classifications, page_classifications[1:]):
        if _should_start_new_segment(previous_page, current_page, current_segment):
            current_segment["end_page"] = previous_page["page_num"]
            segments.append(_finalize_segment(current_segment))
            current_segment = {
                "start_page": current_page["page_num"],
                "end_page": current_page["page_num"],
                "pages": [current_page],
                "page_count": 1,
            }
            continue

        current_segment["end_page"] = current_page["page_num"]
        current_segment["pages"].append(current_page)
        current_segment["page_count"] = len(current_segment["pages"])

    segments.append(_finalize_segment(current_segment))
    return segments


def _relative_to_output(path: Path, output_dir: Path) -> str:
    return path.relative_to(output_dir).as_posix()


def _export_segment_pdf(
    *,
    source_path: Path,
    output_path: Path,
    start_page: int,
    end_page: int,
) -> None:
    source_document = pymupdf.open(source_path)
    try:
        segment_document = pymupdf.open()
        try:
            segment_document.insert_pdf(source_document, from_page=start_page - 1, to_page=end_page - 1)
            segment_document.save(str(output_path))
        finally:
            segment_document.close()
    finally:
        source_document.close()


def _write_segment_json(segment: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(segment, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_bundle_splitter_result(
    ocr_result: dict[str, Any],
    *,
    source_path: Path,
    output_dir: Path,
    source_kind: str,
) -> dict[str, Any]:
    """生成 bundle 切分结果，并在 PDF 场景下导出子文档文件。"""
    bundle_dir = output_dir / "bundle_segments"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    page_classifications = [
        _build_page_classification(ocr_result, page)
        for page in ocr_result.get("pages", [])
    ]
    segments = _build_segments(page_classifications)

    exported_pdf_count = 0
    for index, segment in enumerate(segments, start=1):
        segment_id = f"segment_{index:02d}"
        segment["segment_id"] = segment_id

        json_path = bundle_dir / f"{segment_id}.json"
        segment["json_path"] = _relative_to_output(json_path, output_dir)

        pdf_path = bundle_dir / f"{segment_id}_{segment['label']}_p{segment['start_page']:03d}-{segment['end_page']:03d}.pdf"
        if source_kind == "pdf" and source_path.exists():
            _export_segment_pdf(
                source_path=source_path,
                output_path=pdf_path,
                start_page=segment["start_page"],
                end_page=segment["end_page"],
            )
            segment["pdf_path"] = _relative_to_output(pdf_path, output_dir)
            exported_pdf_count += 1
        else:
            segment["pdf_path"] = ""

        # 这里把单段元数据单独写出来，方便后续只消费某一个子文档。
        _write_segment_json(
            {
                "segment_id": segment["segment_id"],
                "label": segment["label"],
                "confidence": segment["confidence"],
                "start_page": segment["start_page"],
                "end_page": segment["end_page"],
                "page_count": segment["page_count"],
                "page_labels": segment["page_labels"],
                "pdf_path": segment["pdf_path"],
            },
            json_path,
        )

    return {
        "schema_version": BUNDLE_SPLITTER_SCHEMA_VERSION,
        "source_file": ocr_result.get("source_file", ""),
        "source_kind": source_kind,
        "page_classifications": page_classifications,
        "segments": [
            {
                "segment_id": segment["segment_id"],
                "label": segment["label"],
                "confidence": segment["confidence"],
                "start_page": segment["start_page"],
                "end_page": segment["end_page"],
                "page_count": segment["page_count"],
                "page_labels": segment["page_labels"],
                "json_path": segment["json_path"],
                "pdf_path": segment["pdf_path"],
            }
            for segment in segments
        ],
        "analysis": {
            "page_count": len(page_classifications),
            "segment_count": len(segments),
            "detected_bundle": len(segments) > 1,
            "exported_pdf_count": exported_pdf_count,
        },
    }


def write_bundle_splitter_json(bundle_result: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(
        json.dumps(bundle_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
