

## Tesseract OCR 引擎的配置参数字符串

DEFAULT_TESSERACT_CONFIG = "--oem 3 --psm 3"
1. --oem 3 (OCR Engine Mode)
作用：指定 OCR 引擎模式
值 3 的含义：使用默认引擎模式，即同时启用 LSTM 神经网络引擎和传统 Tesseract 引擎的组合模式
其他可选值：
0：仅使用传统 Tesseract 引擎（legacy）
1：仅使用 LSTM 神经网络引擎
2：LSTM + legacy 组合（与 3 类似，但更严格）
3：默认模式（自动选择最佳引擎）
2. --psm 3 (Page Segmentation Mode)
作用：指定页面分割模式，告诉 Tesseract 如何分析和分割输入图像
值 3 的含义：完全自动页面分割，假设是一个普通的文档页面，自动检测文本区域、段落、行和词
适用场景：大多数标准文档（如 PDF、扫描文件），这也是为什么注释中说"适合大多数整页文档"
其他常用值：
1：自动页面分割带方向检测
4：假设是单列可变大小文本
6：假设是统一文本块
7：假设是单行文本
8：假设是单个词
13：原始行 + 自定义分隔符


## pymupdf.open() 
### PyMuPDF 是什么？
PyMuPDF是一个高性能的 PDF 文档处理库，它是 MuPDF 渲染引擎的 Python 绑定。
核心特性：
- 速度快：底层使用 C 语言实现的 MuPDF 引擎 
- 功能强大：支持 PDF 渲染、文本提取、图像处理、注释操作等 
- 轻量级：相比其他 PDF 库（如 pdfplumber、PyPDF2），性能更优 
- 精确渲染：能将 PDF 页面高保真地转换为图片

### open() 函数的详细工作原理
```python
doc = pymupdf.open(pdf_path)
```

数据流向和操作原理：
1. 输入阶段
参数：pdf_path 是一个 Path 对象，指向 PDF 文件路径
操作：打开并解析 PDF 文件
2. 内部处理过程
当调用 pymupdf.open() 时，发生以下操作：
```text
磁盘上的 PDF 文件 
    ↓
读取文件二进制数据
    ↓
解析 PDF 结构（页面对象、字体、图像资源等）
    ↓
创建 Document 对象（内存中的文档表示）
    ↓
返回 doc 对象（可用于访问每一页）
```
具体步骤：
文件加载：从磁盘读取 PDF 文件的字节流
结构解析：分析 PDF 的内部结构树，包括：
    页面数量
    每页的尺寸（MediaBox）
    页面内容流（文本、图形指令）
    嵌入的字体和资源
对象构建：在内存中创建一个 Document 对象，包含所有页面的元数据和引用

3. 返回值
类型：Document 对象（类似文件句柄）
特性：
    可迭代：可以用 for page in doc: 遍历所有页面
    索引访问：可以用 doc[page_number] 访问特定页
    提供方法：如 doc.close() 释放资源

## PDF 的本质：矢量格式
PDF（Portable Document Format）本质上是一个矢量图形格式，它存储的不是像素点阵，而是绘图指令。下面是详细说明：
PDF 的默认坐标系：Points（点）
1 point = 1/72 inch（英寸）

DPI（Dots Per Inch）：每英寸多少个像素, 常见 DPI：72、150、200、300, DPI 越高，图片越清晰

DPI：200 pixels/inch
1 inch = 72 points = 200 pixels


### PDF 内部结构示例
一个 PDF 页面的内容流（Content Stream）可能包含这样的指令：
```text
BT                          % Begin Text（开始文本）
/F1 12 Tf                   % 使用字体 F1，字号 12
100 700 Td                  % 移动到坐标 (100, 700)
(Hello World) Tj            % 绘制文本 "Hello World"
ET                          % End Text（结束文本）

100 650 m                   % Move to (100, 650) - 移动画笔
200 650 l                   % Line to (200, 650) - 画线
S                           % Stroke - 描边路径

q                           % Save graphics state
1 0 0 rg                    % Set fill color to red
50 50 100 50 re             % Rectangle at (50,50) with 100x50
f                           % Fill path
Q                           % Restore graphics state
```
关键理解：
    没有像素信息：PDF 不存储"哪个位置是什么颜色的点"
    只有绘图命令：存储的是"在某个坐标画什么形状/文字"
    坐标系统：使用浮点数坐标，可以无限精确

### 矢量 vs 位图对比
特性                 矢量（PDF）                      位图（PNG/JPG）
存储内容        数学公式、路径、指令                    像素颜色数组
缩放               无损，任意放大                      会失真、出现锯齿
文件大小        通常较小（与复杂度相关）                与分辨率和尺寸成正比
渲染               需要实时计算绘制                      直接显示像素
示例         "在 (10,10) 画一个半径 5 的圆"      "第 10 行第 10 列的像素是红色"

### 为什么需要 DPI 转换？
PDF 的默认坐标系：Points（点）


PDF 使用的是 PostScript Point 单位：
- 1 point = 1/72 inch（英寸） 
- 这是印刷行业的标准单位 
- PDF 页面大小通常表示为：MediaBox [0 0 595 842]（A4 纸，单位是 points）

