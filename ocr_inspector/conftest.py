"""Pytest 全局配置"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent  # 获取 conftest.py 所在目录（项目根目录）
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))  # 将项目根目录添加到 Python 搜索路径


"""
用户执行命令：
$ pytest tests/test_render_pdf.py -v
       ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 1: pytest 启动并查找配置文件
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       ↓
发现 pytest.ini 和 conftest.py
       ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 2: 执行 conftest.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       ↓
┌─────────────────────────────────────┐
│ import sys                          │
│ from pathlib import Path            │
│                                     │
│ ROOT_DIR = Path(__file__).parent    │
│ # = /path/to/ocr_inspector          │
│                                     │
│ sys.path.insert(0, str(ROOT_DIR))   │
│ # 添加项目根目录到搜索路径            │
└─────────────────────────────────────┘
       ↓
现在可以导入项目模块了：
from ocr_engine import xxx  ✅
       ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 3: 读取 pytest.ini 配置
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       ↓
┌─────────────────────────────────────┐
│ testpaths = tests                   │
│ → 只在 tests/ 目录搜索               │
│                                     │
│ python_files = test_*.py            │
│ → 只识别 test_ 开头的 .py 文件       │
│                                     │
│ python_classes = Test*              │
│ → 只识别 Test 开头的类               │
│                                     │
│ python_functions = test_*           │
│ → 只识别 test_ 开头的函数            │
└─────────────────────────────────────┘
       ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 4: 收集测试用例
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       ↓
扫描 tests/ 目录
       ↓
找到 test_render_pdf.py
       ↓
识别类：TestRenderPdfToImages  ✅
       ↓
识别方法：
  - test_render_single_page_pdf  ✅
  - test_render_multi_page_pdf   ✅
  - test_different_dpi_settings  ✅
  ...
       ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 5: 执行测试
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       ↓
对每个测试方法：
  1. 查找并执行依赖的 fixture
  2. 注入 fixture 返回值
  3. 执行测试逻辑
  4. 验证断言
  5. 清理资源
       ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
步骤 6: 输出结果
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       ↓
tests/test_render_pdf.py::TestRenderPdfToImages::test_render_single_page_pdf PASSED
tests/test_render_pdf.py::TestRenderPdfToImages::test_render_multi_page_pdf PASSED
...
"""

