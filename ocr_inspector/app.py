from __future__ import annotations

"""OCR Inspector Demo 入口。"""

import copy
import hashlib
from collections import OrderedDict
from pathlib import Path
import json
import os
import shutil
from threading import Lock
from typing import Any

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
from ocr_engine_9_query import answer_document_query, load_query_json, write_query_json

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
WEB_DIR = BASE_DIR / "web"
REQUEST_CACHE_SIZE = 16

for directory in (UPLOADS_DIR, OUTPUTS_DIR, WEB_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="OCR Inspector",
    description="上传 PDF 或图片，输出 ocr.json、叠框图、Markdown 和错误分析页。",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 这行代码的核心作用是：将服务器上的 outputs 目录挂载为静态文件服务，使得客户端可以通过 HTTP URL 直接访问 OCR 处理生成的所有输出文件。
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

# 创建了一个全局的锁对象 _request_state_lock，用于保护下面三个共享状态变量：
_request_state_lock = Lock()  # 锁的初始化
_active_ocr_job_id: str | None = None  # 当前正在执行 OCR 任务的 job ID
_inflight_request_keys: set[str] = set()  # 正在处理中的请求指纹集合
_completed_response_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()  # 已完成请求的响应缓存（OrderedDict）


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# 生成器表达式（Generator Expression） 的统计函数
def _count_low_confidence_words(ocr_result: dict, threshold: float) -> int:
    return sum(
        1  # ← 每次条件满足时，生成器就"产出"一个 1
        for page in ocr_result["pages"]  # 外层迭代器：逐个取出页面
        for word in page["words"]  # 内层迭代器：逐个取出单词
        if 0 <= word["confidence"] < threshold
    )


# 为每次 OCR 任务生成一个"清单文件"（manifest），记录该任务的所有关键信息和输出文件的访问地址，方便后续追溯、调试和复用。
def _write_job_manifest(
        job: JobSkeleton,  # 任务骨架对象，包含所有路径信息
        ocr_result: dict,  # OCR 处理结果，包含源文件、配置等元数据
        analysis_url: str,  # 错误分析页面的 URL
        request_fingerprint: str,  # 请求指纹（文件哈希+参数的唯一标识）
) -> None:
    manifest = {
        "job_id": job.job_id,  # 任务唯一ID，如 "b86a192c21f7"
        "request_fingerprint": request_fingerprint,  # 请求指纹（用于去重和缓存）
        "source_file": ocr_result["source_file"],   # 源文件名，如 "source.pdf"
        "source_kind": ocr_result["source_kind"],  # 文件类型："pdf" 或 "image"
        "created_at": ocr_result["created_at"],  # 创建时间戳
        "page_count": ocr_result["page_count"],  # 页数
        "config": ocr_result["config"],   # OCR 配置参
        "artifacts": {  # 输出产物 URLs
            "ocr_json": job.output_url(job.ocr_json_path),
            "full_text": job.output_url(job.full_text_path),
            "document_markdown": job.output_url(job.document_markdown_path),
            "tables_index": job.output_url(job.tables_index_path),
            "form_json": job.output_url(job.form_json_path),
            "receipt_json": job.output_url(job.receipt_json_path),
            "router_json": job.output_url(job.router_json_path),
            "bundle_json": job.output_url(job.bundle_json_path),
            "review_json": job.output_url(job.review_json_path),
            "query_json": job.output_url(job.query_json_path),
            "analysis_page": analysis_url,
            "pages_dir": job.output_url(job.pages_dir),
            "overlays_dir": job.output_url(job.overlays_dir),
            "texts_dir": job.output_url(job.texts_dir),
            "markdown_dir": job.output_url(job.markdown_dir),
            "tables_dir": job.output_url(job.tables_dir),
            "bundle_segments_dir": job.output_url(job.bundle_segments_dir),
            "review_overlays_dir": job.output_url(job.review_overlays_dir),
        },
    }
    job.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# 将用户上传的文件保存到服务器磁盘，同时计算文件的 SHA256 哈希值（用于唯一标识文件内容），最后返回这个哈希值。
def _save_upload(upload_file: UploadFile, destination: Path) -> str:
    digest = hashlib.sha256()
    with destination.open("wb") as saved_file:
        while chunk := upload_file.file.read(1024 * 1024):
            saved_file.write(chunk)  # 写入磁盘
            digest.update(chunk)  # 增量哈希
    return digest.hexdigest()  # 返回哈希值


# ：根据文件内容和所有 OCR 参数生成一个唯一的"请求指纹"（哈希值），用于识别完全相同的请求，实现智能缓存和去重。
def _build_request_fingerprint(
        *,  # ← 强制关键字参数
        file_hash: str,
        source_kind: str,
        ocr_lang: str,
        dpi: int,
        tesseract_config: str,
        preprocess_mode: str,
        ocr_padding: int,
        enable_sparse_fallback: bool,
        enable_rotated_text: bool,
        suppress_graphic_artifacts: bool,
) -> str:
    payload = {
        "file_hash": file_hash,
        "source_kind": source_kind,
        "ocr_lang": ocr_lang,
        "dpi": dpi,
        "tesseract_config": tesseract_config,
        "preprocess_mode": preprocess_mode,
        "ocr_padding": max(0, int(ocr_padding)),  # 确保是非负整数
        "enable_sparse_fallback": bool(enable_sparse_fallback),  # 确保是布尔值
        "enable_rotated_text": bool(enable_rotated_text),
        "suppress_graphic_artifacts": bool(suppress_graphic_artifacts),
    }
    #  序列化为 JSON
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)  # sort_keys=True：按键名排序
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _get_cached_response(request_fingerprint: str) -> dict[str, Any] | None:  # 读取缓存
    """
    工作流程：
    进入 with 块时自动获取锁：其他线程如果也想访问缓存，会被阻塞等待
    从缓存中查找：根据请求指纹查找是否有已完成的响应
    更新访问顺序：move_to_end 将最近访问的项移到末尾（LRU 策略）
    返回深拷贝：避免外部修改影响缓存中的数据
    退出 with 块时自动释放锁：其他等待的线程可以继续执行
    为什么需要锁？
    OrderedDict 不是线程安全的
    get 和 move_to_end 两个操作必须是原子的（atomic），否则可能出现竞态条件
    """
    with _request_state_lock:
        cached = _completed_response_cache.get(request_fingerprint)
        if cached is None:
            return None
        _completed_response_cache.move_to_end(request_fingerprint)
        return copy.deepcopy(cached)