举例：
```text
A4 纸张尺寸：
- 物理尺寸：210mm × 297mm = 8.27" × 11.69"
- PDF 坐标：595 points × 842 points
- 验证：595 ÷ 72 ≈ 8.27", 842 ÷ 72 ≈ 11.69" ✓
```

目标输出：Pixels（像素）
    当我们把 PDF 转换成图片时：
    图片使用 像素（pixels） 作为单位
    需要指定 DPI（Dots Per Inch）：每英寸多少个像素
    常见 DPI：72、150、200、300

实际例子：
```text
PDF 中的一个文本框：
- 左上角坐标：(100 points, 200 points)
- 尺寸：50 points × 10 points

渲染到 200 DPI 图片后：
- 左上角坐标：(100×2.778, 200×2.778) = (278 pixels, 556 pixels)
- 尺寸：(50×2.778, 10×2.778) = (139 pixels, 28 pixels)
```

### 完整的渲染流程
让我展示从 PDF 矢量到图片位图的完整数据流向：
```text
┌─────────────────────────────────────────────────┐
│ 1. PDF 文件（矢量格式）                           │
│    Content Stream:                                │
│    - "在 (100, 200) 绘制文本 'Hello'"             │
│    - "在 (50, 50) 画一个矩形"                     │
└──────────────────┬──────────────────────────────┘
                   │ pymupdf.open()
                   ↓
┌─────────────────────────────────────────────────┐
│ 2. Document 对象（内存中的文档结构）               │
│    - page_count: 10                              │
│    - pages: [Page0, Page1, ...]                  │
└──────────────────┬──────────────────────────────┘
                   │ for page in doc:
                   ↓
┌─────────────────────────────────────────────────┐
│ 3. Page 对象（单个页面）                          │
│    - MediaBox: [0, 0, 595, 842] (A4)            │
│    - Contents: 绘图指令流                         │
└──────────────────┬──────────────────────────────┘
                   │ page.get_pixmap(matrix=Matrix(2.778, 2.778))
                   ↓
┌─────────────────────────────────────────────────┐
│ 4. 栅格化（Rasterization）过程                    │
│                                                   │
│    a. 解析绘图指令                                │
│       → "绘制文本 'Hello' at (100, 200)"         │
│                                                   │
│    b. 应用坐标变换                                │
│       → (100, 200) × 2.778 = (278, 556) pixels  │
│                                                   │
│    c. 字体渲染                                    │
│       → 根据字体轮廓生成字符形状                   │
│       → 在变换后的坐标处绘制                       │
│                                                   │
│    d. 采样（Sampling）                            │
│       → 对每个像素计算颜色值                       │
│       → 抗锯齿处理（Anti-aliasing）               │
│                                                   │
│    e. 生成像素数组                                │
│       → RGB 值矩阵:                               │
│         [[255,255,255], [0,0,0], ...]            │
└──────────────────┬──────────────────────────────┘
                   │ pix.save("page_001.png")
                   ↓
┌─────────────────────────────────────────────────┐
│ 5. PNG 图片（位图格式）                           │
│    - 尺寸：1654 × 2339 pixels (200 DPI A4)      │
│    - 格式：RGB 三通道像素数组                     │
│    - 可被 Tesseract OCR 处理                     │
└─────────────────────────────────────────────────┘
```

## get_pixmap() 的深度解析
### 一、什么是 Pixmap？
Pixmap（Pixel Map） 是一个像素数组对象，代表渲染后的位图数据。
数据结构：
```text
Pixmap 对象包含：
├── width: 图片宽度（像素）
├── height: 图片高度（像素）
├── stride: 每行的字节数
├── colorspace: 颜色空间（RGB、RGBA、GRAY等）
├── n: 通道数（RGB=3, RGBA=4, GRAY=1）
├── alpha: 是否包含透明通道
└── samples: 像素数据数组（一维字节数组）
```

内存布局示例（RGB 图片，2×2 像素）：
```text
samples 数组：
[R0, G0, B0, R1, G1, B1, R2, G2, B2, R3, G3, B3]
 ↑第一个像素↑  ↑第二个像素↑  ↑第三个像素↑  ↑第四个像素↑

实际排列：
像素(0,0)  像素(1,0)
像素(0,1)  像素(1,1)
```

### 二、get_pixmap() 的完整工作流程
#### 对于矢量页面
阶段 1：页面内容解析
```text
输入：Page 对象
  ↓
读取页面的 Content Stream（内容流）
  ↓
解析 PDF 操作符（PDF Operators）：
  - BT/ET: 文本开始/结束
  - Tf: 设置字体
  - Tj/TJ: 显示文本
  - m/l/c: 路径绘制（move/line/curve）
  - rg/RGB: 设置颜色
  - re: 矩形
  - S/f: 描边/填充
  ...
  ↓
构建内部显示列表（Display List）
```

具体过程：
```text
# PDF 内容流示例：
BT
/F1 12 Tf
100 700 Td
(Hello) Tj
ET

# 被解析为内部指令：
DisplayList = [
    SetFont("F1", 12),
    MoveTextPosition(100, 700),
    ShowText("Hello"),
]
```

