# Custom Schema Extractor 开发记录

## 背景

本次是在现有九个能力基础上继续扩展：

1. `OCR Inspector`
2. `Layout Reader`
3. `Table to CSV`
4. `Form to JSON`
5. `Receipt & Invoice Extractor`
6. `Mixed Document Router`
7. `Bundle Splitter`
8. `Signature & Handwriting Review`
9. `Query Extractor`

这次新增主题是：

- 自定义字段 schema
- 单领域抽取

并且这一步只选一个垂直领域，不同时做多种类型。

本次选择的唯一垂直是：

- `合同`

最小交付目标是：

- 对合同文档输出统一的 8 字段 JSON

过关标准是：

- 同类合同文档能比较稳定地产生统一 JSON

## 为什么继续拆新文件

用户已经持续强调 `ocr_engine.py` 太大。

这次没有把合同垂直抽取规则继续塞回主引擎，而是新增独立模块：

- `ocr_engine_10_contract_schema.py`

这样做的目的很直接：

1. `ocr_engine.py` 继续做主流水线装配
2. 合同领域规则可以单独维护
3. 后面如果继续增强合同字段、补充英文合同、补充双语合同规则，不会继续把主引擎撑大

## 这次选择的合同 8 字段

当前固定输出字段是：

1. `contract_title`
2. `contract_number`
3. `party_a`
4. `party_b`
5. `signing_date`
6. `effective_date`
7. `end_date`
8. `total_amount`

之所以选这 8 个，是因为它们既常见，又比较适合做统一 JSON，且和现有第 4、6、9 步能力衔接自然。

## 这次做了什么

### 1. 新增合同 schema 模块

新增文件：

- `ocr_engine_10_contract_schema.py`

这个模块负责：

1. 判断文档是否“像合同”
2. 从合同文本里抽取固定 8 字段
3. 对非合同文档输出稳定的 `skipped` 占位结果
4. 写出 `contract_schema.json`

### 2. 复用第 9 步 Query Extractor

这次没有完全重写一套字段查找逻辑，而是优先复用第 9 步 query 索引：

- `effective_date`
- `end_date`
- `total_amount`

这些字段会先走合同直规则，
缺失时再回退到 query-based lookup。

这样做的好处是：

- 规则复用度高
- 字段定位更稳
- 更适合后续继续问答和字段抽取共存

### 3. 复用合同专用文本规则

对下面这些字段，本次仍然增加了合同专用规则：

- `contract_title`
- `contract_number`
- `party_a`
- `party_b`
- `signing_date`

例如会识别：

- `Contract No`
- `合同编号`
- `Party A`
- `Party B`
- `甲方`
- `乙方`
- `Signing Date`
- `签署日期`

### 4. 非合同文档输出稳定占位结果

这次没有让非合同文档“什么都不输出”，而是写出稳定结构：

- `status: "skipped"`
- `skip_reason`

这样前端、接口和下载入口都不会因为某个字段不存在而崩掉。

## 修改文件

### 1. `ocr_engine_10_contract_schema.py`

新增核心合同垂直抽取模块，负责：

- 合同识别
- 8 字段抽取
- `contract_schema.json` 写入

### 2. `ocr_engine.py`

主流水线新增：

- `custom_schema_extractor` 配置项
- `contract_schema_result`
- `contract_schema.json`

### 3. `job_skeleton.py`

任务骨架新增：

- `contract_schema_json_path`

让 `contract_schema.json` 成为正式产物。

### 4. `app.py`

接口响应和 `job_manifest.json` 新增：

- `contract_schema_json` 下载链接
- `contract_field_count`
- `contract_detected`
- `contract_schema` 摘要结果

### 5. `web/index.html`

前端结果页新增：

- 合同字段数量统计
- `contract_schema.json` 下载入口
- 总览里展示合同 schema 摘要

### 6. `ocr_engine_9_query.py`

顺手增强了合同问法支持，例如：

- `signing_date`
- `party_a`
- `party_b`

这样合同垂直和 Query Extractor 的衔接更自然。

### 7. `tests/test_contract_schema_extractor.py`

新增测试覆盖：

1. 合同 8 字段直接抽取
2. 完整流水线写出 `contract_schema.json`

## 当前实现策略

这次仍然是启发式垂直抽取，不是训练好的合同理解模型。

当前主要依赖这些信号：

- 合同标题关键词
- 合同编号规则
- `甲方 / 乙方` 或 `Party A / Party B`
- 生效 / 结束 / 签署日期关键词
- Query Extractor 的日期 / 金额候选

这种方式的优势是：

- 可解释
- 规则清晰
- 很适合 demo 阶段先做一个垂直做深

## 当前边界

当前更擅长处理的是：

- 中英文合同
- 标题明确的协议类文档
- 含 `甲方 / 乙方` 或 `Party A / Party B` 的常见合同
- 字段相对规整的服务协议 / 合作协议

当前边界包括：

- 字段极度自由的长合同
- 没有明确 parties 标记的复杂法律文本
- 需要真正法律条款理解才能稳定抽出的深层字段
- 图片质量很差、OCR 自身错误很多的扫描合同

## 总结

这次改造之后，项目除了能抽 OCR、做结构恢复、路由、切分、复核和提问之外，又进一步具备了：

- 针对一个垂直领域做更深的统一 JSON 抽取
- 并且这个垂直已经明确收敛为“合同”

也就是说，这个 demo 现在不仅能做通用文档理解，还开始具备“单领域做深”的能力。