def _try_acquire_global_ocr_slot(job_id: str) -> bool:  # 获取全局 OCR 槽位
    """
    工作原理：
    检查并设置是原子操作：在锁的保护下，先检查 _active_ocr_job_id 是否为空，如果为空则设置为当前 job_id
    实现并发控制：确保同一时间只有一个 OCR 任务在执行
    为什么需要锁？
    如果没有锁，两个线程可能同时检测到 _active_ocr_job_id is None，然后都设置自己的 job_id，导致并发执行多个 OCR 任务
    这就是经典的 "检查-然后-设置"（Check-Then-Act）竞态条件
    """
    global _active_ocr_job_id
    with _request_state_lock:
        if _active_ocr_job_id is not None:
            return False
        _active_ocr_job_id = job_id
        return True
# 数据流向示例：
# 线程A: [获取锁] → 检查(None) → 设置为"job_A" → [释放锁] → 返回True
# 线程B: [等待锁...] → [获取锁] → 检查("job_A") → 返回False → [释放锁]


def _release_global_ocr_slot(job_id: str) -> None:
    """
    工作原理：
    验证身份：只有持有槽位的 job 才能释放它（防止错误释放）
    原子性清除：在锁保护下将 _active_ocr_job_id 设为 None
    为什么需要锁？
    确保写入操作的可见性：一个线程的修改对其他线程立即可见
    防止部分写入导致的脏数据
    """
    global _active_ocr_job_id
    with _request_state_lock:
        if _active_ocr_job_id == job_id:
            _active_ocr_job_id = None