阶段 2：应用坐标变换矩阵
```text
matrix=matrix  # 传入的 Matrix(zoom, zoom)
```

变换链（Transformation Chain）：
PDF 渲染时存在多层坐标变换：

```text
1. 用户空间变换（User Space Transformation）
   ├─ MediaBox: 页面物理尺寸
   ├─ CropBox: 可见区域
   └─ 旋转（Rotation）

2. 当前变换矩阵（CTM - Current Transformation Matrix）
   ├─ 由 PDF 内容流中的 q/Q/cm 操作符修改
   └─ 控制图形元素的缩放、旋转、平移

3. 设备变换矩阵（Device Transformation Matrix）
   └─ 我们传入的 matrix 参数就在这里生效
      → 将 PDF 坐标映射到像素坐标
```

数学运算过程：
```text
# 假设 PDF 中有一个点 (100, 200)
pdf_point = (100, 200)

# 我们的变换矩阵
zoom = 200 / 72.0 ≈ 2.778
matrix = [2.778, 0, 0, 2.778, 0, 0]

# 应用变换（仿射变换公式）
pixel_x = pdf_x × matrix[0] + pdf_y × matrix[1] + matrix[4]
        = 100 × 2.778 + 200 × 0 + 0
        = 277.8

pixel_y = pdf_x × matrix[2] + pdf_y × matrix[3] + matrix[5]
        = 100 × 0 + 200 × 2.778 + 0
        = 555.6

# 最终像素坐标
pixel_point = (278, 556)  # 取整
```

阶段 3：栅格化（Rasterization）
这是最核心的计算密集型步骤：
3.1 字体渲染
```text
文本 "Hello" at (278, 556)
  ↓
查找字体 F1 的字形数据（Glyph Data）
  ├─ TrueType 字体：读取轮廓曲线（贝塞尔曲线）
  ├─ Type1 字体：解析 PostScript 路径
  └─ CID 字体：处理中文/日文等多字节字符
  ↓
将字形轮廓应用到目标位置
  ↓
Hinting（字体提示）：优化小字号时的显示效果
  ↓
生成字形的矢量路径
```
字形轮廓示例（字母 "O"）：
```text
外圈：M 100,200 
      C 150,150 250,150 300,200  (贝塞尔曲线)
      C 350,250 350,350 300,400
      C 250,450 150,450 100,400
      C 50,350 50,250 100,200
内圈：M 150,250  (空心部分)
      C ... 
      
填充规则：非零环绕规则（Non-zero Winding Rule）
```
3.2 图形元素渲染
```text
对于每个图形元素：
  ├─ 矩形、线条、曲线
  ├─ 图像（如果 PDF 嵌入了图片）
  └─ 渐变、透明度等效果
  ↓
计算元素覆盖的像素区域
  ↓
应用抗锯齿（Anti-aliasing）
  └─ 对于边缘像素，计算覆盖率
     → 覆盖率 30% → 颜色 = 前景色×0.3 + 背景色×0.7
```

抗锯齿原理：
```text
理想矢量边缘：  |█████████|
                |░░░░░░░░░|

像素网格：      [███][??][░░]
                 ↑    ↑    ↑
               100%  40%   0% 覆盖率

渲染结果：      [RGB(0,0,0)] [RGB(102,102,102)] [RGB(255,255,255)]
                纯黑          40%灰度            纯白
```

3.3 采样与像素填充
```text
遍历输出图片的每个像素：
  for y in range(height):      # 垂直方向
    for x in range(width):     # 水平方向
      ↓
      反向映射到 PDF 坐标：
        pdf_x = x / zoom
        pdf_y = y / zoom
      ↓
      查询该位置的覆盖情况：
        - 哪些图形元素覆盖了这个点？
        - 颜色是什么？
        - 透明度如何？
      ↓
      混合计算（Blending）：
        final_color = blend(all_overlapping_elements)
      ↓
      写入 samples 数组：
        index = (y × width + x) × channels
        samples[index]     = R
        samples[index + 1] = G
        samples[index + 2] = B
```

阶段 4：Alpha 通道处理
```python
alpha=False  # 不包含透明通道
```
为什么 OCR 不需要 Alpha？
    Tesseract 只需要颜色信息来识别文字
    透明通道会增加 33% 的内存和处理时间
    PDF 页面通常是不透明的白色背景

