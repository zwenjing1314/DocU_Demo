from __future__ import annotations

"""OCR 总流水线入口。

基础能力已经拆到：
1. ocr_engine_1_inspector.py：PDF/图片转页、OCR、bbox、置信度、叠框图；
2. ocr_engine_2_layout_reader.py：标题、段落、列表、页眉页脚、阅读顺序、Markdown；
3. ocr_engine_3_table_to_csv.py：表格检测、结构恢复、CSV/HTML 导出。

这个文件保留主流程编排，并继续接入 4-18 的后续主题能力。
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

import pymupdf
import pytesseract

from ocr_engine_1_inspector import (
    DEFAULT_ENABLE_ROTATED_TEXT,
    DEFAULT_OCR_PADDING,
    DEFAULT_PREPROCESS_MODE,
    DEFAULT_TESSERACT_CONFIG,
    _draw_overlay,
    _normalize_preprocess_mode,
    _ocr_image,
    prepare_page_images,
    render_image_to_page,
    render_pdf_to_images,
)
from ocr_engine_2_layout_reader import (
    _analyze_document_layout,
    _build_document_markdown,
    _build_page_markdown,
)
from ocr_engine_3_table_to_csv import (
    _detect_tables_from_ocr_page,
    _extract_tables_from_pdf_page,
    _write_table_csv,
    _write_table_html,
    _write_tables_index,
)
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