def _try_start_request(request_fingerprint: str) -> bool:
    """
    工作原理：
    防重复提交：检查同一个文件+参数的组合是否已经在处理中
    原子性添加：检查和添加必须在锁内完成
    实际应用场景： 用户上传相同的 PDF，使用相同的参数，第一次请求还在处理时，第二次请求会被拒绝（返回 409 冲突）
    """
    with _request_state_lock:
        if request_fingerprint in _inflight_request_keys:
            return False
        _inflight_request_keys.add(request_fingerprint)
        return True


def _finish_request(
        request_fingerprint: str,
        response_payload: dict[str, Any] | None = None,
) -> None:
    """
    工作流程：
    清理进行中标记：从 _inflight_request_keys 中移除
    缓存响应：将结果存入 _completed_response_cache
    更新访问顺序：移到末尾表示最近使用
    LRU 淘汰：如果缓存超过 16 个（REQUEST_CACHE_SIZE），删除最久未使用的
    为什么需要锁？
    同时修改了两个数据结构（_inflight_request_keys 和 _completed_response_cache）
    必须保证这两个操作的原子性，否则可能出现：已从 inflight 移除但还未加入缓存，此时另一个线程查询会误判
    """
    with _request_state_lock:
        _inflight_request_keys.discard(request_fingerprint)
        if response_payload is None:
            return
        _completed_response_cache[request_fingerprint] = copy.deepcopy(response_payload)
        _completed_response_cache.move_to_end(request_fingerprint)
        while len(_completed_response_cache) > REQUEST_CACHE_SIZE:
            _completed_response_cache.popitem(last=False)


def _cleanup_job(job: JobSkeleton) -> None:  # 任务骨架对象，包含所有相关路径信息
    shutil.rmtree(job.upload_dir, ignore_errors=True)
    shutil.rmtree(job.output_dir, ignore_errors=True)


