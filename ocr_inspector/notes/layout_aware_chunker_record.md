# Layout-aware Chunker 开发记录

## 背景

这次新增第 13 个能力：

- Layout-aware Chunker

新增主题包括：

- 基于布局的 chunking
- 带标题上下文的切块

继承前面这些能力：

- 2. `Layout Reader`
- 3. `Table to CSV`
- 11. `Multi-page Consolidator`

最小交付目标：

- 把一份长文档切成适合 RAG 的 chunk
- 保留标题链
- 保留表头上下文
- 保留页码

过关标准：

- 同一段内容不会脱离标题语境
- 表格块不会被切得支离破碎

## 为什么新增独立文件

`ocr_engine.py` 已经很大，所以这次继续新增独立模块：

- `ocr_engine_13_chunker.py`

这个模块只消费已有结果：

1. 第 2 步生成的 `page.layout.items`
2. 第 3 步生成的 `tables`
3. 第 11 步生成的 `multi_page_consolidation_result`

它不重新做 OCR，也不重新做表格检测。

## 新增输出

新增正式产物：

- `layout_chunks.json`

每个 chunk 包含：

- `chunk_id`
- `type`
- `text`
- `title_chain`
- `title_context`
- `page_range`
- `page_nums`
- `source_refs`
- `char_count`

表格 chunk 额外包含：

- `table_id`
- `table_header_context`
- `row_count`
- `col_count`

## 当前 chunk 类型

### 1. `text`

来自 Layout Reader 的 heading / paragraph / list item。

正文 chunk 会继承最近的标题链，例如：

- `Annual Report > Revenue Overview`

实际写入 `text` 时，会把标题上下文放到正文前面，方便直接进入向量库。

### 2. `table`

来自 Table to CSV 的完整表格结果。

表格不会被按行拆成多个 chunk，而是作为一个整体保留：

- 表头
- 所有行
- `csv_path`
- `html_path`
- 页码

这样做是为了避免 RAG 召回时表头和数据行分离。

### 3. `consolidation_summary`

来自 Multi-page Consolidator。

用于保留跨页合并摘要，例如：

- consolidated item count
- duplicate count
- total check status
- balance check status

## 修改文件

### 1. `ocr_engine_13_chunker.py`

新增核心 chunker 模块，负责：

- 按布局 item 生成正文 chunk
- 维护标题链
- 原子化生成表格 chunk
- 生成跨页合并摘要 chunk
- 写出 `layout_chunks.json`

### 2. `ocr_engine.py`

主流程新增：

- `layout_aware_chunker` 配置项
- `layout_chunk_result`
- `layout_chunks.json`

### 3. `job_skeleton.py`

任务骨架新增：

- `layout_chunks_json_path`

### 4. `app.py`

接口响应和 manifest 新增：

- `layout_chunks_json`
- `layout_chunk_count`
- `layout_table_chunk_count`
- `heading_context_chunk_count`
- `layout_chunks`

### 5. `web/index.html`

结果页新增：

- RAG chunks 数量
- 表格 chunks 数量
- `layout_chunks.json` 下载入口

### 6. `tests/test_layout_aware_chunker.py`

新增测试覆盖：

1. 标题链上下文能进入正文 chunk
2. 表格作为完整 chunk 保留
3. 完整 OCR pipeline 会写出 `layout_chunks.json`

## 当前策略

当前实现采用启发式、可解释策略：

1. 遇到 heading 时更新标题链
2. paragraph / list item 进入正文 buffer
3. buffer 超过 `max_chars` 时切出 text chunk
4. 表格单独变成 table chunk
5. 跨页合并结果单独变成 consolidation summary chunk

默认参数：

- `max_chars = 900`
- `overlap_chars = 120`

## 当前边界

当前更适合：

- 有清晰标题层级的报告
- 多段落 PDF
- 包含表格的长文档
- 需要进入 RAG / 向量库的 OCR 结果

暂时不擅长：

- 标题识别本身错误的文档
- 表格跨页断裂但没有重复表头的复杂场景
- 需要语义模型判断段落边界的论文级 chunking
- 图文混排非常复杂的页面

后续如果继续做深，可以接入 query log、人工复核记录和字段 schema，把 chunk 进一步分成 retrieval chunk 和 answer grounding chunk 两类。
