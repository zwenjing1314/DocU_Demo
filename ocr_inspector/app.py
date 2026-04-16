from __future__ import annotations

"""OCR Inspector Demo 入口。"""

from pathlib import Path
import json
import os
import shutil

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from analysis_report import build_error_analysis_page
from job_skeleton import (
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    JobSkeleton,
    detect_upload_kind,
)
from ocr_engine import (
    DEFAULT_ENABLE_ROTATED_TEXT,
    DEFAULT_OCR_PADDING,
    DEFAULT_PREPROCESS_MODE,
    DEFAULT_TESSERACT_CONFIG,
    run_ocr_pipeline,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
WEB_DIR = BASE_DIR / "web"

for directory in (UPLOADS_DIR, OUTPUTS_DIR, WEB_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="OCR Inspector",
    description="上传 PDF 或图片，输出 ocr.json、叠框图、Markdown 和错误分析页。",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _count_low_confidence_words(ocr_result: dict, threshold: float) -> int:
    return sum(
        1
        for page in ocr_result["pages"]
        for word in page["words"]
        if word["confidence"] >= 0 and word["confidence"] < threshold
    )


def _write_job_manifest(job: JobSkeleton, ocr_result: dict, analysis_url: str) -> None:
    manifest = {
        "job_id": job.job_id,
        "source_file": ocr_result["source_file"],
        "source_kind": ocr_result["source_kind"],
        "created_at": ocr_result["created_at"],
        "page_count": ocr_result["page_count"],
        "config": ocr_result["config"],
        "artifacts": {
            "ocr_json": job.output_url(job.ocr_json_path),
            "full_text": job.output_url(job.full_text_path),
            "analysis_page": analysis_url,
            "pages_dir": job.output_url(job.pages_dir),
            "overlays_dir": job.output_url(job.overlays_dir),
            "texts_dir": job.output_url(job.texts_dir),
            "markdown_dir": job.output_url(job.markdown_dir),
        },
    }
    job.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.get("/")
def index() -> FileResponse:
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="缺少 web/index.html 文件。")
    return FileResponse(index_file)


@app.post("/ocr")
def upload_document(
    file: UploadFile = File(...),
    ocr_lang: str = Form(os.getenv("OCR_LANG", "eng")),
    dpi: int = Form(int(os.getenv("OCR_DPI", "300"))),
    tesseract_config: str = Form(os.getenv("TESSERACT_CONFIG", DEFAULT_TESSERACT_CONFIG)),
    preprocess_mode: str = Form(os.getenv("OCR_PREPROCESS_MODE", DEFAULT_PREPROCESS_MODE)),
    ocr_padding: int = Form(int(os.getenv("OCR_PADDING", str(DEFAULT_OCR_PADDING)))),
    enable_sparse_fallback: bool = Form(_env_bool("OCR_SPARSE_FALLBACK", True)),
    enable_rotated_text: bool = Form(_env_bool("OCR_ROTATED_TEXT", DEFAULT_ENABLE_ROTATED_TEXT)),
    suppress_graphic_artifacts: bool = Form(_env_bool("OCR_SUPPRESS_GRAPHIC_ARTIFACTS", True)),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="未检测到文件名。")

    try:
        source_kind = detect_upload_kind(file.filename, file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = JobSkeleton.create(
        uploads_root=UPLOADS_DIR,
        outputs_root=OUTPUTS_DIR,
        source_filename=file.filename,
        source_kind=source_kind,
    )

    with job.source_path.open("wb") as saved_file:
        shutil.copyfileobj(file.file, saved_file)

    try:
        result = run_ocr_pipeline(
            source_path=job.source_path,
            output_dir=job.output_dir,
            source_kind=source_kind,
            lang=ocr_lang,
            dpi=dpi,
            tesseract_config=tesseract_config,
            preprocess_mode=preprocess_mode,
            ocr_padding=ocr_padding,
            enable_sparse_fallback=enable_sparse_fallback,
            enable_rotated_text=enable_rotated_text,
            suppress_graphic_artifacts=suppress_graphic_artifacts,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"OCR 处理失败：{exc}") from exc
    finally:
        file.file.close()

    ocr_result = result["ocr_result"]
    analysis_url = job.output_url(job.analysis_index_path)
    build_error_analysis_page(
        output_path=job.analysis_index_path,
        job_id=job.job_id,
        source_file=ocr_result["source_file"],
        default_threshold=DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    )
    _write_job_manifest(job, ocr_result, analysis_url)

    page_previews = []
    for page in ocr_result["pages"]:
        page_num = page["page_num"]
        low_confidence_word_count = sum(
            1
            for word in page["words"]
            if word["confidence"] >= 0 and word["confidence"] < DEFAULT_LOW_CONFIDENCE_THRESHOLD
        )
        words_payload = [
            {
                "text": word["text"],
                "confidence": word["confidence"],
                "block_num": word["block_num"],
                "line_num": word["line_num"],
                "word_num": word["word_num"],
                "source": word.get("source", "primary"),
                "angle": word.get("angle", 0),
                "bbox": word["bbox"],
            }
            for word in page["words"]
        ]

        page_previews.append(
            {
                "page_num": page_num,
                "image_url": job.output_url(Path("pages") / page["image_path"]),
                "overlay_url": job.output_url(Path("overlays") / page["overlay_path"]),
                "text_url": job.output_url(Path("texts") / page["text_path"]),
                "markdown_url": job.output_url(Path("markdown") / page["markdown_path"]),
                "word_count": len(page["words"]),
                "line_count": len(page["lines"]),
                "rejected_word_count": len(page.get("rejected_words", [])),
                "low_confidence_word_count": low_confidence_word_count,
                "image_width": page["image_width"],
                "image_height": page["image_height"],
                "text": page["text"],
                "words": words_payload,
                "diagnostics": page.get("diagnostics", {}),
            }
        )

    low_confidence_word_count = _count_low_confidence_words(
        ocr_result,
        DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    )

    return {
        "message": "OCR 完成。",
        "job_id": job.job_id,
        "source_file": ocr_result["source_file"],
        "source_kind": ocr_result["source_kind"],
        "page_count": ocr_result["page_count"],
        "config": ocr_result["config"],
        "analysis": {
            "default_low_confidence_threshold": DEFAULT_LOW_CONFIDENCE_THRESHOLD,
            "low_confidence_word_count": low_confidence_word_count,
            "rejected_word_count": sum(
                len(page.get("rejected_words", []))
                for page in ocr_result["pages"]
            ),
            "analysis_page": analysis_url,
        },
        "downloads": {
            "ocr_json": job.output_url(job.ocr_json_path),
            "full_text": job.output_url(job.full_text_path),
            "analysis_page": analysis_url,
            "job_manifest": job.output_url(job.manifest_path),
        },
        "artifacts": {
            "pages_dir": job.output_url(job.pages_dir),
            "overlays_dir": job.output_url(job.overlays_dir),
            "texts_dir": job.output_url(job.texts_dir),
            "markdown_dir": job.output_url(job.markdown_dir),
        },
        "page_previews": page_previews,
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