# 将 OCR 处理的原始结果转换为前端友好的 API 响应格式，包含完整的元数据、统计信息、下载链接和页面预览。
def _build_response_payload(
        *,
        job: JobSkeleton,
        ocr_result: dict[str, Any],
        analysis_url: str,
        cached: bool,
) -> dict[str, Any]:
    page_previews = []
    review_pages_by_num = {
        page["page_num"]: page
        for page in ocr_result.get("signature_handwriting_review_result", {}).get("pages", [])
    }
    for page in ocr_result["pages"]:
        page_num = page["page_num"]
        review_page = review_pages_by_num.get(page_num, {})
        # 计算单页的低置信度单词数
        low_confidence_word_count = sum(
            1
            for word in page["words"]
            if word["confidence"] >= 0 and word["confidence"] < DEFAULT_LOW_CONFIDENCE_THRESHOLD
        )
        # 构建单词负载数据
        # 这是一个列表推导式，为每个单词创建一个精简版的字典
        # 目的是过滤掉不必要的字段，只保留前端需要的信息
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

        # 构建页面预览列表
        page_previews.append(
            {
                "page_num": page_num,
                "image_url": job.output_url(Path("pages") / page["image_path"]),
                "overlay_url": job.output_url(Path("overlays") / page["overlay_path"]),
                "review_overlay_url": (
                    job.output_url(review_page["review_overlay_path"])
                    if review_page.get("review_overlay_path")
                    else ""
                ),
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
                "review_counts": {
                    "signature_region_count": len(review_page.get("signature_regions", [])),
                    "handwriting_region_count": len(review_page.get("handwriting_regions", [])),
                    "suspicious_field_count": len(review_page.get("suspicious_fields", [])),
                },
                "review_summary": review_page,
                "table_count": len(page.get("tables", [])),
                "tables": [
                    {
                        "table_id": table["table_id"],
                        "source": table["source"],
                        "row_count": table["row_count"],
                        "col_count": table["col_count"],
                        "csv_url": job.output_url(Path("tables") / table["csv_path"]),
                        "html_url": job.output_url(Path("tables") / table["html_path"]),
                    }
                    for table in page.get("tables", [])
                ],
            }
        )

    # 计算全局统计信息
    low_confidence_word_count = _count_low_confidence_words(
        ocr_result,
        DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    )

    # 构建最终响应字典
    return {
        "message": "复用已有 OCR 结果。" if cached else "OCR 完成。",
        "cached": cached,
        "job_id": job.job_id,  # 唯一标识这次处理任务
        "source_file": ocr_result["source_file"],
        "source_kind": ocr_result["source_kind"],
        "page_count": ocr_result["page_count"],
        "config": ocr_result["config"],
        # analysis 子字典。 用途：质量评估报告
        "analysis": {
            "default_low_confidence_threshold": DEFAULT_LOW_CONFIDENCE_THRESHOLD,
            "low_confidence_word_count": low_confidence_word_count,
            "rejected_word_count": sum(
                len(page.get("rejected_words", []))
                for page in ocr_result["pages"]
            ),
            "table_count": len(ocr_result.get("tables", [])),
            "form_field_count": ocr_result.get("form_analysis", {}).get("field_count", 0),
            "selected_option_count": ocr_result.get("form_analysis", {}).get("selected_option_count", 0),
            "receipt_line_item_count": ocr_result.get("receipt_invoice_analysis", {}).get("line_item_count", 0),
            "document_label": ocr_result.get("document_label", ""),
            "router_confidence": ocr_result.get("document_router_result", {}).get("analysis", {}).get("confidence", 0.0),
            "bundle_segment_count": ocr_result.get("bundle_splitter_result", {}).get("analysis", {}).get("segment_count", 0),
            "bundle_detected": ocr_result.get("bundle_splitter_result", {}).get("analysis", {}).get("detected_bundle", False),
            "review_page_count": ocr_result.get("signature_handwriting_review_result", {}).get("analysis", {}).get("review_page_count", 0),
            "signature_region_count": ocr_result.get("signature_handwriting_review_result", {}).get("analysis", {}).get("signature_region_count", 0),
            "handwriting_region_count": ocr_result.get("signature_handwriting_review_result", {}).get("analysis", {}).get("handwriting_region_count", 0),
            "suspicious_field_count": ocr_result.get("signature_handwriting_review_result", {}).get("analysis", {}).get("suspicious_field_count", 0),
            "query_candidate_count": ocr_result.get("query_extractor_result", {}).get("analysis", {}).get("candidate_count", 0),
            "analysis_page": analysis_url,
        },
        # downloads 子字典。 用途：提供完整文件的下载链接
        "downloads": {
            "ocr_json": job.output_url(job.ocr_json_path),
            "full_text": job.output_url(job.full_text_path),
            "document_markdown": job.output_url(job.document_markdown_path),
            "tables_index": job.output_url(job.tables_index_path),
            "form_json": job.output_url(job.form_json_path),
            "receipt_json": job.output_url(job.receipt_json_path),
            "router_json": job.output_url(job.router_json_path),
            "bundle_json": job.output_url(job.bundle_json_path),
            "review_json": job.output_url(job.review_json_path),
            "query_json": job.output_url(job.query_json_path),
            "analysis_page": analysis_url,
            "job_manifest": job.output_url(job.manifest_path),
        },
        # artifacts 子字典。 用途：提供目录级别的访问入口
        "artifacts": {
            "pages_dir": job.output_url(job.pages_dir),
            "overlays_dir": job.output_url(job.overlays_dir),
            "texts_dir": job.output_url(job.texts_dir),
            "markdown_dir": job.output_url(job.markdown_dir),
            "tables_dir": job.output_url(job.tables_dir),
            "document_markdown": job.output_url(job.document_markdown_path),
            "form_json": job.output_url(job.form_json_path),
            "receipt_json": job.output_url(job.receipt_json_path),
            "router_json": job.output_url(job.router_json_path),
            "bundle_json": job.output_url(job.bundle_json_path),
            "bundle_segments_dir": job.output_url(job.bundle_segments_dir),
            "review_json": job.output_url(job.review_json_path),
            "review_overlays_dir": job.output_url(job.review_overlays_dir),
            "query_json": job.output_url(job.query_json_path),
        },
        "tables": [
            {
                "table_id": table["table_id"],
                "page_num": table["page_num"],
                "source": table["source"],
                "row_count": table["row_count"],
                "col_count": table["col_count"],
                "csv_url": job.output_url(Path("tables") / table["csv_path"]),
                "html_url": job.output_url(Path("tables") / table["html_path"]),
            }
            for table in ocr_result.get("tables", [])
        ],
        "form": ocr_result.get("form_result", {}),
        "receipt_invoice": ocr_result.get("receipt_invoice_result", {}),
        "document_router": ocr_result.get("document_router_result", {}),
        "bundle_splitter": ocr_result.get("bundle_splitter_result", {}),
        "signature_handwriting_review": ocr_result.get("signature_handwriting_review_result", {}),
        "query_extractor": ocr_result.get("query_extractor_result", {}),
        # page_previews 数组。 用途：每一页的详细预览信息
        "page_previews": page_previews,
    }