### 三、完整的逐层数据变化
让我用实际数据展示整个过程：
```text
# 输入：A4 尺寸的 PDF 页面
page.MediaBox = [0, 0, 595, 842]  # points

# 第 1 步：计算缩放
zoom = 200 / 72.0 = 2.7778
matrix = Matrix(2.7778, 2.7778)

# 第 2 步：调用 get_pixmap
pix = page.get_pixmap(matrix=matrix, alpha=False)

# 内部处理：
# ┌─────────────────────────────────────────────┐
# │ 1. 解析页面内容                              │
# │    - 找到 50 个文本块                        │
# │    - 找到 10 个图形元素                      │
# │    - 找到 2 个嵌入图像                       │
# │                                             │
# │ 2. 应用坐标变换                              │
# │    - 所有坐标 × 2.7778                       │
# │    - 页面尺寸：595×842 → 1654×2339 pixels   │
# │                                             │
# │ 3. 栅格化                                    │
# │    - 创建 1654×2339×3 的像素数组             │
# │    - 逐个像素计算颜色值                       │
# │    - 应用抗锯齿                               │
# │                                             │
# │ 4. 生成 Pixmap 对象                          │
# └─────────────────────────────────────────────┘

# 输出：Pixmap 对象
pix.width = 1654       # 像素宽度
pix.height = 2339      # 像素高度
pix.stride = 4962      # 每行字节数 (1654 × 3)
pix.n = 3              # 3 个通道 (RGB)
pix.alpha = False      # 无透明通道
pix.colorspace = "RGB"

# 像素数据（简化示例）
pix.samples[:12] = [
    255, 255, 255,  # 像素(0,0): 白色
    255, 255, 255,  # 像素(1,0): 白色
    0, 0, 0,        # 像素(2,0): 黑色（文字的某个点）
    255, 255, 255,  # 像素(3,0): 白色
]
```

常见参数组合
```python
# 标准 OCR 场景（项目当前配置）
pix = page.get_pixmap(matrix=Matrix(200/72, 200/72), alpha=False)

# 高质量存档
pix = page.get_pixmap(matrix=Matrix(300/72, 300/72), alpha=False)

# 快速预览
pix = page.get_pixmap(matrix=Matrix(72/72, 72/72), alpha=False)

# 保留透明度（用于叠加处理）
pix = page.get_pixmap(matrix=Matrix(200/72, 200/72), alpha=True)
```

#### 对于扫描文档
阶段 1：页面内容分析
```text
输入：Page 对象（扫描版）
  ↓
读取 Content Stream
  ↓
解析指令：
  - q (save graphics state)
  - cm (concatenate matrix) - 坐标变换
  - Do (draw XObject) - 绘制图像
  - Q (restore graphics state)
  ↓
发现：这是一个图像绘制指令，不是文本指令
  ↓
提取图像引用：/Im1
```

阶段 2：图像资源解码
```text
查找 /Im1 的定义
  ↓
读取图像元数据：
  ├─ Width: 2480 pixels
  ├─ Height: 3508 pixels
  ├─ ColorSpace: DeviceRGB
  ├─ BitsPerComponent: 8
  └─ Filter: /DCTDecode (JPEG)
  ↓
应用解码过滤器：
  JPEG 解码 → RGB 像素数组
  ↓
得到原始位图数据：
  samples[2480 × 3508 × 3] bytes
```
解码过程详解：
```text
# 伪代码展示内部流程
def decode_image_resource(image_dict):
    """解码 PDF 中的图像资源"""
    
    # 1. 读取压缩的图像数据
    compressed_data = image_dict["stream"]
    
    # 2. 根据 Filter 选择解码器
    filter_type = image_dict["Filter"]
    
    if filter_type == "/DCTDecode":
        # JPEG 解码
        raw_pixels = jpeg_decode(compressed_data)
    
    elif filter_type == "/FlateDecode":
        # ZIP/Deflate 解码
        raw_pixels = zlib_decompress(compressed_data)
    
    elif filter_type == "/CCITTFaxDecode":
        # 传真压缩（黑白扫描件常用）
        raw_pixels = ccitt_decode(compressed_data)
    
    elif filter_type == "/JPXDecode":
        # JPEG 2000 解码
        raw_pixels = jpx_decode(compressed_data)
    
    # 3. 应用颜色空间转换
    colorspace = image_dict["ColorSpace"]
    if colorspace == "/DeviceCMYK":
        raw_pixels = cmyk_to_rgb(raw_pixels)
    
    # 4. 返回像素数组
    return raw_pixels
```

阶段 3：应用坐标变换矩阵
这是最关键的一步，即使是扫描件也需要变换！
````text
# PDF 中的指令
q
595 0 0 842 0 0 cm   # 这个矩阵的作用是什么？
/Im1 Do
Q

# 矩阵解析
matrix = [595, 0, 0, 842, 0, 0]

# 含义：
# 将图像缩放到页面的 MediaBox 尺寸
# 原图可能是 2480×3508 像素
# 但要填充到 595×842 points 的页面

# 我们的 zoom 矩阵
zoom = 200 / 72.0 = 2.778
user_matrix = [2.778, 0, 0, 2.778, 0, 0]

# 最终变换矩阵 = PDF 内置矩阵 × 用户矩阵
final_matrix = matrix × user_matrix
             = [595×2.778, 0, 0, 842×2.778, 0, 0]
             = [1653, 0, 0, 2339, 0, 0]
````

变换的效果：
```text
原始嵌入图片：2480 × 3508 pixels
  ↓
PDF 页面尺寸：595 × 842 points
  ↓
应用 zoom=2.778：
  595 × 2.778 = 1653 pixels
  842 × 2.778 = 2339 pixels
  ↓
输出 Pixmap：1653 × 2339 pixels
```

