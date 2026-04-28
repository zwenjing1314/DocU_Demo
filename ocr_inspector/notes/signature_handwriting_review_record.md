# Signature & Handwriting Review 开发记录

## 背景

本次是在现有七个能力基础上继续扩展：

1. `OCR Inspector`
2. `Layout Reader`
3. `Table to CSV`
4. `Form to JSON`
5. `Receipt & Invoice Extractor`
6. `Mixed Document Router`
7. `Bundle Splitter`

这次新增主题是：

- 签名区域
- 手写字段
- 低置信度区域高亮

最小交付目标是：

- 在表单 / 合同上标出签名区、手写区和可疑字段

过关标准是：

- 不追求完美识别
- 但能把“该人工复核的地方”圈出来

## 为什么继续拆新文件

用户已经持续强调 `ocr_engine.py` 太大。

这次没有继续把复核逻辑塞回主引擎，而是新增独立模块：

- `ocr_engine_8_review.py`

这样做的目的很直接：

1. `ocr_engine.py` 继续负责主流水线装配
2. 签名 / 手写 / 可疑字段复核逻辑可以单独维护
3. 后续如果继续补“印章区”“涂改区”“疑似漏填项”等规则，不会继续把主引擎撑大

## 这次做了什么

### 1. 新增人工复核模块

新增文件：

- `ocr_engine_8_review.py`

这个模块负责：

1. 找出签名标签附近的签名区域
2. 找出低置信度的手写候选区域
3. 找出需要人工重点复核的可疑字段
4. 生成单独的复核叠框图

### 2. 复核目标不是“识别签名内容”

这次实现故意没有把目标定成：

- 精确识别签名内容
- 精确识别每一笔手写字

而是把目标定成更适合 demo 落地的形式：

- 把签名区圈出来
- 把像手写填入的区域圈出来
- 把缺失字段、噪声字段、低置信度字段提出来

也就是说，这一步更像“人工复核助手”，不是“最终自动审核器”。

### 3. 新增专门的复核叠框图

每次任务现在会额外生成：

- `signature_handwriting_review.json`
- `review_overlays/page_001_review.png`
- `review_overlays/page_002_review.png`

颜色区分大致是：

- 橙色：签名区域
- 蓝色：手写候选区域
- 红色：可疑字段

这样用户打开复核叠框图时，不需要自己再去翻 `ocr.json` 才知道该看哪里。

### 4. 复核规则复用了已有能力

这次没有凭空新造一套理解链，而是尽量复用已有结果：

- 第 1 步 OCR 的词级置信度
- 第 4 步 `Form to JSON` 的字段结构
- 第 7 步 `Bundle Splitter` 的页级标签

这样做的好处是：

- 依赖少
- 规则更容易解释
- 和前面能力之间能自然串起来

## 修改文件

### 1. `ocr_engine_8_review.py`

新增核心复核模块，负责：

- 签名区域检测
- 手写候选区域检测
- 可疑字段识别
- `signature_handwriting_review.json` 写入
- `review_overlays/` 叠框图生成

### 2. `ocr_engine.py`

主流水线新增：

- `signature_handwriting_review` 配置项
- `signature_handwriting_review_result`
- `signature_handwriting_review.json`
- `review_overlays/`

### 3. `job_skeleton.py`

任务骨架新增：

- `review_json_path`
- `review_overlays_dir`

让复核 JSON 和复核叠框图成为正式产物。

### 4. `app.py`

接口和 `job_manifest.json` 新增：

- `review_json` 下载链接
- `review_overlays_dir` 目录入口
- `signature_region_count`
- `handwriting_region_count`
- `suspicious_field_count`
- `signature_handwriting_review` 结果摘要

并把每页的复核叠框图链接也挂进了 `page_previews`。

### 5. `web/index.html`

前端结果页新增：

- 签名区域统计
- 手写候选区统计
- 可疑字段统计
- `signature_handwriting_review.json` 下载入口
- 页面级复核叠框图预览和链接

### 6. `tests/test_signature_handwriting_review.py`

新增测试覆盖：

1. 签名区识别
2. 手写候选区识别
3. 可疑字段识别
4. 完整流水线写出复核 JSON 和复核叠框图

## 当前实现策略

这次仍然是启发式复核，不是训练好的 handwriting / signature detection 模型。

当前主要依赖这些信号：

- 签名关键词
- 词级低置信度
- 表单字段结构
- 缺失字段
- 格式异常字段
- bundle 页级标签

这种方式的优势是：

- 落地快
- 可解释
- 很适合 demo 阶段的人工复核辅助

## 当前边界

当前更擅长处理的是：

- 表单页
- 申请页
- 带签名栏的合同页
- 低置信度手写填空区域

当前边界包括：

- 完全没有标签的自由签名页
- OCR 本身几乎完全失真的手写扫描件
- 需要真正视觉笔迹检测模型才能稳定识别的复杂潦草签名
- 只靠文本结构很难判断的“印章 vs 签名”边界场景

## 总结

这次改造之后，项目除了能抽取内容、分类路由和拆分 bundle 之外，又进一步具备了：

- 自动圈出签名区
- 自动圈出手写候选区
- 自动标记可疑字段
- 生成专门给人工复核看的叠框图

也就是说，这个 demo 现在不仅能“提取结果”，还开始具备“提示人工重点复核位置”的能力。
