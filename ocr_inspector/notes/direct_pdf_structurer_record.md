# Direct PDF Structurer 开发记录

## 背景

这次新增第 14 个能力：

- Direct PDF Structurer

新增主题包括：

- 直接 PDF 理解
- 严格 schema 输出

继承前面这些能力：

- 10. `Custom Schema Extractor`
- 13. `Layout-aware Chunker`

最小交付目标：

- 不先手工 parse 全文
- 直接输入 PDF
- 输出摘要、目录树或固定 JSON

过关标准：

- 输出可被程序消费
- 不是“写得像人话”的自由文本

## 当前实现说明

当前 demo 没有调用外部多模态模型 API。

这次实现的是一个本地、可测试、可替换的直接 PDF 结构化适配层：

1. 直接读取 PDF 文件本身
2. 从 PDF 原生结构中取目录、元数据、每页文本片段
3. 复用第 10 步合同 schema
4. 复用第 13 步 RAG chunks 上下文
5. 输出严格 JSON

如果后续接入真正多模态模型，可以复用这次输出里的 `model_contract.json_schema`。

## 为什么新增独立文件

`ocr_engine.py` 已经很大，所以继续新增独立模块：

- `ocr_engine_14_direct_pdf_structurer.py`

这个模块负责：

- 直接读取 PDF 原生信息
- 构造 strict schema
- 构造目录树
- 构造固定 JSON
- 构造多模态模型输出契约
- 写出 `direct_pdf_structure.json`

主流程只负责调用它和保存文件。

## 新增输出

新增正式产物：

- `direct_pdf_structure.json`

顶层结构包括：

- `schema_version`
- `source_file`
- `source_kind`
- `status`
- `mode`
- `metadata`
- `strict_schema`
- `pages`
- `model_contract`
- `validation`
- `analysis`

## strict_schema 内容

`strict_schema` 固定包含：

- `summary`
- `outline_tree`
- `fixed_json`
- `rag_context`

### 1. `summary`

包含：

- `short`
- `page_count`
- `detected_topics`

### 2. `outline_tree`

优先使用 PDF 自带 TOC。

如果 PDF 没有 TOC，则从每页原生文本里找 heading candidates，构造一个可消费的浅层目录。

### 3. `fixed_json`

当前主要复用第 10 步合同 schema：

- `document_type`
- `contract`

如果合同字段已抽出，会进入这里。

### 4. `rag_context`

复用第 13 步 chunker：

- `chunk_count`
- `table_chunk_count`
- `referenced_chunk_ids`

这样 direct PDF structurer 可以和 RAG chunks 串起来。

## model_contract

这次新增了 `model_contract` 字段。

它描述未来接多模态模型时应该怎么调用：

- 输入：PDF file bytes or PDF URL
- 输出：strict JSON
- schema：`summary / outline_tree / fixed_json / rag_context`
- 要求：不要返回 JSON 外的自由文本

这让后续从“本地 PDF 原生结构”切到“真正多模态 PDF 理解”时，前后端和下游消费方式不用大改。

## 状态说明

当前状态可能是：

- `ok`
- `needs_model_or_ocr`
- `skipped`

含义：

- `ok`：PDF 有原生文本，可以本地直接结构化
- `needs_model_or_ocr`：PDF 没有原生文本，更适合交给多模态模型或 OCR
- `skipped`：输入不是 PDF

## 修改文件

### 1. `ocr_engine_14_direct_pdf_structurer.py`

新增核心模块。

### 2. `ocr_engine.py`

主流程新增：

- `direct_pdf_structurer` 配置项
- `direct_pdf_structure_result`
- `direct_pdf_structure.json`

### 3. `job_skeleton.py`

任务骨架新增：

- `direct_pdf_structure_json_path`

### 4. `app.py`

接口响应和 manifest 新增：

- `direct_pdf_structure_json`
- `direct_pdf_schema_valid`
- `direct_pdf_outline_count`
- `direct_pdf_native_text_pages`
- `direct_pdf_structure`

### 5. `web/index.html`

结果页新增：

- PDF schema 状态
- PDF 目录项数量
- `direct_pdf_structure.json` 下载入口

### 6. `tests/test_direct_pdf_structurer.py`

新增测试覆盖：

1. 直接从 PDF 原生 TOC / 文本构造 strict schema
2. 完整 OCR pipeline 写出 `direct_pdf_structure.json`

## 当前边界

当前更适合：

- 有原生文本的 PDF
- 带 TOC / 标题的 PDF
- 合同类 PDF
- 需要严格 JSON 输出的下游程序

暂时没有做：

- 调用外部 Gemini / GPT / 其他多模态模型
- 对扫描 PDF 做视觉级理解
- 复杂图片、图表、手写内容的直接理解

后续如果要真正接多模态模型，可以在这个模块里新增一个 provider 层，让模型直接按 `model_contract.json_schema` 返回 JSON，再复用现有校验和输出路径。