为什么要重新采样？
```text
情况 1：嵌入的图片分辨率 > 目标 DPI
  原图：2480 × 3508 (300 DPI)
  目标：1653 × 2339 (200 DPI)
  操作：下采样（Downsample），缩小图片
  好处：减少内存，加快 OCR 速度

情况 2：嵌入的图片分辨率 < 目标 DPI
  原图：827 × 1169 (100 DPI)
  目标：1653 × 2339 (200 DPI)
  操作：上采样（Upsample），放大图片
  注意：不会增加真实细节，只是插值

情况 3：嵌入的图片分辨率 ≈ 目标 DPI
  原图：1650 × 2336 (200 DPI)
  目标：1653 × 2339 (200 DPI)
  操作：轻微调整或直接使用
```

阶段 4：图像重采样（Resampling）
```text
# MuPDF 内部的重采样算法
def resample_image(source_pixels, src_width, src_height, 
                   dst_width, dst_height):
    """将源图像重采样到目标尺寸"""
    
    # 计算缩放比例
    scale_x = dst_width / src_width
    scale_y = dst_height / src_height
    
    # 创建目标像素数组
    dest_pixels = allocate(dst_width * dst_height * 3)
    
    # 双线性插值（Bilinear Interpolation）
    for y in range(dst_height):
        for x in range(dst_width):
            # 反向映射到源坐标
            src_x = x / scale_x
            src_y = y / scale_y
            
            # 获取周围的 4 个像素
            x0 = floor(src_x)
            y0 = floor(src_y)
            x1 = x0 + 1
            y1 = y0 + 1
            
            # 计算权重
            wx = src_x - x0
            wy = src_y - y0
            
            # 双线性插值
            for channel in range(3):  # R, G, B
                p00 = source[(y0 * src_width + x0) * 3 + channel]
                p01 = source[(y0 * src_width + x1) * 3 + channel]
                p10 = source[(y1 * src_width + x0) * 3 + channel]
                p11 = source[(y1 * src_width + x1) * 3 + channel]
                
                value = (p00 * (1-wx) * (1-wy) +
                        p01 * wx * (1-wy) +
                        p10 * (1-wx) * wy +
                        p11 * wx * wy)
                
                dest[(y * dst_width + x) * 3 + channel] = int(value)
    
    return dest_pixels
```

插值效果对比：
```text
最近邻插值（快，但质量差）：
  源像素: [A, B]
  放大2倍: [A, A, B, B]  ← 有锯齿

双线性插值（平衡）：
  源像素: [A, B]
  放大2倍: [A, mix(A,B), mix(A,B), B]  ← 平滑

双三次插值（慢，但质量好）：
  考虑 16 个邻近像素
  更平滑，保留更多细节
```

## 遇到的问题
### Word 文档是矢量的吗？
Word 的本质：混合格式

1. Word (.docx) 的内部结构
.docx 实际上是一个 ZIP 压缩包，包含多个 XML 文件和资源：
```text
document.docx (重命名为 .zip 后可解压)
├── [Content_Types].xml      # 文件类型定义
├── _rels/
│   └── .rels                # 关系映射
├── word/
│   ├── document.xml         # 主文档内容（XML格式）
│   ├── styles.xml           # 样式定义
│   ├── settings.xml         # 文档设置
│   ├── fontTable.xml        # 字体表
│   └── media/               # 嵌入的媒体文件
│       ├── image1.png       # 位图图片
│       ├── image2.jpg
│       └── ...
└── docProps/
    ├── core.xml             # 元数据
    └── app.xml
```

2. Word 中的内容类型对比
内容类型             存储方式                  是否矢量
文本          XML 标签 + Unicode 字符      ❌ 非矢量（纯文本）
字体信息        引用字体名称+样式        ⚠️ 半矢量（依赖外部字体文件）
形状/图形   DrawingML（基于 XML 的矢量描述）     ✅ 矢量
SmartArt        DrawingML                    ✅ 矢量
嵌入图片        PNG/JPG/BMP 等位图             ❌ 位图
图表      OOXML Chart + 可选缓存图片        ✅ 矢量（主要）
艺术字         DrawingML 路径                  ✅ 矢量

3. Word 文本的存储方式
```xml
<!-- word/document.xml 中的文本示例 -->
<w:p>  <!-- 段落 -->
  <w:r>  <!-- 运行（Run，具有相同格式的文本片段） -->
    <w:rPr>
      <w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>
      <w:sz w:val="24"/>  <!-- 字号 12pt (24 半点) -->
      <w:color w:val="FF0000"/>  <!-- 红色 -->
    </w:rPr>
    <w:t>Hello World</w:t>  <!-- 实际文本内容 -->
  </w:r>
</w:p>
```
关键理解：
    Word 存储的是 "什么文字 + 什么格式"，而不是绘图指令
    没有坐标信息（除了绝对定位的文本框）
    布局是流式的（Flow Layout），由渲染引擎动态计算
    不是矢量图形，而是结构化文本 + 样式

### Word 转 PDF 的过程
转换原理：从流式布局到固定布局
```text
Word 文档 (.docx)
  ↓
【Word 渲染引擎 / 转换工具】
  ├─ 1. 解析 XML 结构
  ├─ 2. 应用样式和格式
  ├─ 3. 计算页面布局（分页、换行）
  ├─ 4. 生成绘图指令
  └─ 5. 嵌入字体和资源
  ↓
PDF 文档 (.pdf)
```
详细转换流程
阶段 1：文档结构解析
```text
# Word XML 结构
<w:p>
  <w:r>
    <w:t>标题文字</w:t>
  </w:r>
</w:p>

# 被解析为内部对象
Paragraph {
    runs: [
        Run {
            text: "标题文字",
            font: "宋体",
            size: 16pt,
            bold: True
        }
    ]
}
```

