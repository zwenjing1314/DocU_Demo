# Receipt & Invoice Extractor 开发记录

## 背景

本次是在现有四个能力基础上继续扩展：

1. `OCR Inspector`
2. `Layout Reader`
3. `Table to CSV`
4. `Form to JSON`

本次新增主题是：

- 票据 schema
- line items
- 税额 / 总额抽取

最小交付目标是：

- 对收据和发票输出 `vendor / date / tax / total / items[]`

过关标准是：

- 总额、税额、日期基本靠谱
- 明细行不大面积串行

## 为什么继续拆新文件

用户已经多次指出 `ocr_engine.py` 太大。

这次没有把票据规则继续塞回主引擎，而是新增独立模块：

- `ocr_engine_5_receipt.py`

这样做的目的很直接：

1. `ocr_engine.py` 继续只负责主流水线装配
2. 票据 / 发票抽取逻辑可以单独维护
3. 后面如果继续做 vendor 识别、行项目优化、币种扩展，不会把主引擎越改越乱

## 本次实现思路

### 1. 先抽通用票据 schema

当前固定输出结构集中在：

- `normalized_receipt`

字段包括：

- `vendor`
- `date`
- `invoice_number`
- `currency`
- `subtotal`
- `tax`
- `total`
- `items`

### 2. items[] 优先复用表格结果

这次最核心的一点，是没有重新发明一套明细恢复逻辑，而是优先复用第 3 步 `Table to CSV` 已经恢复好的表格：

- 如果检测到比较像 item table 的表格
- 就直接从表头和数据行恢复：
  - `description`
  - `quantity`
  - `unit_price`
  - `amount`

这样做的好处是：

- 明细行不容易大面积串行
- 比单纯靠 OCR 文本切分更稳

### 3. 无表格时再用行文本兜底

对一些简单小票，不一定能形成规则表格。

所以新增了行文本兜底：

- 识别类似 `Latte 4.50`
- 或 `Burger 2 x 12.50 25.00`

虽然这是启发式方案，但对简单小票足够覆盖最小交付。

### 4. total / tax / date / vendor 用 OCR 行文本抽

这些字段目前主要从 OCR 行里恢复：

- `date`
  - 支持 `2026/04/28`
  - 支持 `2026年4月28日`
  - 支持常见 `MM/DD/YYYY`

- `tax`
  - 识别 `Tax / VAT / GST / 税额 / 税金`

- `total`
  - 识别 `Grand Total / Total / Amount Due / 合计 / 总计`

- `vendor`
  - 从首页顶部几行中选择最像商户名的一行
  - 会避开 `invoice / receipt / tax / total / date` 之类非商户提示词

## 新增文件

### 1. `ocr_engine_5_receipt.py`

这是本次新增的核心模块，负责：

- 票据 / 发票 schema 组织
- vendor/date/invoice_number/currency 提取
- total/subtotal/tax 提取
- line items 提取
- `receipt_invoice.json` 写入

### 2. `tests/test_receipt_invoice_extractor.py`

新增测试覆盖：

1. 从发票表格抽 item rows
2. 从简单收据行文本兜底抽 item rows
3. 完整流水线写出 `receipt_invoice.json`

### 3. `notes/receipt_invoice_extractor_record.md`

记录这次开发内容、设计思路和当前边界。

## 修改文件

### 1. `ocr_engine.py`

主流水线新增对收据 / 发票抽取的接入：

- 在表格导出之后调用 `ocr_engine_5_receipt.py`
- 这样 `items[]` 能优先复用 `tables`
- 生成：
  - `receipt_invoice_result`
  - `receipt_invoice_analysis`
  - `receipt_invoice.json`

### 2. `job_skeleton.py`

任务骨架新增：

- `receipt_json_path`

让 `receipt_invoice.json` 成为正式产物。

### 3. `app.py`

接口和 `job_manifest.json` 新增：

- `receipt_json` 下载链接
- `receipt_invoice` 响应摘要
- `receipt_line_item_count` 分析统计

### 4. `web/index.html`

前端结果总览新增：

- `receipt_invoice.json` 下载链接
- 明细行统计
- 结果摘要里展示 `receipt_invoice.normalized_receipt`

另外顺手清理了之前前端里残留的重复下载链接和重复文本。

## 新增产物

每次 OCR 任务现在会额外产出：

- `outputs/<job_id>/receipt_invoice.json`

而且 `ocr.json` 中也会多出：

- `receipt_invoice_result`
- `receipt_invoice_analysis`

## 当前边界

这次实现的是最小可交付版，所以仍然是启发式抽取，不是训练好的票据理解模型。

当前更擅长处理的是：

- 规则发票
- 带 item table 的 invoice
- 简单商超小票
- 简单餐饮收据

当前边界包括：

- vendor 识别仍然主要靠首页顶部启发式判断
- 没有做复杂税率拆分
- 多币种混排场景支持有限
- 非规则长小票的 item 行兜底仍可能有误差

## 总结

这次改造之后，项目除了：

- 抽 OCR 词框
- 恢复版面结构
- 导出表格
- 提取表单 JSON

现在又进一步具备了：

- 收据 / 发票基础字段抽取
- 税额 / 总额恢复
- 明细 items[] 输出

也就是说，这个 demo 已经从“看 OCR 结果”逐渐变成了一个更完整的文档理解原型系统。
