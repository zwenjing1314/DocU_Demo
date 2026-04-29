# Evidence-grounded Multi-page QA 开发记录

## 背景

这次新增第 15 个能力：

- Evidence-grounded Multi-page QA

新增主题包括：

- 多页问答
- 证据页
- 答案可追溯

继承前面这些能力：

- 13. `Layout-aware Chunker`
- 14. `Direct PDF Structurer`

最小交付目标：

- 对一份 20-100 页文档提问
- 返回答案
- 返回证据页码

过关标准：

- 答错不可怕
- 可怕的是答了却找不到证据
- 这个 demo 要解决“答案必须能追溯到证据”的问题

## 为什么新增独立文件

`ocr_engine.py` 已经很大，所以继续新增独立模块：

- `ocr_engine_15_evidence_qa.py`

这个模块只负责：

1. 从第 13 步 chunks 构建 evidence units
2. 从第 14 步 strict PDF structure 补充 evidence units
3. 根据问题检索证据
4. 基于证据返回答案
5. 保存 query history

主流程只负责调用它生成 `evidence_qa.json`。

## 新增输出

新增正式产物：

- `evidence_qa.json`

顶层结构包括：

- `schema_version`
- `source_file`
- `source_kind`
- `status`
- `index`
- `evidence_units`
- `query_history`
- `analysis`

## evidence unit 来源

### 1. Layout-aware chunks

来自第 13 步：

- `layout_chunk_result.chunks`

每个 chunk 会转换成一个 evidence unit。

保留信息包括：

- `unit_id`
- `unit_type`
- `text`
- `page_nums`
- `title_context`
- `source_ref`

### 2. Direct PDF strict schema

来自第 14 步：

- `strict_schema.summary`
- `strict_schema.outline_tree`
- `strict_schema.fixed_json`

这些内容作为辅助证据，尤其适合目录树、摘要和固定字段。

## 新增接口

新增接口：

- `POST /evidence-qa`

输入：

- `job_id`
- `query`

输出：

- `answer`
- `evidence_pages`
- `evidence_chunks`
- `confidence`
- `status`

如果找不到足够证据，会返回：

- `status: insufficient_evidence`
- `answer: ""`
- `evidence_pages: []`
- `evidence_chunks: []`

## 前端变化

在原来的 Query Extractor 面板里新增按钮：

- `证据问答`

它会调用 `/evidence-qa`，并展示：

- 答案
- 证据页
- 证据片段
- 完整 JSON

同时结果总览新增：

- 证据单元数量
- `evidence_qa.json` 下载入口

## 修改文件

### 1. `ocr_engine_15_evidence_qa.py`

新增核心模块，负责：

- 构建 evidence QA 索引
- 证据检索
- 答案抽取
- 证据页返回
- query history 保存

### 2. `ocr_engine.py`

主流程新增：

- `evidence_grounded_multi_page_qa` 配置项
- `evidence_qa_result`
- `evidence_qa.json`

### 3. `job_skeleton.py`

任务骨架新增：

- `evidence_qa_json_path`

### 4. `app.py`

接口响应和 manifest 新增：

- `evidence_qa_json`
- `evidence_unit_count`
- `evidence_query_count`
- `evidence_qa`

新增问答接口：

- `/evidence-qa`

### 5. `web/index.html`

新增：

- `证据问答` 按钮
- evidence pages 展示
- evidence chunks 展示
- `evidence_qa.json` 下载入口

### 6. `tests/test_evidence_qa.py`

新增测试覆盖：

1. 有证据时返回答案、证据页和证据 chunk
2. 无证据时返回 `insufficient_evidence`
3. API 调用后会写回 query history

## 当前策略

当前是本地启发式检索，不调用外部 LLM。

策略包括：

1. 从 query 中提取关键词和意图
2. 对 evidence units 做 token overlap 和意图加权
3. 选 top evidence chunks
4. 从最佳证据里抽取金额、日期或片段
5. 返回 evidence pages 和 source refs

## 当前边界

当前更适合：

- 问金额
- 问日期
- 问章节内容
- 问能在 chunk 里直接找到证据的问题

暂时不擅长：

- 需要复杂推理的问题
- 需要跨多个表格计算的问题
- 需要外部知识的问题
- 证据存在但 OCR / chunking 已经严重错误的情况

后续如果接入 LLM，也应该保持这个模块的底线：

- 模型可以组织答案
- 但 evidence pages / evidence chunks 必须来自本地检索结果
- 如果没有证据，就不能输出看似确定的答案