阶段 2：布局计算（Layout Engine）
这是最关键的一步，Word 的流式布局需要转换为固定坐标：
```text
输入：流式文本
  "这是一段很长的文字，需要自动换行..."
  
布局计算过程：
  ├─ 页面宽度：595 points (A4)
  ├─ 边距：左右各 72 points
  ├─ 可用宽度：595 - 72 - 72 = 451 points
  ├─ 字体：宋体 12pt
  ├─ 计算每个字符宽度
  ├─ 确定换行位置
  └─ 分配坐标
  
输出：带坐标的文本块
  Line 1: "这是一段很长的文字，" at (72, 720)
  Line 2: "需要自动换行..." at (72, 705)
```
布局计算的复杂性：
    文本换行：根据宽度和断词规则
    分页：避免在表格行、段落中间断开
    对象定位：图片、文本框的绝对/相对位置
    表格布局：单元格大小自适应
    目录生成：页码计算

阶段 3：生成 PDF 绘图指令
```text
# 转换后的 PDF Content Stream
BT
/F1 12 Tf                    # 设置字体
72 720 Td                    # 移动到坐标 (72, 720)
(This is a long text,) Tj    # 绘制文本
ET

BT
/F1 12 Tf
72 705 Td                    # 下一行
(needs automatic wrapping...) Tj
ET

# 如果有图片
q
200 0 0 150 100 400 cm       # 坐标变换矩阵
/Im1 Do                      # 绘制图像 Im1
Q
```

阶段 4：字体嵌入
```text
PDF 需要确保在任何设备上显示一致，所以：

方案 1：完全嵌入字体
  ├─ 将 .ttf/.otf 文件的子集嵌入 PDF
  ├─ 只包含文档中使用的字符
  └─ 文件体积增加，但保证一致性

方案 2：引用系统字体
  ├─ 只记录字体名称
  ├─ 文件体积小
  └─ 目标设备必须有该字体

方案 3：字体轮廓化（转曲）
  ├─ 将文字转换为矢量路径
  ├─ 不再依赖字体文件
  └─ 无法再编辑文本，文件体积大
```

### 竖排文字的识别机制
竖排文字的两种实现方式
方式 1：旋转坐标系（最常见）
```text
# PDF Content Stream 中的竖排文字
q                          # 保存图形状态
0 -1 1 0 300 500 cm       # 旋转 90° 的变换矩阵
BT
/F1 12 Tf
0 0 Td
(竖排文字) Tj              # 正常绘制，但坐标系已旋转
ET
Q                          # 恢复图形状态
```
矩阵解析：
```text
[0  -1  0]
[1   0  0]    ← 逆时针旋转 90° 的旋转矩阵
[300 500 1]   ← 平移到 (300, 500)

效果：
- X 轴变成向下
- Y 轴变成向右
- 文字看起来就是竖排的了
```

数据流向：
```text
1. PDF 解析器读取到 cm (concatenate matrix) 操作符
   ↓
2. 更新当前变换矩阵 (CTM)
   CTM_new = CTM_old × [0 -1 1 0 300 500]
   ↓
3. 后续的文本绘制都应用这个变换
   ↓
4. 文字 "竖排文字" 的每个字符：
   - 逻辑上还是从左到右
   - 但坐标系旋转了 90°
   - 视觉上变成从上到下
   ↓
5. PyMuPDF 渲染时：
   - 应用完整的 CTM 变换
   - 正确计算出每个像素的位置
   - 生成的图片中文字就是竖排的
```

方式 2：逐字符定位（传统中文排版）
```text
# 每个字符单独定位
BT
/F1 12 Tf
300 700 Td
(第) Tj
0 -15 Td                   # 向下移动 15 points
一 Tj
0 -15 Td
(个) Tj
0 -15 Td
(字) Tj
ET
```
特点：
    没有旋转坐标系
    每个字符手动指定位置
    更灵活，可以控制字符间距
    文件体积更大

### Tesseract 如何识别竖排文字？
关键点：Tesseract 看到的是图片，不是 PDF 指令
```text
PDF Content Stream（有旋转矩阵信息）
  ↓
PyMuPDF 渲染（应用旋转矩阵）
  ↓
PNG 图片（竖排文字的像素阵列）
  ↓
Tesseract OCR（分析图片中的文字方向）
```

Tesseract 的识别过程：

1. 页面分割模式（PSM）的作用
```text
# 项目配置
DEFAULT_TESSERACT_CONFIG = "--oem 3 --psm 3"

# PSM 3: 自动页面分割
# Tesseract 会自动检测：
# - 文字方向（0°, 90°, 180°, 270°）
# - 文本区域
# - 阅读顺序
```

