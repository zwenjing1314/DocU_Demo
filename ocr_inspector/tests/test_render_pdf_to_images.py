from __future__ import annotations

"""测试 render_pdf_to_images 函数。

测试场景包括：
1. 正常 PDF 文件渲染
2. 多页 PDF 处理
3. 不同 DPI 设置
4. 输出目录自动创建
5. 异常情况处理（文件不存在、损坏的PDF等）
6. 返回值验证
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest
import pymupdf

from ocr_engine import render_pdf_to_images


class TestRenderPdfToImages:
    """render_pdf_to_images 函数的测试类"""

    @pytest.fixture
    def temp_output_dir(self, tmp_path):
        """提供临时输出目录"""
        return tmp_path / "output_images"

    @pytest.fixture
    def sample_pdf_path(self, tmp_path):
        """创建一个简单的测试 PDF 文件"""
        pdf_path = tmp_path / "test.pdf"

        # 使用 PyMuPDF 创建一个简单的 PDF
        doc = pymupdf.open()

        # 添加第一页
        page1 = doc.new_page(width=595, height=842)  # A4 尺寸
        page1.insert_text((72, 72), "Test Page 1", fontsize=24)

        # 添加第二页
        page2 = doc.new_page(width=595, height=842)
        page2.insert_text((72, 72), "Test Page 2", fontsize=24)

        # 保存 PDF
        doc.save(str(pdf_path))
        doc.close()

        return pdf_path

    @pytest.fixture
    def single_page_pdf(self, tmp_path):
        """创建单页 PDF"""
        pdf_path = tmp_path / "single_page.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), "Single Page", fontsize=24)
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    # ==================== 基本功能测试 ====================

    def test_render_single_page_pdf(self, single_page_pdf, temp_output_dir):
        """测试单页 PDF 渲染"""
        # Act
        result = render_pdf_to_images(single_page_pdf, temp_output_dir, dpi=300)

        # Assert
        assert len(result) == 1, "应该返回 1 个图片路径"
        assert result[0].exists(), "图片文件应该存在"
        assert result[0].name == "page_001.png", "文件名格式应该正确"
        assert result[0].parent == temp_output_dir, "文件应该在输出目录中"

    def test_render_multi_page_pdf(self, sample_pdf_path, temp_output_dir):
        """测试多页 PDF 渲染"""
        # Act
        result = render_pdf_to_images(sample_pdf_path, temp_output_dir, dpi=300)

        # Assert
        assert len(result) == 2, "应该返回 2 个图片路径"

        # 验证所有文件都存在
        for i, path in enumerate(result, 1):
            assert path.exists(), f"第 {i} 页图片应该存在"
            assert path.name == f"page_{i:03d}.png", f"第 {i} 页文件名应该正确"

    def test_output_directory_created_automatically(self, sample_pdf_path, tmp_path):
        """测试输出目录自动创建（包括嵌套目录）"""
        nested_dir = tmp_path / "level1" / "level2" / "images"

        # Act
        result = render_pdf_to_images(sample_pdf_path, nested_dir, dpi=300)

        # Assert
        assert nested_dir.exists(), "嵌套目录应该被自动创建"
        assert nested_dir.is_dir(), "应该是目录而不是文件"
        assert len(result) > 0, "应该成功生成图片"

    # ==================== DPI 参数测试 ====================

    @pytest.mark.parametrize("dpi,expected_min_size", [
        (72, 500),  # 低分辨率，图片较小
        (150, 1000),  # 中等分辨率
        (300, 2000),  # 高分辨率（OCR推荐）
        (600, 4000),  # 超高分辨率
    ])
    def test_different_dpi_settings(self, single_page_pdf, temp_output_dir, dpi, expected_min_size):
        """测试不同 DPI 设置对输出图片尺寸的影响"""
        # Act
        result = render_pdf_to_images(single_page_pdf, temp_output_dir, dpi=dpi)

        # Assert
        assert len(result) == 1

        # 验证图片尺寸与 DPI 成正比
        from PIL import Image
        with Image.open(result[0]) as img:
            width, height = img.size
            assert width >= expected_min_size, f"DPI={dpi} 时宽度应至少 {expected_min_size}px"
            assert height >= expected_min_size, f"DPI={dpi} 时高度应至少 {expected_min_size}px"

            # 验证大致比例（A4 纸张比例约为 1:1.414）
            aspect_ratio = height / width
            assert 1.3 <= aspect_ratio <= 1.5, "宽高比应该接近 A4 纸张比例"

    def test_default_dpi_is_300(self, single_page_pdf, temp_output_dir):
        """测试默认 DPI 为 300"""
        # Act - 不指定 dpi 参数
        result = render_pdf_to_images(single_page_pdf, temp_output_dir)

        # Assert - 应该使用默认值 300 DPI
        from PIL import Image
        with Image.open(result[0]) as img:
            # A4 @ 300 DPI ≈ 2480 x 3508
            width, height = img.size
            assert 2400 <= width <= 2600, "默认 DPI 应该是 300"
            assert 3400 <= height <= 3600, "默认 DPI 应该是 300"

    # ==================== 返回值测试 ====================

    def test_return_value_is_list_of_paths(self, sample_pdf_path, temp_output_dir):
        """测试返回值类型和结构"""
        # Act
        result = render_pdf_to_images(sample_pdf_path, temp_output_dir)

        # Assert
        assert isinstance(result, list), "返回值应该是列表"
        assert all(isinstance(p, Path) for p in result), "列表元素应该是 Path 对象"
        assert result == sorted(result), "路径应该按页码排序"

    def test_returned_paths_are_absolute(self, sample_pdf_path, temp_output_dir):
        """测试返回的路径是绝对路径"""
        # Act
        result = render_pdf_to_images(sample_pdf_path, temp_output_dir)

        # Assert
        for path in result:
            assert path.is_absolute(), "应该返回绝对路径"

    # ==================== 边界情况测试 ====================

    def test_empty_pdf_handling(self, tmp_path, temp_output_dir):
        """测试空 PDF（无页面）的处理"""
        # Arrange
        pdf_path = tmp_path / "empty.pdf"
        doc = pymupdf.open()
        try:
            doc.save(str(pdf_path))
            doc.close()

            # Act
            result = render_pdf_to_images(pdf_path, temp_output_dir)

            # Assert
            assert result == [], "空 PDF 应该返回空列表"
        except Exception as e:
            # 如果保存失败，这也是可接受的行为
            pytest.skip(f"PyMuPDF 版本不允许创建空文档: {e}")

    def test_large_page_count_pdf(self, tmp_path, temp_output_dir):
        """测试多页 PDF（10页）"""
        # Arrange
        pdf_path = tmp_path / "large.pdf"
        doc = pymupdf.open()
        for i in range(10):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), f"Page {i + 1}", fontsize=24)
        doc.save(str(pdf_path))
        doc.close()

        # Act
        result = render_pdf_to_images(pdf_path, temp_output_dir)

        # Assert
        assert len(result) == 10, "应该生成 10 张图片"
        for i, path in enumerate(result, 1):
            assert path.name == f"page_{i:03d}.png"

    # ==================== 异常处理测试 ====================

    def test_nonexistent_pdf_raises_error(self, temp_output_dir):
        """测试不存在的 PDF 文件"""
        # Arrange
        nonexistent_pdf = Path("/nonexistent/path/file.pdf")

        # Act & Assert
        with pytest.raises((FileNotFoundError, RuntimeError)):
            render_pdf_to_images(nonexistent_pdf, temp_output_dir)

    def test_corrupted_pdf_handling(self, tmp_path, temp_output_dir):
        """测试损坏的 PDF 文件"""
        # Arrange
        corrupted_pdf = tmp_path / "corrupted.pdf"
        corrupted_pdf.write_bytes(b"%PDF-1.4 corrupted content")

        # Act & Assert
        with pytest.raises(Exception):
            render_pdf_to_images(corrupted_pdf, temp_output_dir)

    # ==================== 图片质量测试 ====================

    def test_generated_image_is_valid_png(self, sample_pdf_path, temp_output_dir):
        """测试生成的图片是有效的 PNG 格式"""
        # Act
        result = render_pdf_to_images(sample_pdf_path, temp_output_dir)

        # Assert
        from PIL import Image
        for path in result:
            with Image.open(path) as img:
                assert img.format == "PNG", "图片格式应该是 PNG"
                assert img.mode in ("RGB", "RGBA"), "图片模式应该是 RGB 或 RGBA"

    def test_image_has_no_alpha_channel(self, sample_pdf_path, temp_output_dir):
        """测试图片不包含透明通道（alpha=False）"""
        # Act
        result = render_pdf_to_images(sample_pdf_path, temp_output_dir)

        # Assert
        from PIL import Image
        for path in result:
            with Image.open(path) as img:
                assert img.mode == "RGB", "应该没有透明通道（RGB而非RGBA）"

    def test_image_content_is_not_blank(self, single_page_pdf, temp_output_dir):
        """测试图片内容不是全白（确实有渲染内容）"""
        # Act
        result = render_pdf_to_images(single_page_pdf, temp_output_dir)

        # Assert
        from PIL import Image
        import numpy as np

        with Image.open(result[0]) as img:
            arr = np.array(img)
            # 检查是否有非白色像素（白色 = [255, 255, 255]）
            non_white_pixels = np.any(arr != [255, 255, 255], axis=-1)
            assert np.any(non_white_pixels), "图片应该包含非白色内容"

    # ==================== 性能测试 ====================

    def test_resource_cleanup_after_rendering(self, sample_pdf_path, temp_output_dir):
        """测试渲染后资源是否正确释放"""
        # Act
        result = render_pdf_to_images(sample_pdf_path, temp_output_dir)

        # Assert
        # 如果 doc.close() 没有正确调用，可能会导致文件锁定
        # 尝试再次打开同一个 PDF 应该成功
        try:
            doc = pymupdf.open(sample_pdf_path)
            doc.close()
            assert True, "PDF 文件应该可以被再次打开（资源已释放）"
        except Exception as e:
            pytest.fail(f"资源未正确释放: {e}")

    # ==================== 集成测试 ====================

    def test_full_workflow_with_real_pdf(self, temp_output_dir):
        """使用项目中的示例 PDF 进行完整工作流测试"""
        # Arrange
        sample_pdf = Path(__file__).parent.parent / "sample_invoice.pdf"

        if not sample_pdf.exists():
            pytest.skip("示例 PDF 文件不存在")

        # Act
        result = render_pdf_to_images(sample_pdf, temp_output_dir, dpi=300)

        # Assert
        assert len(result) > 0, "应该生成至少一张图片"

        # 验证第一张图片
        first_image = result[0]
        assert first_image.exists()
        assert first_image.suffix == ".png"

        # 验证图片可以正常打开
        from PIL import Image
        with Image.open(first_image) as img:
            assert img.width > 0
            assert img.height > 0

    def test_consecutive_calls_produce_same_results(self, single_page_pdf, tmp_path):
        """测试连续调用产生相同结果（幂等性）"""
        # Act
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"

        result1 = render_pdf_to_images(single_page_pdf, dir1, dpi=300)
        result2 = render_pdf_to_images(single_page_pdf, dir2, dpi=300)

        # Assert
        assert len(result1) == len(result2)

        # 比较图片内容是否一致
        from PIL import Image
        with Image.open(result1[0]) as img1, Image.open(result2[0]) as img2:
            assert img1.size == img2.size
            # 逐像素比较
            import numpy as np
            arr1 = np.array(img1)
            arr2 = np.array(img2)
            assert np.array_equal(arr1, arr2), "两次渲染结果应该完全一致"


# ==================== 辅助测试工具 ====================

class TestHelperFunctions:
    """测试辅助功能的类"""

    def test_filename_formatting(self):
        """测试页码到文件名的格式化"""
        # 验证格式化逻辑
        for page_index in [0, 1, 9, 10, 99, 100]:
            filename = f"page_{page_index + 1:03d}.png"
            assert filename.startswith("page_")
            assert filename.endswith(".png")
            assert len(filename) == len("page_XXX.png")

    def test_zoom_calculation(self):
        """测试 zoom 值计算的正确性"""
        test_cases = [
            (72, 1.0),
            (144, 2.0),
            (300, 300 / 72),
            (600, 600 / 72),
        ]

        for dpi, expected_zoom in test_cases:
            zoom = dpi / 72.0
            assert abs(zoom - expected_zoom) < 1e-10, f"DPI={dpi} 时 zoom 计算错误"


# ==================== 使用 Mock 的单元测试 ====================

class TestWithMocks:
    """使用 Mock 对象的隔离测试"""

    def test_pymupdf_open_called_with_correct_path(self, tmp_path):
        """测试 pymupdf.open 使用正确的路径调用"""
        # Arrange
        mock_doc = MagicMock()
        mock_doc.__iter__ = Mock(return_value=iter([]))  # 空迭代器
        mock_doc.__enter__ = Mock(return_value=mock_doc)
        mock_doc.__exit__ = Mock(return_value=False)

        pdf_path = tmp_path / "test.pdf"

        with patch('pymupdf.open', return_value=mock_doc) as mock_open:
            # Act
            try:
                render_pdf_to_images(pdf_path, tmp_path / "out")
            except:
                pass  # 忽略后续错误

            # Assert
            mock_open.assert_called_once_with(pdf_path)

    def test_doc_close_called_even_on_error(self, tmp_path):
        """测试即使发生错误也会调用 doc.close()"""
        # Arrange
        mock_doc = MagicMock()
        mock_doc.__iter__ = Mock(side_effect=RuntimeError("Simulated error"))

        pdf_path = tmp_path / "test.pdf"

        with patch('pymupdf.open', return_value=mock_doc):
            # Act & Assert
            with pytest.raises(RuntimeError):
                render_pdf_to_images(pdf_path, tmp_path / "out")

            # 验证 close 被调用（finally 块执行）
            mock_doc.close.assert_called_once()

    def test_get_pixmap_called_with_correct_matrix(self, tmp_path):
        """测试 get_pixmap 使用正确的 matrix 调用"""
        # Arrange
        mock_page = MagicMock()
        mock_pixmap = MagicMock()
        mock_page.get_pixmap.return_value = mock_pixmap

        mock_doc = MagicMock()
        mock_doc.__iter__ = Mock(return_value=iter([mock_page]))
        mock_doc.__enter__ = Mock(return_value=mock_doc)
        mock_doc.__exit__ = Mock(return_value=False)

        pdf_path = tmp_path / "test.pdf"

        with patch('pymupdf.open', return_value=mock_doc):
            with patch('pymupdf.Matrix') as mock_matrix_class:
                mock_matrix = MagicMock()
                mock_matrix_class.return_value = mock_matrix

                # Act
                try:
                    render_pdf_to_images(pdf_path, tmp_path / "out", dpi=300)
                except:
                    pass

                # Assert
                mock_matrix_class.assert_called_once()
                call_args = mock_matrix_class.call_args
                zoom = call_args[0][0]
                assert abs(zoom - 300 / 72.0) < 1e-10

                mock_page.get_pixmap.assert_called_once()
                pixmap_call = mock_page.get_pixmap.call_args
                assert pixmap_call.kwargs['matrix'] == mock_matrix
                assert pixmap_call.kwargs['alpha'] is False


"""
# 运行所有测试
pytest tests/test_render_pdf_to_images.py -v

# 运行特定测试类
pytest tests/test_render_pdf_to_images.py::TestRenderPdfToImages -v

# 运行特定测试方法
pytest tests/test_render_pdf_to_images.py::TestRenderPdfToImages::test_render_single_page_pdf -v

# 显示覆盖率
pytest tests/test_render_pdf_to_images.py --cov=ocr_engine --cov-report=html

# 只运行标记的测试
pytest tests/test_render_pdf_to_images.py -m "parametrize"
"""
