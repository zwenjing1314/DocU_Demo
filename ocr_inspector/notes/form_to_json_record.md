# Form to JSON 开发记录

## 背景

本次是在已有两个能力上继续扩展：

1. `OCR Inspector`
- 已具备 PDF 转页、OCR、word / line bbox、置信度、叠框图、纯文本导出。

2. `Layout Reader`
- 已具备标题、段落、列表、页眉页脚、阅读顺序、Markdown 导出。

本次新增主题是：

- 键值对
- checkbox
- 字段标准化

最小交付目标是：

- 把申请表 / 登记表抽成固定 JSON

过关标准是：

- 姓名、日期、地址、勾选项这些基础字段能稳定输出

## 这次为什么单独拆文件

用户已经明确指出 `ocr_engine.py` 太大。

这次没有继续把表单抽取逻辑塞回主引擎，而是新增了单独模块：

- `ocr_engine_4_json.py`

这样做有两个目的：

1. 让 `ocr_engine.py` 继续只负责主 OCR 流水线和导出总装配
2. 让表单 JSON 抽取逻辑可以单独维护、单独测试

## 这次做了什么

### 1. 新增表单抽取模块

新增文件：

- `ocr_engine_4_json.py`

这个模块负责三件事：

1. 从 OCR 行文本中抽取键值对
- 优先识别同一行的 `标签: 值`
- 例如：
  - `Applicant Name: Alice Chen`
  - `Date: 2026/04/28`
  - `地址：杭州市西湖区...`

2. 处理表单常见的“标签和值分开”的布局
- 如果某一行只有标签，比如 `Name`
- 会继续在同一页里找：
  - 右侧同行的值
  - 下方相邻区域的值

3. 做字段标准化
- 把抽到的内容统一映射进固定 JSON
- 当前固定字段包括：
  - `name`
  - `date`
  - `address`
  - `phone`
  - `email`
  - `id_number`
  - `gender`
  - `selected_options`

### 2. 新增 checkbox 解析

当前支持从 OCR 行文本里识别常见勾选标记，例如：

- `☑`
- `☒`
- `☐`
- `□`
- `[x]`
- `[ ]`
- `(x)`
- `( )`

输出结果会保留：

- 原始 label
- 标准化 label
- 是否勾选

并且会把已勾选项汇总进：

- `normalized_form.selected_options`

### 3. 新增 JSON 导出

每次 OCR 任务现在会额外落盘：

- `outputs/<job_id>/form.json`

文件内容是完整表单抽取结果，而不是只保留几个字段值。

这样后续如果你继续做：

- 表单字段校验
- 字段映射
- 业务系统入库

都会更方便。

## 修改文件

### 1. `ocr_engine_4_json.py`

新增表单抽取主模块：

- 键值对识别
- checkbox 识别
- 字段标准化
- `form.json` 写入

### 2. `ocr_engine.py`

主流水线新增对 `Form to JSON` 的接入：

- 在 `layout` 和 `table` 信息准备完成后调用 `ocr_engine_4_json.py`
- 把结果挂进 `ocr_result`
- 输出 `form_analysis`
- 写入 `form.json`

同时保留了主流水线入口不变，仍然是：

- `run_ocr_pipeline()`

### 3. `job_skeleton.py`

任务骨架新增：

- `form_json_path`

使 `form.json` 成为正式产物，而不是临时文件。

### 4. `app.py`

接口和任务清单新增：

- `form_json` 下载链接
- `form` 摘要结果
- `form_field_count`
- `selected_option_count`

### 5. `web/index.html`

前端结果页新增：

- `form.json` 下载入口
- 表单字段统计
- 结果总览里展示 `normalized_form` 摘要

### 6. `tests/test_form_to_json.py`

新增测试覆盖三类场景：

1. 同一行键值对抽取
2. 左标签右值 / 下值 的表单布局抽取
3. 完整流水线写出 `form.json`

## 这次顺手处理的问题

在开始做 `Form to JSON` 之前，仓库里其实残留了一次未完成的 merge：

- `app.py`
- `job_skeleton.py`
- `ocr_engine.py`
- `web/index.html`

里面带有 `<<<<<<< HEAD` 这类冲突标记，项目当时是不能正常跑测试的。

这次在开发新功能前，先把这些冲突清理回可运行状态，避免在坏基线之上继续叠代码。

## 当前输出结构

现在一个任务的核心产物会包括：

- `ocr.json`
- `full_text.txt`
- `document.md`
- `form.json`
- `tables/index.html`
- `pages/`
- `overlays/`
- `texts/`
- `markdown/`
- `tables/`

## 当前边界

这次实现的是“最小可交付版本”，所以策略是启发式的，不是训练好的表单理解模型。

当前更擅长处理的是：

- 申请表
- 登记表
- 联系信息表
- 简单勾选表

对下面这些复杂情况，后续还可以继续增强：

- 大量跨列的复杂表单
- 多层嵌套 checkbox / radio 组
- 非常规标签命名
- OCR 识别很乱的扫描件

## 总结

这次改造之后，项目从：

- OCR 检查台
- Layout Reader
- Table to CSV

继续推进到了：

- Form to JSON

也就是说，现在它不仅能抽文本、抽结构、抽表格，还能把常见申请表 / 登记表里的基础字段直接转成固定 JSON，已经更接近一个可继续扩展的文档理解 demo 了。