2. 方向检测（OSD - Orientation and Script Detection）
```text
输入：竖排文字的图片
  ↓
Tesseract 的分析步骤：
  
  a. 特征提取
     ├─ 检测文字笔画的方向性
     ├─ 分析连通分量（Connected Components）
     └─ 计算字符的长宽比分布
  
  b. 方向判断
     ├─ 如果字符高度 >> 宽度 → 可能是竖排
     ├─ 如果字符宽度 >> 高度 → 可能是横排
     └─ 使用训练好的分类器判断
  
  c. 脚本检测
     ├─ 识别是中文、日文、英文等
     └─ 不同脚本有不同的方向特征
  
  d. 置信度评估
     └─ 对每个可能的方向打分
```

实际例子：
```text
竖排中文图片的特征：
  - 单个字符：接近正方形或略高
  - 整列文字：高度远大于宽度
  - 笔画分布：垂直方向的连续性更强
  
Tesseract 判断：
  Orientation: 90° clockwise (顺时针 90°)
  Script: Han (中文)
  Confidence: 0.95
```

3. 识别策略
```text
# Tesseract 内部处理流程

# 方案 A：自动旋转后识别（默认）
1. 检测到文字方向为 90°
2. 将图片逆时针旋转 90°
3. 按正常横排文字识别
4. 输出结果保持原始方向标记

# 方案 B：直接竖排识别（需要特殊配置）
--psm 5  # 假设是垂直对齐的文本块
# Tesseract 会调整识别模型，考虑竖排特征
```

### 为什么 Tesseract 能正确识别？
原因 1：训练数据包含多方向样本
```text
Tesseract 的训练数据集：
  ├─ 横排文字（主要）
  ├─ 竖排文字（中文、日文古籍）
  ├─ 旋转文字（各种角度）
  └─ 混合排版
  
LSTM 神经网络学习到了：
  - 不同方向的字符形态特征
  - 上下文语言模型
  - 版面布局模式
```

原因 2：字符级别的鲁棒性
```text
对于中文字符 "中"：
  - 横排时：宽度 ≈ 高度
  - 竖排时：宽度 ≈ 高度（基本不变）
  
对于英文单词 "Hello"：
  - 横排时：宽度 >> 高度
  - 竖排时（旋转 90°）：高度 >> 宽度
  
Tesseract 的特征提取器能捕捉这些模式
```

原因 3：语言模型的辅助
```text
识别过程中：
  候选 1: "竖排文字" (符合中文语法) ✓
  候选 2: "土非卄寸" (单个字符可能对，但组合无意义) ✗
  
语言模型会选择候选 1
```

完整的数据流对比
横排文字 vs 竖排文字

```text
┌─────────────────────────────────────────────────────┐
│ 场景 1：横排文字                                      │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Word: <w:t>Hello</w:t>                             │
│    ↓                                                 │
│  PDF: BT /F1 12 Tf 100 700 Td (Hello) Tj ET        │
│    ↓ (无旋转，CTM = 单位矩阵)                         │
│  渲染: 像素坐标 (100, 700) 开始向右绘制               │
│    ↓                                                 │
│  图片: H e l l o (从左到右排列)                       │
│    ↓                                                 │
│  Tesseract: 检测到横排，直接识别 → "Hello" ✓         │
│                                                      │
├─────────────────────────────────────────────────────┤
│ 场景 2：竖排文字（旋转坐标系）                         │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Word: <w:textDirection w:val="tb"/> (文本框竖排)    │
│    ↓                                                 │
│  PDF: q 0 -1 1 0 300 500 cm BT /F1 12 Tf            │
│       0 0 Td (竖排) Tj ET Q                          │
│    ↓ (CTM 包含 90° 旋转)                              │
│  渲染: 应用旋转矩阵，像素坐标变换                      │
│    ↓                                                 │
│  图片: 竖                                             │
│       排                                             │
│       (从上到下排列)                                   │
│    ↓                                                 │
│  Tesseract: 检测到竖排特征                            │
│    ├─ 方法1: 旋转图片后识别 → "竖排" ✓               │
│    └─ 方法2: 直接用竖排模型识别 → "竖排" ✓           │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### 为什么ocr不对pdf直接进行处理
一、PDF 的两种类型：关键区别
类型 1：原生数字 PDF（Born-Digital PDF）
```text
来源：Word、LaTeX、InDesign 等直接导出
特点：
  ✅ 包含真实的文本层
  ✅ 有字体信息、字符编码
  ✅ 可以复制粘贴文字
  ✅ 有明确的阅读顺序

示例 Content Stream：
  BT
  /F1 12 Tf
  100 700 Td
  (Hello World) Tj    ← 这是真实的文本数据
  ET
```
对于这种 PDF，确实不需要 OCR！


类型 2：扫描版 PDF（Scanned PDF / Image-only PDF）
```text
来源：扫描仪、相机拍摄、传真
特点：
  ❌ 没有文本层
  ❌ 每一页都是一张图片
  ❌ 无法复制文字
  ❌ 只是把图片包装在 PDF 容器里

示例结构：
  Page 1:
    - 嵌入的 JPEG 图片（整个页面）
    - 没有 BT/Tj/ET 文本指令
  
  Page 2:
    - 嵌入的 PNG 图片
    - 没有文本内容
