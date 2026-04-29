from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_UPLOAD_EXTENSIONS = SUPPORTED_PDF_EXTENSIONS | SUPPORTED_IMAGE_EXTENSIONS

SUPPORTED_PDF_CONTENT_TYPES = {"application/pdf", "application/octet-stream", None}
SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/bmp",
    "image/tiff",
    "image/webp",
    "application/octet-stream",
    None,
}

DEFAULT_LOW_CONFIDENCE_THRESHOLD = 85.0


def detect_upload_kind(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()

    if suffix in SUPPORTED_PDF_EXTENSIONS and content_type in SUPPORTED_PDF_CONTENT_TYPES:
        return "pdf"

    if suffix in SUPPORTED_IMAGE_EXTENSIONS and content_type in SUPPORTED_IMAGE_CONTENT_TYPES:
        return "image"

    raise ValueError("只支持 PDF 或常见图片格式（PNG/JPG/JPEG/BMP/TIFF/WEBP）。")


@dataclass(slots=True)
class JobSkeleton:
    job_id: str
    source_kind: str
    upload_dir: Path
    output_dir: Path
    source_path: Path
    pages_dir: Path
    overlays_dir: Path
    texts_dir: Path
    markdown_dir: Path
    tables_dir: Path
    analysis_dir: Path
    ocr_json_path: Path
    full_text_path: Path
    document_markdown_path: Path
    form_json_path: Path
    receipt_json_path: Path
    router_json_path: Path
    bundle_json_path: Path
    review_json_path: Path
    query_json_path: Path
    contract_schema_json_path: Path
    multi_page_consolidation_json_path: Path
    layout_chunks_json_path: Path
    direct_pdf_structure_json_path: Path
    evidence_qa_json_path: Path
    complex_page_analysis_json_path: Path
    review_workbench_revisions_json_path: Path
    manifest_path: Path
    tables_index_path: Path
    analysis_index_path: Path
    bundle_segments_dir: Path
    review_overlays_dir: Path

    @classmethod
    def create(
        cls,
        *,
        uploads_root: Path,
        outputs_root: Path,
        source_filename: str,
        source_kind: str,
    ) -> "JobSkeleton":
        job_id = uuid4().hex[:12]
        upload_dir = uploads_root / job_id
        output_dir = outputs_root / job_id

        upload_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        pages_dir = output_dir / "pages"
        overlays_dir = output_dir / "overlays"
        texts_dir = output_dir / "texts"
        markdown_dir = output_dir / "markdown"
        tables_dir = output_dir / "tables"
        bundle_segments_dir = output_dir / "bundle_segments"
        review_overlays_dir = output_dir / "review_overlays"
        analysis_dir = output_dir / "analysis"

        for path in (pages_dir, overlays_dir, texts_dir, markdown_dir, tables_dir, bundle_segments_dir, review_overlays_dir, analysis_dir):
            path.mkdir(parents=True, exist_ok=True)

        suffix = Path(source_filename).suffix.lower()
        normalized_suffix = suffix if suffix in SUPPORTED_UPLOAD_EXTENSIONS else ".bin"
        source_path = upload_dir / f"source{normalized_suffix}"

        return cls(
            job_id=job_id,
            source_kind=source_kind,
            upload_dir=upload_dir,
            output_dir=output_dir,
            source_path=source_path,
            pages_dir=pages_dir,
            overlays_dir=overlays_dir,
            texts_dir=texts_dir,
            markdown_dir=markdown_dir,
            tables_dir=tables_dir,
            analysis_dir=analysis_dir,
            ocr_json_path=output_dir / "ocr.json",
            full_text_path=output_dir / "full_text.txt",
            document_markdown_path=output_dir / "document.md",
            form_json_path=output_dir / "form.json",
            receipt_json_path=output_dir / "receipt_invoice.json",
            router_json_path=output_dir / "document_router.json",
            bundle_json_path=output_dir / "bundle_splitter.json",
            review_json_path=output_dir / "signature_handwriting_review.json",
            query_json_path=output_dir / "query_extractor.json",
            contract_schema_json_path=output_dir / "contract_schema.json",
            multi_page_consolidation_json_path=output_dir / "multi_page_consolidation.json",
            layout_chunks_json_path=output_dir / "layout_chunks.json",
            direct_pdf_structure_json_path=output_dir / "direct_pdf_structure.json",
            evidence_qa_json_path=output_dir / "evidence_qa.json",
            complex_page_analysis_json_path=output_dir / "complex_page_analysis.json",
            review_workbench_revisions_json_path=output_dir / "review_workbench_revisions.json",
            manifest_path=output_dir / "job_manifest.json",
            tables_index_path=tables_dir / "index.html",
            analysis_index_path=analysis_dir / "index.html",
            bundle_segments_dir=bundle_segments_dir,
            review_overlays_dir=review_overlays_dir,
        )

    def output_url(self, relative_path: str | Path) -> str:
        if isinstance(relative_path, Path):
            if relative_path.is_absolute():
                relative = relative_path.relative_to(self.output_dir).as_posix()
            else:
                relative = relative_path.as_posix()
        else:
            relative = str(Path(relative_path)).replace("\\", "/")
        return f"/outputs/{self.job_id}/{relative}"
