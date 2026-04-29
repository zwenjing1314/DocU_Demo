# 17. Robustness Lab 修改记录

## 新增主题

- 扫描低对比、歪斜、阴影、弯曲、屏摄、模糊退化版本生成。
- 对 OCR、layout、结构化抽取、证据问答四层做 baseline 与退化风险比较。
- 输出 `degradation_report.json`，明确每种退化最可能崩掉的层。

## 继承关系

- 继承 1-16 的 OCR、layout、表格、表单、票据、路由、切分、复核、问答、schema、跨页聚合、chunk、direct PDF、evidence QA、complex page analyst 结果。
- Robustness Lab 不重复实现前面能力，而是消费已有 `ocr_result` 中的指标做鲁棒性诊断。

## 代码变更

- 新增 `ocr_engine_17_robustness_lab.py`，负责生成退化页图、汇总 baseline 指标、计算风险分数并写出 degradation report。
- 更新 `ocr_engine.py`，在主流水线末尾接入 Robustness Lab，生成 `robustness_lab/` 目录和 `degradation_report.json`。
- 更新 `job_skeleton.py`，为任务骨架增加 `robustness_lab_dir` 和 `degradation_report_json_path`。
- 更新 `app.py`，在 manifest、downloads、artifacts、analysis 和 API 响应中暴露 Robustness Lab 结果。
- 更新 `web/index.html`，前端增加退化版本数、最脆弱层统计和 `degradation_report.json` 下载入口。
- 新增 `tests/test_robustness_lab.py`，覆盖退化图生成、可选 OCR probe 接口、主流水线报告写出。

## 交付结果

- 每个任务输出 `robustness_lab/<variant>/page_XXX.png`，便于直接查看退化样本。
- 每个任务输出 `degradation_report.json`，包含：
  - 原始 OCR / layout / extraction / reasoning baseline；
  - 各退化版本的视觉指标；
  - 各层风险分数；
  - `likely_failure_layer` 和总体 `most_fragile_layer`；
  - 当前 demo 的能力边界说明。

## 说明

- 默认采用轻量 `visual_proxy` 评估，避免对每个退化版本重新跑完整 OCR 流水线导致上传耗时显著增加。
- 模块保留 `degraded_page_evaluator` 扩展点，后续可以接入真实 OCR probe 或标注集，升级为更严格的 benchmark 模式。
