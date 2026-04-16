# OCR Inspector（Ubuntu 版）

一个最小但可扩展的 OCR Demo：

- 上传 PDF 或图片
- 渲染成统一页图
- 调用 Tesseract OCR
- 导出 `ocr.json`
- 导出 `full_text.txt`
- 导出每页 Markdown
- 导出每页叠框图
- 生成简单错误分析页
- 保留可复用的 `uploads / outputs / json` 任务骨架

## 1. Ubuntu 安装依赖

### 1) 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-eng
```

如果需要中文 OCR，可以额外安装：

```bash
sudo apt install -y tesseract-ocr-chi-sim
```

### 2) 创建虚拟环境并安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 2. 启动服务

```bash
uvicorn app:app --reload
```

启动后访问：

```text
http://127.0.0.1:8000
```



## 发现问题
我的这个ocr - demo我运行完了后，发现有些pdf页面会出现以下问题：
1. 当页面的图和表格多的话，它的置信度低的词语会变多。
2. 有的特殊标点符号无法识别出来。
3. 页面的页数有时候正常bboxing，有时候会出现问题。 
4. 图片上方或下方的字母有时候无法正常bboxing。
5. 对于那些简笔的图形它会识别错成圆圈。
6. 对于45度的倾斜角度的单词无法正常识别

## OCR 增强选项

当前 demo 默认开启了几个针对上述问题的增强：

- `DPI=300`：提高 PDF 渲染分辨率，改善小字、特殊标点和页码 bbox。
- `preprocess_mode=clean`：OCR 前做灰度、自动对比度和锐化，页图输出仍保留原图尺寸。
- `ocr_padding=24`：OCR 前给页面四周补白，减少页码和靠边文字被 Tesseract 漏检。
- `enable_sparse_fallback=true`：额外使用 `psm 11` 稀疏文本补扫，补图表周围的小字、页眉页脚和页码。
- `enable_rotated_text=false`：默认关闭；页面确实有 `45°` 倾斜文字时可开启，程序会做 `+45/-45` 度补扫，并把旋转结果的 bbox 映射回原图。
- `suppress_graphic_artifacts=true`：过滤孤立圆形、表格线等疑似图形误识别，过滤结果保留在每页的 `rejected_words`。

如果某些真实的单个 `O/0` 被误过滤，可以关闭 `图形误识别过滤`；如果要处理倾斜词，再在页面表单里开启 `45° 倾斜补扫`。



# Layout Reader
新增主题：标题、段落、列表、页眉页脚、阅读顺序。
继承：1。
最小交付：把一份多段落 PDF 转成带层级的 Markdown。
过关标准：标题层级和正文顺序基本正确，不把页眉页脚混进正文。