@app.get("/")
def index() -> FileResponse:
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=500, detail="缺少 web/index.html 文件。")
    return FileResponse(index_file)


@app.post("/ocr")
def upload_document(
        file: UploadFile = File(...),
        ocr_lang: str = Form(os.getenv("OCR_LANG", "chi_sim+eng")),
        dpi: int = Form(int(os.getenv("OCR_DPI", "300"))),  # FastAPI 自动将表单参数转换为 int
        tesseract_config: str = Form(os.getenv("TESSERACT_CONFIG", DEFAULT_TESSERACT_CONFIG)),
        preprocess_mode: str = Form(os.getenv("OCR_PREPROCESS_MODE", DEFAULT_PREPROCESS_MODE)),
        ocr_padding: int = Form(int(os.getenv("OCR_PADDING", str(DEFAULT_OCR_PADDING)))),
        enable_sparse_fallback: bool = Form(_env_bool("OCR_SPARSE_FALLBACK", True)),
        enable_rotated_text: bool = Form(_env_bool("OCR_ROTATED_TEXT", DEFAULT_ENABLE_ROTATED_TEXT)),
        suppress_graphic_artifacts: bool = Form(_env_bool("OCR_SUPPRESS_GRAPHIC_ARTIFACTS", True)),
): # 第一层：接口设计（参数声明）
    """"
    upload_document 是整个 OCR Inspector 系统的核心入口，负责：
        接收用户上传的文件和参数
        执行完整的 OCR 处理流程
        返回结构化的结果
    这是一个典型的 "上传-处理-响应" 模式，但内部包含了大量精心设计的并发控制、缓存优化、错误处理和资源管理机制。
    """
    # 第二层：前置校验（快速失败）
    if not file.filename:
        raise HTTPException(status_code=400, detail="未检测到文件名。")

    try:
        source_kind = detect_upload_kind(file.filename, file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 第三层：任务初始化
    job = JobSkeleton.create(
        uploads_root=UPLOADS_DIR,
        outputs_root=OUTPUTS_DIR,
        source_filename=file.filename,
        source_kind=source_kind,
    )

    # 第四层：状态跟踪变量
    request_fingerprint = ""
    request_started = False
    global_slot_acquired = False

    # 第五层：核心处理流程（try 块）
    try:
        # 步骤 1：保存文件并计算哈希
        file_hash = _save_upload(file, job.source_path)
        # 步骤 2：构建请求指纹
        # 生成唯一标识符，用于缓存和去重
        # 相同的文件 + 相同的参数 = 相同的指纹
        request_fingerprint = _build_request_fingerprint(
            file_hash=file_hash,
            source_kind=source_kind,
            ocr_lang=ocr_lang,
            dpi=dpi,
            tesseract_config=tesseract_config,
            preprocess_mode=preprocess_mode,
            ocr_padding=ocr_padding,
            enable_sparse_fallback=enable_sparse_fallback,
            enable_rotated_text=enable_rotated_text,
            suppress_graphic_artifacts=suppress_graphic_artifacts,
        )

        # 步骤 3：检查缓存
        cached_response = _get_cached_response(request_fingerprint)
        if cached_response is not None:
            _cleanup_job(job)  # ← 清理刚创建的目录
            cached_response["cached"] = True
            cached_response["message"] = "复用已有 OCR 结果。"
            return cached_response

        # 步骤 4：获取全局 OCR 槽位（
        if not _try_acquire_global_ocr_slot(job.job_id):
            _cleanup_job(job)  # ← 清理后拒绝请求
            raise HTTPException(status_code=409, detail="当前已有 OCR 任务在处理中，请等待当前任务结束后再提交新的 OCR。")
        global_slot_acquired = True

        # 步骤 5：请求去重
        if not _try_start_request(request_fingerprint):
            _release_global_ocr_slot(job.job_id)
            global_slot_acquired = False
            _cleanup_job(job)
            raise HTTPException(status_code=409, detail="相同文件和参数的 OCR 正在处理中，请稍候。")
        request_started = True

        # 步骤 6：执行 OCR 流水线
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
        ocr_result = result["ocr_result"]

        # 步骤 7：生成错误分析页面
        analysis_url = job.output_url(job.analysis_index_path)
        build_error_analysis_page(
            output_path=job.analysis_index_path,
            job_id=job.job_id,
            source_file=ocr_result["source_file"],
            default_threshold=DEFAULT_LOW_CONFIDENCE_THRESHOLD,
        )

        # 步骤 8：写入任务清单
        _write_job_manifest(
            job,
            ocr_result,
            analysis_url,
            request_fingerprint,
        )

        # 步骤 9：构建响应负载
        response_payload = _build_response_payload(
            job=job,
            ocr_result=ocr_result,
            analysis_url=analysis_url,
            cached=False,
        )

        # 步骤 10：完成请求并缓存
        _finish_request(request_fingerprint, response_payload)
        request_started = False
        _release_global_ocr_slot(job.job_id)
        global_slot_acquired = False
        return response_payload

    # 第六层：异常处理
    except HTTPException:
        if request_started:
            _finish_request(request_fingerprint)
        if global_slot_acquired:
            _release_global_ocr_slot(job.job_id)
        raise
    except Exception as exc:  # noqa: BLE001
        if request_started:
            _finish_request(request_fingerprint)
        if global_slot_acquired:
            _release_global_ocr_slot(job.job_id)
        raise HTTPException(status_code=500, detail=f"OCR 处理失败：{exc}") from exc
    # 第七层：最终清理（finally 块）
    finally:
        file.file.close()


@app.post("/query")
def query_document(
        job_id: str = Form(...),
        query: str = Form(...),
) -> dict[str, Any]:
    """对同一份已处理文档做自然语言字段提问。"""
    normalized_job_id = (job_id or "").strip()
    normalized_query = (query or "").strip()

    if not normalized_job_id:
        raise HTTPException(status_code=400, detail="缺少 job_id。")
    if not normalized_query:
        raise HTTPException(status_code=400, detail="缺少 query。")

    output_dir = OUTPUTS_DIR / normalized_job_id
    query_json_path = output_dir / "query_extractor.json"
    if not query_json_path.exists():
        raise HTTPException(status_code=404, detail="未找到对应任务的 query_extractor.json。")

    query_result = load_query_json(query_json_path)
    answer_payload = answer_document_query(query_result, normalized_query)
    write_query_json(query_result, query_json_path)

    return {
        "job_id": normalized_job_id,
        "query": normalized_query,
        "result": answer_payload,
        "query_history_count": len(query_result.get("query_history", [])),
        "query_json": f"/outputs/{normalized_job_id}/query_extractor.json",
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
