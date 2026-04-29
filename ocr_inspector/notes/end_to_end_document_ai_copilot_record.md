# 18. End-to-End Document AI Copilot 修改记录

## 新增主题

- 完整流水线整合：分类、切分、OCR/layout、业务抽取、长文档 chunking、问答/摘要、人工复核、导出。
- 将 1-17 的产物组织成产品级 workflow，而不是继续堆单点脚本。
- 输出统一 Copilot JSON 和 Markdown，方便演示、提交和下游系统消费。

## 继承关系

- 继承全部 1-17 能力。
- 不重复实现 OCR、路由、抽取、问答、复核、鲁棒性诊断算法，只消费前面已经生成的结构化结果。

## 代码变更

- 新增 `ocr_engine_18_copilot.py`：
  - 汇总 `document_router_result`、`bundle_splitter_result`、OCR/layout/table/extraction 结果；
  - 汇总 `layout_chunk_result`、`direct_pdf_structure_result`、`evidence_qa_result`、`complex_page_analysis_result`；
  - 汇总 `signature_handwriting_review_result` 和 `robustness_lab_result`；
  - 生成 `pipeline.stages`、`readiness`、`demo_script`、`exports` 和 `key_facts`。
- 更新 `ocr_engine.py`：
  - 在 Robustness Lab 后接入 End-to-End Copilot；
  - 写出 `document_ai_copilot.json` 和 `document_ai_copilot.md`；
  - 将 Copilot 结果写回 `ocr_result`。
- 更新 `job_skeleton.py`：
  - 增加 `document_ai_copilot_json_path` 和 `document_ai_copilot_markdown_path`。
- 更新 `app.py`：
  - 在 manifest、downloads、artifacts、analysis 和响应体中暴露 Copilot 结果。
- 更新 `web/index.html`：
  - 增加 Copilot 状态、阶段数和导出下载入口。
- 新增 `tests/test_document_ai_copilot.py`：
  - 覆盖 Copilot 汇总结构、Markdown 导出、主流水线文件写出。

## 交付结果

- 上传文档后会自动产出：
  - `document_ai_copilot.json`
  - `document_ai_copilot.md`
- Copilot JSON 中包含：
  - `document_package`：文档类型、页数、是否混合包、切分数量；
  - `pipeline.stages`：每个阶段的输入、输出、指标、handoff；
  - `qa.suggested_questions`：可演示的问题；
  - `human_review`：人工复核入口和修订文件；
  - `exports`：统一 JSON/Markdown 导出清单；
  - `readiness`：整条链路是否能演示。

## 过关标准对应

- 分类：来自 `document_router.json`。
- 切分：来自 `bundle_splitter.json` 和 `bundle_segments/`。
- OCR/layout/extraction：来自 `ocr.json`、`document.md`、`tables/`、业务 JSON。
- 长文档 chunking：来自 `layout_chunks.json`。
- 问答/摘要：来自 `direct_pdf_structure.json`、`evidence_qa.json`、`complex_page_analysis.json`。
- 人工复核：来自 Review Workbench 和 `review_workbench_revisions.json`。
- 导出：最终统一到 `document_ai_copilot.json` 和 `document_ai_copilot.md`。
