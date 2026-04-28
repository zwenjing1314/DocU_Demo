# Mixed Document Router 开发记录

## 背景

本次是在已有五个能力基础上继续扩展：

1. `OCR Inspector`
2. `Layout Reader`
3. `Table to CSV`
4. `Form to JSON`
5. `Receipt & Invoice Extractor`

这次新增主题是：

- 文档分类
- 路由分发

最小交付目标是：

- 给一个混合文件夹自动打上 `invoice / receipt / form / report / id` 标签
- 并调用不同处理链

过关标准是：

- 至少 4 类文档能分对大部分
- 不再所有文档走同一条逻辑

## 为什么继续拆新文件

用户已经明确指出 `ocr_engine.py` 太大。

这次没有继续把路由规则塞回主引擎，而是新增独立模块：

- `ocr_engine_6_router.py`

这样做的目的很直接：

1. 让 `ocr_engine.py` 继续做主流水线装配
2. 让分类规则和路由策略可以单独维护
3. 后续如果继续补 `contract / resume / bank_statement` 之类新标签，不会把主引擎改得越来越重

## 这次做了什么

### 1. 新增混合文档路由模块

新增文件：

- `ocr_engine_6_router.py`

这个模块负责三件事：

1. 汇总 OCR / layout / table 信号
2. 对文档打上 `invoice / receipt / form / report / id` 标签
3. 生成对应的处理链路

当前输出里会包含：

- `label`
- `scores`
- `matched_signals`
- `selected_pipeline`

### 2. 主流程改成“先分类，再分流”

这次最大的变化不是多了一个标签，而是主流程真正分流了：

- `form / id`
  - 走 `form_to_json`

- `invoice / receipt`
  - 走 `receipt_invoice_extractor`

- `report`
  - 只保留基础的 OCR / layout / table 结果

这意味着现在不再是所有文档都无差别执行同一套业务抽取逻辑。

### 3. 保留稳定导出结构

虽然链路变成了按标签分流，但这次没有让前端和下载入口变得不稳定。

现在即使某类文档没有命中某条业务链，也会写出占位结果：

- `form.json`
- `receipt_invoice.json`

占位结果会明确写出：

- `status: "skipped"`
- `skip_reason`

这样前端、接口和历史下载链接都不会因为“某个文件不存在”而坏掉。

### 4. 新增路由导出文件

每次 OCR 任务现在会额外生成：

- `document_router.json`

这个文件会记录：

- 文档最终标签
- 各标签得分
- 命中的分类信号
- 实际分发到哪条处理链

### 5. 新增混合文件夹批量入口

为了满足“给一个混合文件夹自动打标签”的最小交付，这次在路由模块里补了一个批量工具函数：

- `route_documents_in_folder(...)`

它会：

1. 遍历一个目录里的 PDF / 图片
2. 调用现有 OCR 流水线
3. 收集每个文档的标签和处理链
4. 输出一个批量汇总文件：
   - `mixed_router_index.json`

## 修改文件

### 1. `ocr_engine_6_router.py`

新增核心路由模块，负责：

- 分类打标
- 处理链选择
- `document_router.json` 写入
- 混合文件夹批量路由汇总

### 2. `ocr_engine.py`

主流水线改造为：

- 先完成 OCR / layout / table
- 再做路由决策
- 最后根据标签执行不同业务抽取链

并新增：

- `document_label`
- `document_router_result`
- `document_router.json`

### 3. `ocr_engine_4_json.py`

新增：

- `build_skipped_form_result(...)`

用于在当前文档不走表单链时，仍然输出稳定的 `form.json` 占位结果。

### 4. `ocr_engine_5_receipt.py`

新增：

- `build_skipped_receipt_invoice_result(...)`

用于在当前文档不走票据链时，仍然输出稳定的 `receipt_invoice.json` 占位结果。

### 5. `job_skeleton.py`

任务骨架新增：

- `router_json_path`

让 `document_router.json` 成为正式产物。

### 6. `app.py`

接口响应和 `job_manifest.json` 新增：

- `router_json` 下载链接
- `document_router` 结果摘要
- `document_label`
- `router_confidence`

### 7. `web/index.html`

前端结果页新增：

- 文档标签展示
- 路由置信度展示
- `document_router.json` 下载入口
- 总览里展示路由摘要

### 8. `tests/test_mixed_document_router.py`

新增测试覆盖：

1. `invoice` 分类
2. `receipt` 分类
3. `form` 分类
4. `report` 分类
5. `id` 分类
6. 混合文件夹批量路由
7. 完整流水线写出 `document_router.json`

## 当前实现策略

这次仍然是启发式路由，不是训练好的文档分类模型。

当前主要依赖这些信号：

- 关键词
- 键值对密度
- checkbox 标记
- 金额 / total / tax 提示词
- item table 表头
- heading / paragraph 版面结构
- 页数和文本长度

这种方式的优点是：

- 快
- 依赖少
- 容易解释为什么分到某个标签

## 当前边界

当前更擅长处理的是：

- 规则 invoice
- 简单 receipt
- 申请表 / 登记表
- 结构比较清晰的 report
- 常见身份证件类页面

当前边界包括：

- 非常模糊的多类别混合页
- 标签词很少的特殊业务单据
- 版式极度自由的扫描件
- 需要真正视觉分类模型才能稳定区分的边界文档

## 总结

这次改造之后，项目从“单文档统一处理”进一步升级为：

- 能先判断文档属于哪一类
- 再把文档分发到不同业务链
- 并且支持对混合文件夹做批量标签汇总

也就是说，这个 demo 现在不仅能“抽内容”，还开始具备“先识别文档类型，再选择处理策略”的基础路由能力。
