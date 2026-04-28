# Bundle Splitter 开发记录

## 背景

本次是在现有六个能力基础上继续扩展：

1. `OCR Inspector`
2. `Layout Reader`
3. `Table to CSV`
4. `Form to JSON`
5. `Receipt & Invoice Extractor`
6. `Mixed Document Router`

这次新增主题是：

- 多文档 PDF 切分
- 页范围识别

最小交付目标是：

- 对一个扫描包或拼接 PDF 自动切成多个子文档

过关标准是：

- 能给出每个子文档的起止页
- 并把结果保存为独立文件或 JSON 段

## 为什么继续拆新文件

用户已经多次强调 `ocr_engine.py` 太大。

这次没有继续把 bundle 切分规则塞回主引擎，而是新增独立模块：

- `ocr_engine_7_bundle_splitter.py`

这样做的原因很明确：

1. `ocr_engine.py` 继续只做主流水线装配
2. 页级分类、边界判断、子 PDF 导出可以单独维护
3. 后续如果继续补“封面识别”“附件页剔除”“同类文档再合并”等逻辑，不会让主引擎继续膨胀

## 这次做了什么

### 1. 新增 Bundle Splitter 模块

新增文件：

- `ocr_engine_7_bundle_splitter.py`

这个模块负责：

1. 对每一页构造单页 OCR 视角
2. 复用第 6 步 `Mixed Document Router` 做页级标签判断
3. 根据页级标签和首页信号识别子文档边界
4. 导出切分后的子 PDF 和段信息 JSON

### 2. 不只看“标签变化”，还看“首页信号”

如果只靠标签变化来切分，会漏掉一个很常见的场景：

- 两张连续的发票
- 两张连续的表单

它们标签一样，但其实已经是两个子文档。

所以这次额外引入了“首页信号”：

- 顶部标题关键词
- 键值对密度
- 总额 / item table 提示
- report 标题信号

这样即使是：

- `invoice + invoice`
- `form + form`

也有机会切成两个段，而不是误并在一起。

### 3. 新增正式产物

每次 OCR 任务现在会额外产出：

- `bundle_splitter.json`
- `bundle_segments/segment_01.json`
- `bundle_segments/segment_02.json`
- `bundle_segments/...pdf`

其中：

- `bundle_splitter.json`
  - 是总索引
  - 记录所有页分类和所有段范围

- `segment_xx.json`
  - 是单段元数据
  - 便于后续单独消费某一个子文档

- `segment_xx_*.pdf`
  - 是真正切出来的子 PDF

### 4. 多文档 bundle 不再误走单一业务抽取链

这次顺手修正了一个实际问题：

如果一个 PDF 里同时拼了：

- 发票
- 表单
- 收据

那就不应该把整包再当成“单一 invoice”或“单一 form”去输出顶层业务结果。

所以现在一旦识别到：

- `segment_count > 1`

主流程会：

- 保留 `bundle_splitter_result`
- 顶层 `form.json` / `receipt_invoice.json` 输出 `skipped` 占位结果

这样可以避免误导性的整包抽取结果。

## 修改文件

### 1. `ocr_engine_7_bundle_splitter.py`

新增核心 bundle 切分模块，负责：

- 页级路由复用
- 起止页识别
- 子 PDF 导出
- 单段 JSON 导出
- `bundle_splitter.json` 写入

### 2. `ocr_engine.py`

主流水线新增：

- `bundle_splitter` 配置项
- `bundle_splitter_result`
- `bundle_splitter.json`

并在检测到多文档 bundle 时：

- 跳过整包级 `form_to_json`
- 跳过整包级 `receipt_invoice_extractor`

### 3. `job_skeleton.py`

任务骨架新增：

- `bundle_json_path`
- `bundle_segments_dir`

让 bundle 切分结果成为正式产物。

### 4. `app.py`

接口返回和 `job_manifest.json` 新增：

- `bundle_json` 下载链接
- `bundle_segments_dir` 目录入口
- `bundle_segment_count`
- `bundle_detected`
- `bundle_splitter` 摘要结果

### 5. `web/index.html`

前端结果页新增：

- 子文档数量统计
- `bundle_splitter.json` 下载入口
- 总览里展示 `bundle_splitter` 摘要

### 6. `tests/test_bundle_splitter.py`

新增测试覆盖：

1. 同类型连续发票的切分
2. 完整流水线写出 `bundle_splitter.json`
3. 子 PDF 和单段 JSON 的导出
4. 多文档 bundle 时顶层业务抽取输出 `skipped`

## 当前实现策略

这次仍然是启发式切分，不是训练好的 document segmentation 模型。

当前主要依赖这些信号：

- 页级路由标签
- 页顶部关键词
- 表单键值对密度
- 票据金额 / item table 信号
- report 顶部标题信号

这种方式的好处是：

- 易解释
- 依赖少
- 和现有 1–6 步能力复用度高

## 当前边界

当前更擅长处理的是：

- 发票 / 收据 / 表单 / 证件混拼的 bundle
- 同类短文档连续拼接
- 规则性较强的扫描包

当前边界包括：

- 页内同时混有两个子文档的极端排版
- 标题词极少、OCR 又很差的扫描页
- 多份连续 report 且首页特征非常弱的场景
- 需要视觉分割模型才能稳定识别的复杂订书扫描件

## 总结

这次改造之后，项目除了能识别文档类型和分发处理链之外，又进一步具备了：

- 对拼接 PDF 自动识别子文档边界
- 给出每个子文档的起止页
- 导出独立子 PDF 和 JSON 段

也就是说，这个 demo 现在不仅能“看懂一份文档”，还开始具备“先把一包文档拆开，再分别处理”的基础能力。
