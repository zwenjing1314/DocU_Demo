# Review Workbench 开发记录

## 背景

这次新增第 12 个能力：

- Review Workbench

新增主题包括：

- 人工复核台
- 修订记录
- 低置信度队列

继承前面这些能力：

- 8. `Signature & Handwriting Review`
- 10. `Custom Schema Extractor`
- 11. `Multi-page Consolidator`

最小交付目标：

- 做一个很小的网页或本地界面
- 能查看原图
- 能查看预测结果
- 能人工改值并保存

过关标准：

- 让“模型错了以后怎么办”第一次变成系统能力

## 为什么新增独立文件

`ocr_engine.py` 已经很大，这次仍然继续拆模块。

新增文件：

- `ocr_engine_12_review_workbench.py`

这个文件只负责：

1. 从已有输出中构建复核台 state
2. 整理预测字段
3. 整理低置信度 / 可疑区域队列
4. 保存人工修订记录

页面和接口分别放在：

- `web/review_workbench.html`
- `app.py`

这样第 12 步不会把 OCR 主流程继续撑大。

## 新增页面

新增本地网页：

- `/review/{job_id}`

这个页面会加载：

- `/api/review/{job_id}/state`

页面包含三块：

1. 页面原图和复核叠框图
2. 可编辑预测字段
3. 低置信度 / 可疑区域队列

点击队列项时，会跳到对应页面。

保存时，只提交被人工改过的字段。

## 新增 API

### 1. 获取复核台状态

接口：

- `GET /api/review/{job_id}/state`

返回内容包括：

- `pages`
- `predicted_fields`
- `review_queue`
- `revisions`
- `analysis`

### 2. 保存人工修订

接口：

- `POST /api/review/{job_id}/save`

保存内容包括：

- `reviewer`
- `note`
- `revisions[]`

每条 revision 包含：

- `field_id`
- `field_path`
- `source`
- `page_num`
- `old_value`
- `new_value`
- `review_status`

### 3. 读取修订记录

接口：

- `GET /api/review/{job_id}/revisions`

这个接口方便后续外部系统只读取人工修订记录。

## 新增输出

新增正式产物：

- `review_workbench_revisions.json`

这个文件保留：

- `revision_batches`
- `latest_revisions`
- `analysis.revision_batch_count`
- `analysis.revision_count`
- `analysis.latest_revision_count`

这样不是简单覆盖结果，而是能保留每次人工修订批次。

## 复核台 state 的来源

### 1. 第 8 步

来自：

- `signature_handwriting_review_result`

用于构建：

- signature region 队列
- handwriting region 队列
- suspicious field 队列
- low-confidence region 队列

### 2. 第 10 步

来自：

- `contract_schema_result`

用于展示可编辑合同字段。

### 3. 第 11 步

来自：

- `multi_page_consolidation_result`

用于展示跨页 totals、balance 等需要人工确认的字段。

### 4. OCR 原始结果

来自：

- `ocr.json`

用于补充 word 级低置信度队列。

## 修改文件

### 1. `ocr_engine_12_review_workbench.py`

新增核心数据模块，负责：

- 构建复核台 state
- 汇总预测字段
- 汇总低置信度队列
- 保存修订记录

### 2. `web/review_workbench.html`

新增小型复核网页，支持：

- 查看原图
- 查看复核叠框图
- 编辑预测字段
- 查看低置信度队列
- 保存人工修订

### 3. `app.py`

新增：

- `/review/{job_id}`
- `/api/review/{job_id}/state`
- `/api/review/{job_id}/save`
- `/api/review/{job_id}/revisions`

同时在主接口响应里增加：

- `review_workbench`
- `review_workbench_revisions_json`
- `review_revision_count`

### 4. `job_skeleton.py`

新增：

- `review_workbench_revisions_json_path`

### 5. `web/index.html`

结果总览新增：

- `Review Workbench` 入口
- `review_workbench_revisions.json` 下载入口
- 人工修订数量统计

### 6. `ocr_engine.py`

配置中新增：

- `review_workbench`

主流程没有新增复杂逻辑，只标记这个能力已启用。

### 7. `tests/test_review_workbench.py`

新增测试覆盖：

1. 从已有 OCR 输出构建复核台 state
2. 保存人工修订批次
3. API 拉取 state 并保存 revision

## 当前边界

当前 Review Workbench 是最小可用闭环，不是完整标注平台。

当前能做：

- 看原图
- 看复核叠框图
- 看预测字段
- 看低置信度 / 可疑队列
- 改字段值
- 保存修订历史

暂时没有做：

- bbox 拖拽编辑
- 多人协同锁
- 审核状态流转
- 修订结果反写回 `ocr.json`
- 大规模任务分配

后续如果继续做深，可以把 `review_workbench_revisions.json` 作为人工反馈数据源，再接入主动学习、评测集回放或字段级准确率统计。
