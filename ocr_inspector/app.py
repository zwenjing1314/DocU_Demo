from __future__ import annotations

"""OCR Inspector - Ubuntu 版 Demo 入口。

运行后提供两个主要能力：
1. GET /      -> 简单上传页面
2. POST /ocr  -> 上传 PDF，执行 OCR，返回结果链接

最小交付：
- ocr.json
- 每页叠框图
- 纯文本导出
"""

from pathlib import Path
from uuid import uuid4
import os
import shutil

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ocr_engine import DEFAULT_TESSERACT_CONFIG, run_ocr_pipeline

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
WEB_DIR = BASE_DIR / "web"

# 启动时确保目录存在
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="OCR Inspector",
    description="上传 PDF，输出 ocr.json、叠框图和纯文本。",
    version="1.0.0",
)

# 这个 Demo 不涉及复杂鉴权，先放开跨域，便于本地调试。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 把 outputs 目录挂成静态资源目录，这样浏览器可以直接访问导出的 json / txt / png。
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


@app.get("/")
def index() -> FileResponse:
    """返回上传页面。"""
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="缺少 web/index.html 文件。")
    return FileResponse(index_file)


@app.post("/ocr")
def upload_pdf(
    file: UploadFile = File(...),
    ocr_lang: str = Form(os.getenv("OCR_LANG", "eng")),
    dpi: int = Form(int(os.getenv("OCR_DPI", "200"))),
    tesseract_config: str = Form(os.getenv("TESSERACT_CONFIG", DEFAULT_TESSERACT_CONFIG)),
):
    """上传 PDF 并运行 OCR。

    这里使用同步 def，而不是 async def，主要是因为 OCR 和 PDF 渲染都是 CPU 密集型任务，
    用同步写法更直观，也方便直接用 UploadFile.file 做文件流复制。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未检测到文件名。")

    # 这里既检查文件后缀，也兼容某些浏览器没有正确设置 content-type 的情况。
    is_pdf_name = file.filename.lower().endswith(".pdf")
    is_pdf_type = file.content_type in {"application/pdf", "application/octet-stream", None}
    if not (is_pdf_name and is_pdf_type):
        raise HTTPException(status_code=400, detail="只支持上传 PDF 文件。")

    job_id = uuid4().hex[:12]
    job_upload_dir = UPLOADS_DIR / job_id
    job_output_dir = OUTPUTS_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)

    # 原始文件统一保存为 original.pdf，避免文件名里有空格、中文或特殊字符造成麻烦。
    saved_pdf_path = job_upload_dir / "original.pdf"
    with saved_pdf_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = run_ocr_pipeline(
            pdf_path=saved_pdf_path,
            output_dir=job_output_dir,
            lang=ocr_lang,
            dpi=dpi,
            tesseract_config=tesseract_config,
        )
    except Exception as exc:  # noqa: BLE001 - Demo 里统一转成 HTTP 错误更实用
        raise HTTPException(status_code=500, detail=f"OCR 处理失败：{exc}") from exc
    finally:
        file.file.close()

    ocr_result = result["ocr_result"]

    page_previews = []
    for page in ocr_result["pages"]:
        page_num = page["page_num"]
        words_payload = [
            {
                "text": word["text"],
                "confidence": word["confidence"],
                "block_num": word["block_num"],
                "line_num": word["line_num"],
                "word_num": word["word_num"],
                "bbox": word["bbox"],
            }
            for word in page["words"]
        ]

        page_previews.append(
            {
                "page_num": page_num,
                "image_url": f"/outputs/{job_id}/pages/{page['image_path']}",
                "overlay_url": f"/outputs/{job_id}/overlays/{page['overlay_path']}",
                "text_url": f"/outputs/{job_id}/texts/{page['text_path']}",
                "word_count": len(page["words"]),
                "line_count": len(page["lines"]),
                "image_width": page["image_width"],
                "image_height": page["image_height"],
                "words": words_payload,
            }
        )

    # 返回结果链接，而不是直接把完整 OCR JSON 塞进响应体，避免响应过大。
    return {
        "message": "OCR 完成。",
        "job_id": job_id,
        "source_file": ocr_result["source_file"],
        "page_count": ocr_result["page_count"],
        "config": ocr_result["config"],
        "downloads": {
            "ocr_json": f"/outputs/{job_id}/ocr.json",
            "full_text": f"/outputs/{job_id}/full_text.txt",
        },
        "page_previews": page_previews,
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    """简单健康检查接口。"""
    return {"status": "ok"}