```
这种 PDF 必须用 OCR！

二、为什么项目选择"转图片 + OCR"的方案？
原因 1：通用性 —— 处理所有类型的 PDF
```python
# 项目的目标：处理任意 PDF
pdf_path = "unknown_source.pdf"

# 如果直接解析 PDF 文本：
try:
    text = extract_text_from_pdf(pdf_path)
except:
    # 如果是扫描版 PDF，会失败或返回空
    pass

# 如果先转图片再 OCR：
images = render_pdf_to_images(pdf_path)  # ✅ 对所有 PDF 都有效
for img in images:
    text = ocr(img)  # ✅ 统一处理
```
优势：
✅ 同时支持原生 PDF 和扫描版 PDF
✅ 不需要预先判断 PDF 类型
✅ 代码逻辑统一，维护简单

原因 2：位置信息的准确性
这是最关键的技术原因！
直接解析 PDF 文本的问题
```python
# 从 PDF Content Stream 提取的文本
text_objects = [
    {"text": "发票号码", "x": 100, "y": 700, "font": "F1"},
    {"text": ":", "x": 148, "y": 700, "font": "F1"},
    {"text": "No.123456", "x": 155, "y": 700, "font": "F2"},
]

# 问题 1：坐标系统是 PDF 的 point 单位
#   - 需要转换为像素坐标才能标注图片
#   - 转换过程可能引入误差

# 问题 2：复杂布局难以处理
#   - 表格中的文字可能是分散的绘图指令
#   - 旋转的文字需要解析变换矩阵
#   - 重叠的文本层（如水印）需要特殊处理

# 问题 3：字体映射问题
#   - PDF 可能使用自定义编码（Custom Encoding）
#   - 字体子集化后，字符码与实际字符不对应
#   - 例如：PDF 中存储的是 <0041>，需要查表才知道是 "A"
```

实际案例：字体编码陷阱
```python
# PDF 中的文本可能是这样的：
<F1> 12 Tf
<0x8140> Tj    # 这不是 Unicode！

# 需要查询字体的 CMap（Character Map）：
CMap = {
    0x8140: "中",
    0x8141: "华",
    # ... 可能有几千个映射
}

# 如果 CMap 缺失或损坏，就无法正确解码
```

OCR 方案的优势
```python
# OCR 直接从图片识别
ocr_result = {
    "text": "发票号码: No.123456",
    "bbox": {"left": 100, "top": 50, "width": 200, "height": 20},
    "confidence": 0.95
}

# 优势：
# ✅ bbox 直接是像素坐标，与图片完美对应
# ✅ 不依赖字体信息、编码表
# ✅ 无论 PDF 多复杂，渲染后的图片就是最终样子
# ✅ 可以检测识别置信度
```

原因 3：验证与可视化需求
看你的项目输出结构：
```text
outputs/xxx/
├── pages/           # 页面图片
├── overlays/        # 叠框图（bbox 可视化）
├── texts/           # 每页文本
└── ocr.json         # 结构化结果
```

叠框图的作用：
```python
def _draw_overlay(image, words, lines, overlay_path):
    """绘制叠框图，用于人工检查 OCR 质量"""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    
    for line in lines:
        box = line["bbox"]
        draw.rectangle([...], outline=(44, 123, 229), width=3)
    
    canvas.save(overlay_path)
```

如果要直接解析 PDF 文本，如何生成叠框图？
```python
# 方案 A：仍然需要渲染图片
image = render_page_to_image(pdf_page)  # 还是要转图片
draw_bboxes_from_pdf_coords(text_objects)  # 然后画框

# 方案 B：纯矢量绘制（复杂且不直观）
#   - 需要在 PDF 上绘制注解
#   - 不同阅读器支持程度不同
#   - 无法快速预览
```

结论：即使直接解析 PDF，为了可视化验证，还是需要转图片！
 
原因 4：错误检测与质量控制
```python
# OCR 可以提供置信度信息
ocr_result = {
    "text": "发栗号码",  # 识别错误
    "confidence": 0.62,  # 低置信度，标记为可疑
    "words": [
        {"text": "发", "conf": 0.95},
        {"text": "栗", "conf": 0.45},  # ← 这个字不确定
        {"text": "号", "conf": 0.88},
        {"text": "码", "conf": 0.91},
    ]
}

# 可以自动标记低置信度的结果，让人工复核
if ocr_result["confidence"] < 0.7:
    flag_for_review(ocr_result)
```
如果直接解析 PDF 文本：
❌ 没有置信度信息
❌ 无法知道是否有乱码
❌ 无法检测字体缺失导致的显示问题

原因 5：处理扫描件是唯一选择
```text
实际应用场景统计：

场景                      | 占比   | 是否需要 OCR
-------------------------|--------|-------------
财务发票扫描              | 40%    | ✅ 必须
合同扫描件                | 25%    | ✅ 必须
历史档案数字化            | 15%    | ✅ 必须
书籍扫描                  | 10%    | ✅ 必须
原生电子文档              | 10%    | ❌ 不需要

总计需要 OCR 的场景       | 90%    | 
```
项目定位：OCR Inspector，名字就说明了一切——它是为 OCR 场景设计的！

