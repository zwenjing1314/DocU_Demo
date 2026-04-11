# OCR Inspector（Ubuntu 版）

这是一个最小可运行的 Document AI Demo：

- 上传一份 PDF
- 将 PDF 渲染成页图
- 调用 Tesseract OCR
- 输出 `ocr.json`
- 导出每页叠框图（word / line bbox）
- 导出纯文本 `full_text.txt`

## 1. Ubuntu 安装依赖

### 1) 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-eng
```

如果你需要中文 OCR，可以额外安装：

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

## 3. 使用说明

1. 打开浏览器。
2. 选择一个 PDF 文件。
3. 设置 OCR 语言，例如：
   - `eng`
   - `chi_sim`
   - `eng+chi_sim`
4. 点击“开始 OCR”。
5. 完成后下载：
   - `ocr.json`
   - `full_text.txt`
   - 每页叠框图

## 4. 返回结果说明

### `ocr.json` 的结构

```json
{
  "source_file": "original.pdf",
  "created_at": "2026-04-07T00:00:00+00:00",
  "config": {
    "dpi": 200,
    "lang": "eng",
    "tesseract_config": "--oem 3 --psm 3",
    "tesseract_cmd": "tesseract"
  },
  "page_count": 1,
  "pages": [
    {
      "page_num": 1,
      "image_width": 1654,
      "image_height": 2339,
      "words": [],
      "lines": [],
      "text": "..."
    }
  ]
}
```

### words 字段示例

```json
{
  "page_num": 1,
  "text": "Invoice",
  "confidence": 96.12,
  "bbox": {
    "left": 120,
    "top": 88,
    "width": 140,
    "height": 35,
    "right": 260,
    "bottom": 123
  },
  "block_num": 1,
  "par_num": 1,
  "line_num": 1,
  "word_num": 1
}
```

### lines 字段示例

```json
{
  "page_num": 1,
  "text": "Invoice Number 12345",
  "confidence": 93.4,
  "bbox": {
    "left": 118,
    "top": 86,
    "width": 430,
    "height": 38,
    "right": 548,
    "bottom": 124
  },
  "block_num": 1,
  "par_num": 1,
  "line_num": 1,
  "words": ["Invoice", "Number", "12345"]
}
```

## 5. 目录结构

```text
ocr_inspector_ubuntu/
  app.py
  ocr_engine.py
  requirements.txt
  README.md
  web/
    index.html
  uploads/
  outputs/
```

## 6. 可以如何继续扩展

- 在页面上增加“只看低置信度词”的过滤器
- 支持图片上传（不仅是 PDF）
- 增加按页导出 Markdown
- 加一个简单的错误分析页
- 为后续 Demo 复用这套 `uploads / outputs / json` 骨架
