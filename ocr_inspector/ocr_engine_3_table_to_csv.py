from __future__ import annotations

"""Table to CSV 基础能力。

第 3 步负责 PDF 原生表格检测、OCR 词框表格结构恢复，并导出 CSV/HTML。
"""

import contextlib
import csv
from html import escape as html_escape
import io
from pathlib import Path
from typing import Any

import pymupdf

from ocr_engine_1_inspector import _axis_bbox
from ocr_engine_2_layout_reader import _normalize_layout_text, _safe_median

_TABLE_MIN_ROWS = 2
_TABLE_MIN_COLS = 2
_TABLE_ROW_GAP_RATIO = 2.2
_TABLE_COLUMN_TOLERANCE_RATIO = 0.025
_TABLE_CELL_GAP_RATIO = 1.7


def _words_to_text(words: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for word in words:
        text = _normalize_layout_text(word["text"])
        if not text:
            continue
        if parts and parts[-1].endswith("-") and text[:1].islower():
            parts[-1] = parts[-1][:-1] + text
            continue
        parts.append(text)
    return " ".join(parts).strip()


def _bbox_from_word_items(words: list[dict[str, Any]]) -> dict[str, Any]:
    left = min(word["bbox"]["left"] for word in words)
    top = min(word["bbox"]["top"] for word in words)
    right = max(word["bbox"]["right"] for word in words)
    bottom = max(word["bbox"]["bottom"] for word in words)
    return {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
        "right": right,
        "bottom": bottom,
    }


def _build_table_segment(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "text": _words_to_text(words),
        "bbox": _bbox_from_word_items(words),
        "word_count": len(words),
    }


def _build_candidate_table_row(words: list[dict[str, Any]], default_gap: float) -> dict[str, Any]:
    ordered = sorted(words, key=lambda word: word["bbox"]["left"])
    word_heights = [word["bbox"]["height"] for word in ordered]
    gap_threshold = max(
        18.0,
        _safe_median(word_heights, default_gap) * _TABLE_CELL_GAP_RATIO,
    )

    segments: list[dict[str, Any]] = []
    current_segment_words: list[dict[str, Any]] = []
    for word in ordered:
        if not current_segment_words:
            current_segment_words = [word]
            continue

        previous = current_segment_words[-1]
        gap = word["bbox"]["left"] - previous["bbox"]["right"]
        if gap <= gap_threshold:
            current_segment_words.append(word)
            continue

        segment = _build_table_segment(current_segment_words)
        if segment["text"]:
            segments.append(segment)
        current_segment_words = [word]

    if current_segment_words:
        segment = _build_table_segment(current_segment_words)
        if segment["text"]:
            segments.append(segment)

    row_bbox = _bbox_from_word_items(ordered)
    return {
        "bbox": row_bbox,
        "segments": segments,
        "cell_count": len(segments),
        "avg_height": sum(word_heights) / len(word_heights),
    }


def _group_words_to_candidate_table_rows(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usable_words = [word for word in words if _normalize_layout_text(word["text"])]
    if not usable_words:
        return []

    usable_words.sort(key=lambda word: (word["bbox"]["top"], word["bbox"]["left"]))
    median_height = _safe_median(
        [word["bbox"]["height"] for word in usable_words],
        18.0,
    )
    row_tolerance = max(8.0, median_height * 0.75)

    rows: list[dict[str, Any]] = []
    current_row_words: list[dict[str, Any]] = []
    for word in usable_words:
        if not current_row_words:
            current_row_words = [word]
            continue

        current_bbox = _bbox_from_word_items(current_row_words)
        current_center = current_bbox["top"] + current_bbox["height"] / 2
        word_box = word["bbox"]
        word_center = word_box["top"] + word_box["height"] / 2
        vertical_overlap = min(current_bbox["bottom"], word_box["bottom"]) - max(
            current_bbox["top"],
            word_box["top"],
        )
        overlaps_enough = vertical_overlap >= min(current_bbox["height"], word_box["height"]) * 0.35
        if overlaps_enough or abs(word_center - current_center) <= max(row_tolerance, current_bbox["height"] * 0.7):
            current_row_words.append(word)
            continue

        rows.append(_build_candidate_table_row(current_row_words, median_height))
        current_row_words = [word]

    if current_row_words:
        rows.append(_build_candidate_table_row(current_row_words, median_height))

    return [row for row in rows if row["segments"]]


def _horizontal_overlap_amount(first: dict[str, Any], second: dict[str, Any]) -> float:
    return max(0.0, min(first["right"], second["right"]) - max(first["left"], second["left"]))


def _rows_share_table_structure(
        first: dict[str, Any],
        second: dict[str, Any],
        *,
        page_width: int,
        median_height: float,
) -> bool:
    gap = second["bbox"]["top"] - first["bbox"]["bottom"]
    if gap > max(24.0, median_height * _TABLE_ROW_GAP_RATIO):
        return False

    tolerance = max(
        18.0,
        page_width * _TABLE_COLUMN_TOLERANCE_RATIO,
        median_height * 1.4,
    )
    first_centers = [
        segment["bbox"]["left"] + segment["bbox"]["width"] / 2
        for segment in first["segments"]
    ]
    second_centers = [
        segment["bbox"]["left"] + segment["bbox"]["width"] / 2
        for segment in second["segments"]
    ]
    aligned_columns = sum(
        1
        for current in second_centers
        if any(abs(current - previous) <= tolerance for previous in first_centers)
    )
    return aligned_columns >= min(2, len(first_centers), len(second_centers))


def _cluster_table_columns(
        rows: list[dict[str, Any]],
        *,
        page_width: int,
        median_height: float,
) -> list[dict[str, Any]]:
    tolerance = max(
        18.0,
        page_width * _TABLE_COLUMN_TOLERANCE_RATIO,
        median_height * 1.4,
    )
    columns: list[dict[str, Any]] = []

    # 这里用“按 x 方向聚类”的方式恢复列结构，避免简单按最大列数硬切导致错列。
    for segment in sorted(
            (segment for row in rows for segment in row["segments"]),
            key=lambda item: (item["bbox"]["left"] + item["bbox"]["right"]) / 2,
    ):
        segment_box = segment["bbox"]
        segment_center = segment_box["left"] + segment_box["width"] / 2
        best_column: dict[str, Any] | None = None
        best_distance = float("inf")

        for column in columns:
            overlap = _horizontal_overlap_amount(segment_box, column)
            in_band = column["left"] - tolerance <= segment_center <= column["right"] + tolerance
            if overlap < min(segment_box["width"], column["width"]) * 0.18 and not in_band:
                continue

            distance = abs(segment_center - column["center"])
            if distance < best_distance:
                best_distance = distance
                best_column = column

        if best_column is None:
            columns.append(
                {
                    "left": segment_box["left"],
                    "right": segment_box["right"],
                    "width": segment_box["width"],
                    "center": segment_center,
                    "count": 1,
                }
            )
            continue

        best_column["left"] = min(best_column["left"], segment_box["left"])
        best_column["right"] = max(best_column["right"], segment_box["right"])
        best_column["width"] = best_column["right"] - best_column["left"]
        best_column["count"] += 1
        best_column["center"] = (
            best_column["center"] * (best_column["count"] - 1) + segment_center
        ) / best_column["count"]

    ordered_columns = sorted(columns, key=lambda column: column["center"])
    merged_columns: list[dict[str, Any]] = []
    for column in ordered_columns:
        if merged_columns and column["left"] <= merged_columns[-1]["right"] + tolerance * 0.35:
            previous = merged_columns[-1]
            combined_count = previous["count"] + column["count"]
            previous["left"] = min(previous["left"], column["left"])
            previous["right"] = max(previous["right"], column["right"])
            previous["width"] = previous["right"] - previous["left"]
            previous["center"] = (
                previous["center"] * previous["count"] + column["center"] * column["count"]
            ) / combined_count
            previous["count"] = combined_count
            continue
        merged_columns.append(dict(column))

    return merged_columns


def _assign_segment_to_table_column(segment: dict[str, Any], columns: list[dict[str, Any]]) -> int:
    segment_box = segment["bbox"]
    best_index: int | None = None
    best_overlap = -1.0

    for index, column in enumerate(columns):
        overlap = _horizontal_overlap_amount(segment_box, column)
        if overlap >= min(segment_box["width"], column["width"]) * 0.18 and overlap > best_overlap:
            best_overlap = overlap
            best_index = index

    if best_index is not None:
        return best_index

    segment_center = segment_box["left"] + segment_box["width"] / 2
    return min(
        range(len(columns)),
        key=lambda index: abs(segment_center - columns[index]["center"]),
    )


def _normalize_table_matrix(rows: list[list[str]]) -> list[list[str]]:
    normalized = [
        [_normalize_layout_text(cell) for cell in row]
        for row in rows
    ]
    normalized = [row for row in normalized if any(row)]
    if not normalized:
        return []

    max_cols = max(len(row) for row in normalized)
    padded = [row + [""] * (max_cols - len(row)) for row in normalized]
    used_indices = [
        index
        for index in range(max_cols)
        if any(row[index] for row in padded)
    ]
    if not used_indices:
        return []

    return [
        [row[index] for index in used_indices]
        for row in padded
    ]


def _build_table_from_candidate_rows(
        rows: list[dict[str, Any]],
        *,
        page_num: int,
        page_width: int,
        page_height: int,
        source: str,
) -> dict[str, Any] | None:
    if len(rows) < _TABLE_MIN_ROWS:
        return None

    median_height = _safe_median([row["avg_height"] for row in rows], 18.0)
    columns = _cluster_table_columns(
        rows,
        page_width=page_width,
        median_height=median_height,
    )
    if len(columns) < _TABLE_MIN_COLS:
        return None

    matrix: list[list[str]] = []
    for row in rows:
        grid = [""] * len(columns)
        for segment in row["segments"]:
            column_index = _assign_segment_to_table_column(segment, columns)
            segment_text = _normalize_layout_text(segment["text"])
            if not segment_text:
                continue
            if grid[column_index]:
                grid[column_index] = f"{grid[column_index]} {segment_text}"
            else:
                grid[column_index] = segment_text
        matrix.append(grid)

    matrix = _normalize_table_matrix(matrix)
    if len(matrix) < _TABLE_MIN_ROWS or len(matrix[0]) < _TABLE_MIN_COLS:
        return None

    filled_cell_count = sum(1 for row in matrix for cell in row if cell)
    total_cell_count = len(matrix) * len(matrix[0])
    multi_cell_row_count = sum(1 for row in matrix if sum(1 for cell in row if cell) >= 2)
    if total_cell_count <= 0:
        return None
    if filled_cell_count / total_cell_count < 0.35 or multi_cell_row_count < 2:
        return None

    bbox = _axis_bbox(
        min(row["bbox"]["left"] for row in rows),
        min(row["bbox"]["top"] for row in rows),
        max(row["bbox"]["right"] for row in rows),
        max(row["bbox"]["bottom"] for row in rows),
        (page_width, page_height),
    )
    if not bbox:
        return None

    return {
        "page_num": page_num,
        "source": source,
        "row_count": len(matrix),
        "col_count": len(matrix[0]),
        "bbox": bbox,
        "rows": matrix,
    }


def _detect_tables_from_ocr_page(page_result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _group_words_to_candidate_table_rows(page_result["words"])
        if row["cell_count"] >= _TABLE_MIN_COLS
    ]
    if len(rows) < _TABLE_MIN_ROWS:
        return []

    median_height = _safe_median([row["avg_height"] for row in rows], 18.0)
    blocks: list[list[dict[str, Any]]] = []
    current_block: list[dict[str, Any]] = []

    for row in rows:
        if not current_block:
            current_block = [row]
            continue

        if _rows_share_table_structure(
                current_block[-1],
                row,
                page_width=page_result["image_width"],
                median_height=median_height,
        ):
            current_block.append(row)
            continue

        blocks.append(current_block)
        current_block = [row]

    if current_block:
        blocks.append(current_block)

    tables: list[dict[str, Any]] = []
    for block in blocks:
        table = _build_table_from_candidate_rows(
            block,
            page_num=page_result["page_num"],
            page_width=page_result["image_width"],
            page_height=page_result["image_height"],
            source="ocr_layout",
        )
        if table is not None:
            tables.append(table)

    return tables


def _extract_tables_from_pdf_page(page: pymupdf.Page, page_num: int) -> list[dict[str, Any]]:
    table_buffer = io.StringIO()
    with contextlib.redirect_stdout(table_buffer):
        try:
            finder = page.find_tables()
        except Exception:  # noqa: BLE001
            return []

    extracted_tables: list[dict[str, Any]] = []
    for table in finder.tables:
        rows = _normalize_table_matrix(table.extract())
        if len(rows) < _TABLE_MIN_ROWS:
            continue
        if len(rows[0]) < _TABLE_MIN_COLS:
            continue

        non_empty_cells = sum(1 for row in rows for cell in row if cell)
        if non_empty_cells < 4:
            continue

        bbox = _axis_bbox(
            table.bbox[0],
            table.bbox[1],
            table.bbox[2],
            table.bbox[3],
            (
                max(1, int(round(page.rect.width))),
                max(1, int(round(page.rect.height))),
            ),
        )
        if not bbox:
            continue

        extracted_tables.append(
            {
                "page_num": page_num,
                "source": "pdf_text",
                "row_count": len(rows),
                "col_count": len(rows[0]),
                "bbox": bbox,
                "rows": rows,
            }
        )

    return extracted_tables


def _render_table_matrix_html(rows: list[list[str]]) -> str:
    html_rows: list[str] = []
    for row_index, row in enumerate(rows):
        cell_tag = "th" if row_index == 0 else "td"
        cells_html = "".join(
            f"<{cell_tag}>{html_escape(cell) if cell else '&nbsp;'}</{cell_tag}>"
            for cell in row
        )
        html_rows.append(f"<tr>{cells_html}</tr>")
    return "\n".join(html_rows)


def _write_table_csv(rows: list[list[str]], output_path: Path) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerows(rows)
    output_path.write_text(buffer.getvalue(), encoding="utf-8")


def _write_table_html(table: dict[str, Any], output_path: Path, *, source_file: str) -> None:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html_escape(table["table_id"])} - OCR Table Export</title>
  <style>
    :root {{
      --bg: #f4efe5;
      --panel: rgba(255, 250, 242, 0.94);
      --ink: #2b241f;
      --muted: #6b6258;
      --line: #dbcdbd;
      --accent: #b65a32;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(182, 90, 50, 0.18), transparent 32%),
        linear-gradient(180deg, #f7f1e8 0%, #efe5d6 100%);
    }}
    main {{
      width: min(1080px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(91, 67, 44, 0.08);
      padding: 24px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border: 1px solid #efe4d6;
      vertical-align: top;
      text-align: left;
      white-space: pre-wrap;
    }}
    th {{
      background: #fcf4eb;
    }}
    a {{
      color: var(--accent);
    }}
    code {{
      font-family: "SFMono-Regular", monospace;
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <div class="eyebrow">OCR Inspector</div>
      <h1>{html_escape(table["table_id"])}</h1>
      <p>源文件 <code>{html_escape(source_file)}</code>，第 <code>{table["page_num"]}</code> 页，来源 <code>{html_escape(table["source"])}</code>。</p>
      <p>行数 <code>{table["row_count"]}</code>，列数 <code>{table["col_count"]}</code>。<a href="{html_escape(table["csv_path"])}" target="_blank">下载 CSV</a></p>
    </section>
    <section class="panel">
      <table>
        <tbody>
          {_render_table_matrix_html(table["rows"])}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _write_tables_index(tables: list[dict[str, Any]], output_path: Path, *, source_file: str) -> None:
    cards = []
    for table in tables:
        preview_rows = _render_table_matrix_html(table["rows"][: min(4, len(table["rows"]))])
        cards.append(
            f"""
      <article class="panel">
        <h2>{html_escape(table["table_id"])}</h2>
        <p>第 <code>{table["page_num"]}</code> 页，来源 <code>{html_escape(table["source"])}</code>，共 <code>{table["row_count"]}</code> 行 <code>{table["col_count"]}</code> 列。</p>
        <p><a href="{html_escape(table["csv_path"])}" target="_blank">CSV</a> · <a href="{html_escape(table["html_path"])}" target="_blank">HTML</a></p>
        <table>
          <tbody>
            {preview_rows}
          </tbody>
        </table>
      </article>
"""
        )

    body = "\n".join(cards) if cards else """
      <section class="panel">
        <p>当前任务没有检测到可导出的表格。</p>
      </section>
"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OCR Table Exports</title>
  <style>
    :root {{
      --bg: #f4efe5;
      --panel: rgba(255, 250, 242, 0.94);
      --ink: #2b241f;
      --line: #dbcdbd;
      --accent: #b65a32;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(182, 90, 50, 0.18), transparent 32%),
        linear-gradient(180deg, #f7f1e8 0%, #efe5d6 100%);
    }}
    main {{
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(91, 67, 44, 0.08);
      padding: 24px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      margin-top: 14px;
    }}
    th, td {{
      padding: 10px 12px;
      border: 1px solid #efe4d6;
      vertical-align: top;
      text-align: left;
      white-space: pre-wrap;
    }}
    th {{
      background: #fcf4eb;
    }}
    a {{
      color: var(--accent);
    }}
    code {{
      font-family: "SFMono-Regular", monospace;
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <div class="eyebrow">OCR Inspector</div>
      <h1>表格导出索引</h1>
      <p>源文件 <code>{html_escape(source_file)}</code>。当前共导出 <code>{len(tables)}</code> 张表。</p>
    </section>
    {body}
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
