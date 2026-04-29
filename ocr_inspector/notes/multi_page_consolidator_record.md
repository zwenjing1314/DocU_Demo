# Multi-page Consolidator 开发记录

## 背景

这次新增第 11 个能力：

- Multi-page Consolidator

新增主题包括：

- 跨页聚合
- 去重
- 字段合并
- 总额校验

继承前面这些能力：

- 5. `Receipt & Invoice Extractor`
- 7. `Bundle Splitter`
- 10. `Custom Schema Extractor`

最小交付目标：

- 对多页发票、银行流水或长表单做跨页合并

过关标准：

- 同一实体不重复记两次
- 跨页 totals 能对上

## 为什么新增独立文件

`ocr_engine.py` 已经很大，这次继续按前面几个功能的方式拆独立模块：

- `ocr_engine_11_consolidator.py`

主引擎只负责装配：

1. 调用前面 OCR / table / receipt / bundle / contract 结果
2. 调用 consolidator
3. 写出 `multi_page_consolidation.json`

跨页合并、去重和校验规则都放在新文件里维护。

## 新增输出

这次新增正式产物：

- `multi_page_consolidation.json`

这个 JSON 里包含：

- `document_kind`
- `page_range`
- `segments`
- `pages`
- `consolidated.receipt_invoice`
- `consolidated.bank_statement`
- `consolidated.form`
- `consolidated.contract`
- `duplicates`
- `analysis`

## 当前实现内容

### 1. 多页发票 / 收据合并

当前会从所有页的表格里找 item 表，不只拿单页结果。

合并字段包括：

- `description`
- `quantity`
- `unit_price`
- `amount`
- `page_num`
- `table_id`
- `row_index`

去重 key 主要由以下字段组成：

- 描述
- 数量
- 单价
- 金额

这样可以处理页眉重复、跨页重复行、同一明细被重复抽到的问题。

### 2. totals 校验

当前对票据做轻量对账：

- `item_sum = sum(items.amount)`
- `calculated_total = item_sum + tax`
- 对比 `receipt_invoice_result.normalized_receipt.total`

校验结果写入：

- `total_validation.status`
- `total_validation.difference`
- `total_validation.tolerance`

状态包括：

- `matched`
- `mismatch`
- `not_available`

### 3. 银行流水跨页交易合并

当前会识别带这些列的表格：

- date
- description
- debit
- credit
- amount
- balance

会合并所有页交易，并按交易日期、摘要、借贷金额、余额做去重。

### 4. 银行流水余额校验

当前会从文本里找：

- opening balance
- closing balance
- 期初余额
- 期末余额

并校验：

- `opening_balance + net_change == closing_balance`

结果写入：

- `balance_validation.status`
- `balance_validation.difference`

### 5. 长表单和合同字段合并

这次没有重写表单或合同抽取，而是复用已有结果：

- `form_result`
- `contract_schema_result`

其中表单会对 `selected_options` 做一次去重排序，合同会保留第 10 步的 8 字段结构。

## 修改文件

### 1. `ocr_engine_11_consolidator.py`

新增第 11 步核心模块，负责：

- 跨页明细合并
- 跨页交易合并
- 重复实体识别
- total / balance 校验
- 写出 JSON

### 2. `ocr_engine.py`

新增：

- `multi_page_consolidator` 配置项
- `multi_page_consolidation_result`
- `multi_page_consolidation.json`

### 3. `job_skeleton.py`

新增：

- `multi_page_consolidation_json_path`

### 4. `app.py`

接口响应和 manifest 新增：

- `multi_page_consolidation_json`
- `consolidated_item_count`
- `consolidated_transaction_count`
- `duplicate_entity_count`
- `total_check_status`
- `multi_page_consolidation`

### 5. `web/index.html`

前端结果页新增：

- 跨页明细数量
- 跨页交易数量
- 去重实体数量
- `multi_page_consolidation.json` 下载入口

### 6. `tests/test_multi_page_consolidator.py`

新增测试覆盖：

1. 多页发票 item 合并、去重、total 校验
2. 银行流水 transaction 合并、去重、balance 校验
3. 完整 OCR 流水线写出 `multi_page_consolidation.json`

## 当前边界

当前实现是 demo 阶段的启发式规则，不是训练好的财务模型。

更适合：

- 表头比较明确的多页发票
- 明细金额列清晰的票据表格
- 带 date / description / debit / credit / balance 表头的银行流水
- 已经被第 4 步和第 10 步抽出的长表单 / 合同字段

暂时不擅长：

- 表头缺失且列顺序很混乱的流水
- 同名同金额但实际是两笔不同交易的极端场景
- total 分摊、折扣、运费、多税率等复杂财务逻辑
- OCR 错误很多导致金额或日期本身识别错的文件

后续如果继续做深，可以把 dedupe key 改成可配置策略，并为 invoice / bank statement 分别增加更细的 domain schema。
