# Table to CSV 开发记录

## 背景

这次是在现有两个能力基础上继续扩展：

1. `OCR Inspector`
- 已经具备 PDF 转页、OCR、word / line bbox、置信度、叠框图、纯文本导出。

2. `Layout Reader`
- 已经具备标题、段落、列表、页眉页脚、阅读顺序、Markdown 导出。

本次新增主题是：

- 表格检测
- 表格结构恢复
- 单元格导出

最小交付目标是：

- 从 PDF 中抽表格并导出 `CSV / HTML`

过关标准是：

- 简单表格不错行
- 复杂表格至少保住大部分行列关系

## 本次实现思路

这次没有单走一种检测方式，而是做了一个两段式方案：

1. PDF 原生抽表优先
- 对 `source_kind=pdf` 的任务，优先使用 `PyMuPDF page.find_tables()`。
- 这个分支对数字版 PDF、带表格线的文档更稳，能直接拿到行列结构。

2. OCR 词框恢复兜底
- 如果某一页没有抽到 PDF 原生表格，就回退到 OCR 结果。
- 回退逻辑会基于现有 `word bbox`：
  - 先按垂直位置把词聚成候选行
  - 再按横向大间隔把一行拆成多个单元格段
  - 然后按 x 方向聚类恢复列
  - 最终拼成二维表格矩阵

这样做的目的是：

- 让数字版 PDF 直接吃高质量表格结构
- 让扫描件、图片页、或原生抽表失败的页面仍然有兜底结果

## 修改文件

### 1. `ocr_engine.py`

新增了表格相关的核心能力：

- 新增 PDF 原生抽表 helper：
  - `_extract_tables_from_pdf_page()`
- 新增 OCR 兜底抽表 helper：
  - `_group_words_to_candidate_table_rows()`
  - `_cluster_table_columns()`
  - `_detect_tables_from_ocr_page()`
- 新增导出 helper：
  - `_write_table_csv()`
  - `_write_table_html()`
  - `_write_tables_index()`
- 在 `run_ocr_pipeline()` 中接入整条表格导出链路
- 在按页 Markdown 中补充表格导出链接

同时补了一定量的代码注释，重点解释了：

- 为什么优先用 PDF 原生抽表
- 为什么要对 OCR 词框做列聚类
- 表格恢复为什么不直接按最大列数硬切

### 2. `job_skeleton.py`

把表格导出产物纳入正式任务骨架：

- 新增 `tables_dir`
- 新增 `tables_index_path`

现在每个任务会正式生成：

- `outputs/<job_id>/tables/`

### 3. `app.py`

把表格结果接入接口返回和任务清单：

- `job_manifest.json` 新增：
  - `tables_index`
  - `tables_dir`
- 接口响应新增：
  - 总表格数量
  - 全局表格列表
  - 每页表格摘要
  - `tables/index.html` 下载入口

### 4. `web/index.html`

前端结果页新增表格展示：

- 总览区新增表格数量
- 下载区新增 `tables/index.html`
- 每页卡片新增：
  - 当前页抽到的表格数量
  - 每张表的 `CSV / HTML` 链接

### 5. `tests/test_table_exports.py`

新增表格能力回归测试，覆盖三类场景：

- PDF 原生抽表能识别简单网格表
- OCR 词框兜底能恢复简单三列表
- 完整流水线会实际写出 `CSV / HTML / tables/index.html`

## 新增产物

每次任务新增以下导出结果：

- `outputs/<job_id>/tables/index.html`
- `outputs/<job_id>/tables/page_XXX_table_YY.csv`
- `outputs/<job_id>/tables/page_XXX_table_YY.html`

并且 `ocr.json` 中新增：

- `config.table_to_csv`
- `table_analysis`
- `tables`

`pages[].tables` 中也会保留每页的表格摘要信息。

## 当前效果与边界

### 当前效果

- 简单规则表、发票明细表、教材里比较规整的行列表，优先走 PDF 原生抽表时效果会比较稳。
- 对扫描件或原生抽表失败的页面，OCR 词框兜底至少能把常见的多行多列表恢复成可读的二维矩阵。
- CSV 和 HTML 都是正式落盘产物，不只是前端临时展示。

### 当前边界

- OCR 兜底分支本质上是启发式恢复，不是完整的表格结构学习模型。
- 对跨行跨列很多、单元格内大段换行、无明显列对齐的复杂表格，目前会优先保住大部分行列关系，但不会完美恢复合并单元格。
- HTML 当前导出的是规则矩阵视图，重点是可检查和可复用，不是高保真排版复刻。

## 验证方式

建议你本地重点看这几类结果：

1. `tables/index.html`
- 看总共抽到了几张表，每张表的预览是否符合预期。

2. 单张表的 `CSV`
- 用 Excel 或文本编辑器打开，检查行列是否错位。

3. 单张表的 `HTML`
- 检查浏览器里表格是否便于人工核验。

4. `ocr.json`
- 检查 `table_analysis`、`tables`、`pages[].tables` 是否齐全。

## 总结

这次改造把项目从：

- OCR 文本检查台

进一步推进到了：

- OCR + 版面恢复 + 表格导出检查台

也就是说，项目现在除了看词框、看结构化 Markdown 之外，还能把文档里的表格直接导成可复用的 `CSV / HTML`，更接近一个真正可继续扩展的文档理解 demo。
