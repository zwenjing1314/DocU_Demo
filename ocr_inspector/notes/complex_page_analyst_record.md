# Complex Page Analyst 开发记录

## 背景

这次新增第 16 个能力：

- Complex Page Analyst

新增主题：

- 复杂元素解析

继承前面这些能力：

- 2. `Layout Reader`
- 3. `Table to CSV`
- 14. `Direct PDF Structurer`
- 15. `Evidence-grounded Multi-page QA`

题目要求三选一做深：

- 图表问答
- 公式 / 技术文档解析
- 幻灯片 / 海报 / 信息图理解

本次选择：

- 图表问答

原因是它最适合当前 demo 的已有基础：Layout Reader 提供上下文，Table to CSV 提供结构化数据，Evidence QA 提供“带证据回答”的思路。

## 为什么新增独立文件

`ocr_engine.py` 已经很大，所以继续新增独立模块：

- `ocr_engine_16_complex_page_analyst.py`

这个模块只负责：

1. 识别 chart-like table
2. 构造图表候选
3. 支持 max / min / total / trend 类问答
4. 返回证据页和证据表
5. 返回错误解释

主流程只负责调用它并写出 `complex_page_analysis.json`。

## 新增输出

新增正式产物：

- `complex_page_analysis.json`

顶层结构包括：

- `schema_version`
- `source_file`
- `source_kind`
- `selected_domain`
- `status`
- `chart_candidates`
- `query_history`
- `analysis`
- `demo_scope`

## 图表候选怎么来

当前不是直接看图像像素，而是先做一个稳定可演示的 chart QA demo。

候选来源：

- 第 3 步导出的表格 rows
- 第 13 步 layout chunk 的标题上下文

一个表格要成为 chart candidate，通常需要：

- 至少 3 行
- 至少一个维度列
- 至少一个数值列
- 表格内容或标题上下文里出现 chart / figure / trend / revenue / growth / 图表 / 趋势 / 增长 等线索

## 当前能回答的问题

当前支持这些问题类型：

- 最大值
- 最小值
- 合计
- 趋势
- 简要摘要

例如：

- Which region has the highest revenue?
- total revenue
- growth trend
- 哪个地区收入最高

## 为什么强调错误解释

复杂页面最容易出现的问题是：模型看起来回答了，但不知道依据是什么，也不知道哪里可能错。

所以每次图表问答都会返回：

- `evidence_pages`
- `evidence_items`
- `error_explanations`

典型错误解释包括：

- 当前基于抽取表格值，不直接测量图像柱形高度
- 如果 OCR / 表格抽取错列，答案可能错
- 本地 demo 不理解颜色图例和视觉注释

## 新增接口

新增接口：

- `POST /complex-chart-qa`

输入：

- `job_id`
- `query`

输出：

- `answer`
- `evidence_pages`
- `evidence_items`
- `error_explanations`
- `confidence`

如果没有图表候选，会返回：

- `status: insufficient_evidence`

## 前端变化

在 Query 面板新增按钮：

- `图表问答`

结果页新增：

- 图表候选数量
- `complex_page_analysis.json` 下载入口

图表问答结果会展示：

- 答案
- 证据页
- 证据表
- 错误解释
- 完整 JSON

## 修改文件

### 1. `ocr_engine_16_complex_page_analyst.py`

新增核心模块。

### 2. `ocr_engine.py`

主流程新增：

- `complex_page_analyst` 配置项
- `complex_page_analysis_result`
- `complex_page_analysis.json`

### 3. `job_skeleton.py`

任务骨架新增：

- `complex_page_analysis_json_path`

### 4. `app.py`

接口响应和 manifest 新增：

- `complex_page_analysis_json`
- `chart_candidate_count`
- `chart_qa_ready`
- `complex_page_analysis`

新增接口：

- `/complex-chart-qa`

### 5. `web/index.html`

新增：

- `图表问答` 按钮
- 图表候选数量统计
- `complex_page_analysis.json` 下载入口

### 6. `tests/test_complex_page_analyst.py`

新增测试覆盖：

1. 从表格和标题上下文识别图表候选
2. 图表问题返回答案、证据页和错误解释
3. 无候选时返回 `insufficient_evidence`
4. API 调用后写回 query history

## 当前边界

当前能做：

- 基于表格数据回答图表类问题
- 返回证据页和证据表
- 解释为什么可能错

暂时没有做：

- 直接识别柱状图 / 折线图像素
- 解析颜色图例
- 解析公式
- 解析海报 / 信息图的视觉布局语义

后续如果继续做深，可以把图像模型接进来，让它读取图表区域截图，但输出仍然应该保持当前这种可追溯结构。
