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


# Layout Reader
新增主题：标题、段落、列表、页眉页脚、阅读顺序。
继承：1。
最小交付：把一份多段落 PDF 转成带层级的 Markdown。
过关标准：标题层级和正文顺序基本正确，不把页眉页脚混进正文。
