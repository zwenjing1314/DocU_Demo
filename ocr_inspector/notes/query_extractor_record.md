# Query Extractor 开发记录

## 背景

本次是在现有八个能力基础上继续扩展：

1. `OCR Inspector`
2. `Layout Reader`
3. `Table to CSV`
4. `Form to JSON`
5. `Receipt & Invoice Extractor`
6. `Mixed Document Router`
7. `Bundle Splitter`
8. `Signature & Handwriting Review`

这次新增主题是：

- query-based extraction
- 自然语言提问找字段

最小交付目标是：

- 对同一份文档支持提问
- 例如：
  - “总金额是多少”
  - “合同起始日是哪天”

过关标准是：

- 输出答案
- 输出页码
- 最好还能给 bbox 或原文片段

## 为什么继续拆新文件

用户已经持续强调 `ocr_engine.py` 太大。

这次没有把 query 逻辑继续塞回主引擎，而是新增独立模块：

- `ocr_engine_9_query.py`

这样做的目的很直接：

1. `ocr_engine.py` 继续负责主流水线装配
2. 自然语言提问和字段匹配逻辑可以单独维护
3. 后续如果继续补“更多问法”“更复杂字段”“多跳问题”，不会继续把主引擎撑大

## 这次做了什么

### 1. 新增 Query Extractor 模块

新增文件：

- `ocr_engine_9_query.py`

这个模块负责：

1. 从 OCR / form / receipt / line 文本里构建候选答案池
2. 对 query 做轻量意图识别
3. 返回答案、页码、bbox、原文片段
4. 维护查询历史

### 2. 主流程先生成 query 索引

这次没有把“提问”直接写死在 OCR 接口里，而是先在 OCR 结束后生成：

- `query_extractor.json`

里面会保存：

- 可查询候选项
- 字段类型
- 页码
- bbox
- snippet
- 查询历史

这样后面无论你在前端提问，还是将来做 CLI / API，都可以复用同一份索引，而不是每次都重新扫整份文档。

### 3. 新增 `/query` 接口

为了满足“对同一份文档支持提问”的最小交付，这次新增了：

- `POST /query`

入参是：

- `job_id`
- `query`

返回结果里会包含：

- `answer`
- `page_num`
- `bbox`
- `snippet`
- `matched_field`
- `confidence`

### 4. 前端支持直接提问

现在 OCR 结果页会额外出现一个 Query Extractor 面板。

你可以直接输入：

- “总金额是多少”
- “合同起始日是哪天”
- “申请人姓名是什么”

然后页面会直接显示：

- 答案
- 页码
- bbox
- 片段

## 修改文件

### 1. `ocr_engine_9_query.py`

新增核心 query 模块，负责：

- 候选答案构建
- query 意图识别
- 答案返回
- `query_extractor.json` 写入 / 读取

### 2. `ocr_engine.py`

主流水线新增：

- `query_extractor` 配置项
- `query_extractor_result`
- `query_extractor.json`

### 3. `job_skeleton.py`

任务骨架新增：

- `query_json_path`

让 `query_extractor.json` 成为正式产物。

### 4. `app.py`

接口和响应新增：

- `query_json` 下载链接
- `query_candidate_count`
- `query_extractor` 摘要结果
- `POST /query`

### 5. `web/index.html`

前端新增：

- Query Extractor 面板
- 自然语言提问输入框
- 提问结果展示
- `query_extractor.json` 下载入口

### 6. `tests/test_query_extractor.py`

新增测试覆盖：

1. “总金额是多少” 这类金额问题
2. “合同起始日是哪天” 这类日期问题
3. 完整流水线写出 `query_extractor.json`
4. `/query` 接口返回答案、页码、bbox / snippet

## 当前实现策略

这次仍然是启发式 query extraction，不是大模型 RAG，也不是复杂语义解析系统。

当前主要依赖这些信号：

- `Form to JSON` 的字段结果
- `Receipt & Invoice Extractor` 的结构化结果
- 行文本里的金额 / 日期 / 编号模式
- query 关键词和字段别名匹配

这种方式的好处是：

- 快
- 依赖少
- 可解释
- 很适合 demo 阶段先把“可问可答”打通

## 当前边界

当前更擅长处理的是：

- 金额类问题
- 日期类问题
- 发票编号类问题
- 表单字段类问题

当前边界包括：

- 很长的复杂推理型问题
- 需要跨页多跳聚合的问题
- 非常抽象的开放式问题
- 没有任何结构信号、只靠弱语义猜测的问题

## 总结

这次改造之后，项目除了能抽取结构化结果、做路由、切分 bundle、提示人工复核之外，又进一步具备了：

- 对同一份文档做自然语言提问
- 返回答案、页码
- 并尽量给出 bbox 和原文片段

也就是说，这个 demo 现在不仅能“产出结果文件”，还开始具备“按问题取字段”的交互能力。
