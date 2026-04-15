from __future__ import annotations

from pathlib import Path

from job_skeleton import DEFAULT_LOW_CONFIDENCE_THRESHOLD


def build_error_analysis_page(
    *,
    output_path: Path,
    job_id: str,
    source_file: str,
    default_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> None:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OCR Error Analysis - {job_id}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4efe5;
      --panel: rgba(255, 250, 242, 0.94);
      --ink: #2b241f;
      --muted: #6b6258;
      --accent: #b65a32;
      --line: #dbcdbd;
      --warn: #c2473c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Georgia", "Times New Roman", serif;
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
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 50px rgba(91, 67, 44, 0.08);
    }}
    .hero {{
      padding: 28px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      font-size: 13px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    h1, h2, h3 {{
      margin: 0 0 10px;
    }}
    .muted {{
      color: var(--muted);
    }}
    .toolbar {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      margin: 18px 0;
      padding: 20px;
    }}
    label {{
      display: block;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    input {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: white;
    }}
    .stats {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin-bottom: 18px;
    }}
    .stat {{
      padding: 16px 18px;
    }}
    .stat strong {{
      display: block;
      font-size: 28px;
      margin-top: 8px;
    }}
    .page-list {{
      display: grid;
      gap: 16px;
    }}
    .page-card {{
      padding: 20px;
    }}
    .page-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      flex-wrap: wrap;
    }}
    .tag {{
      display: inline-flex;
      padding: 4px 10px;
      border-radius: 999px;
      background: #f9e4d7;
      color: var(--accent);
      font-size: 13px;
    }}
    .preview-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin: 16px 0;
    }}
    img {{
      width: 100%;
      display: block;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: white;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
      background: white;
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #efe4d6;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #fcf4eb;
      font-size: 13px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    .empty {{
      padding: 18px;
      border-radius: 14px;
      border: 1px dashed var(--line);
      color: var(--muted);
      background: white;
    }}
    a {{
      color: var(--accent);
    }}
    .warn {{
      color: var(--warn);
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="eyebrow">OCR Inspector</div>
      <h1>错误分析页</h1>
      <p class="muted">任务 <code>{job_id}</code>，源文件 <code>{source_file}</code>。本页会读取同目录下的 <code>../ocr.json</code>，按阈值筛选低置信度词。</p>
      <p><a href="../ocr.json" target="_blank">查看 ocr.json</a> · <a href="../full_text.txt" target="_blank">查看 full_text.txt</a></p>
    </section>

    <section class="panel toolbar">
      <div>
        <label for="threshold">低置信度阈值 (%)</label>
        <input id="threshold" type="number" min="0" max="100" step="1" value="{default_threshold}" />
      </div>
      <div>
        <label for="search">搜索词</label>
        <input id="search" type="text" placeholder="例如 invoice / total / 日期" />
      </div>
    </section>

    <section id="stats" class="stats"></section>
    <section id="pages" class="page-list"></section>
  </main>

  <script>
    const statsEl = document.getElementById('stats');
    const pagesEl = document.getElementById('pages');
    const thresholdEl = document.getElementById('threshold');
    const searchEl = document.getElementById('search');

    const formatConfidence = (value) => Number.isFinite(value) && value >= 0
      ? `${{value.toFixed(1)}}%`
      : 'N/A';

    const escapeHtml = (str = '') => str.replace(/[&<>"']/g, (match) => ({{
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }}[match]));

    const load = async () => {{
      const response = await fetch('../ocr.json');
      if (!response.ok) {{
        throw new Error('无法加载 ../ocr.json');
      }}
      return response.json();
    }};

    const renderStats = (ocr, threshold, search) => {{
      const pages = ocr.pages ?? [];
      const words = pages.flatMap((page) => page.words ?? []);
      const lowWords = words.filter((word) => word.confidence >= 0 && word.confidence < threshold);
      const hits = search
        ? words.filter((word) => String(word.text || '').toLowerCase().includes(search))
        : [];

      statsEl.innerHTML = `
        <article class="panel stat">
          <div class="muted">页数</div>
          <strong>${{pages.length}}</strong>
        </article>
        <article class="panel stat">
          <div class="muted">总词数</div>
          <strong>${{words.length}}</strong>
        </article>
        <article class="panel stat">
          <div class="muted">低置信度词</div>
          <strong class="${{lowWords.length ? 'warn' : ''}}">${{lowWords.length}}</strong>
        </article>
        <article class="panel stat">
          <div class="muted">搜索命中</div>
          <strong>${{hits.length}}</strong>
        </article>
      `;
    }};

    const renderPages = (ocr, threshold, search) => {{
      const pageCards = (ocr.pages ?? []).map((page) => {{
        const words = page.words ?? [];
        const lowWords = words.filter((word) => word.confidence >= 0 && word.confidence < threshold);
        const searchHits = search
          ? words.filter((word) => String(word.text || '').toLowerCase().includes(search))
          : [];

        const rows = lowWords.length
          ? lowWords.map((word) => `
              <tr>
                <td>${{escapeHtml(word.text)}}</td>
                <td>${{formatConfidence(word.confidence)}}</td>
                <td>${{word.line_num ?? '-'}}</td>
                <td>${{word.bbox?.left ?? '-'}}, ${{word.bbox?.top ?? '-'}}, ${{word.bbox?.width ?? '-'}}, ${{word.bbox?.height ?? '-'}}</td>
              </tr>
            `).join('')
          : '<tr><td colspan="4">当前阈值下没有低置信度词。</td></tr>';

        const hitHtml = search
          ? (searchHits.length
            ? `<p class="muted">搜索命中：${{searchHits.map((word) => escapeHtml(word.text)).join(' / ')}}</p>`
            : '<p class="muted">搜索命中：无</p>')
          : '';

        return `
          <article class="panel page-card">
            <div class="page-head">
              <div>
                <h2>Page ${{page.page_num}}</h2>
                <p class="muted">尺寸 ${{page.image_width}} × ${{page.image_height}}，共 ${{words.length}} 个词。</p>
              </div>
              <div class="tag">低置信度 ${{lowWords.length}}</div>
            </div>
            <div class="preview-grid">
              <div>
                <h3>原始页图</h3>
                <a href="../pages/${{page.image_path}}" target="_blank"><img src="../pages/${{page.image_path}}" alt="page-${{page.page_num}}" /></a>
              </div>
              <div>
                <h3>叠框图</h3>
                <a href="../overlays/${{page.overlay_path}}" target="_blank"><img src="../overlays/${{page.overlay_path}}" alt="overlay-${{page.page_num}}" /></a>
              </div>
            </div>
            <p><a href="../texts/${{page.text_path}}" target="_blank">查看纯文本</a> · <a href="../markdown/${{page.markdown_path}}" target="_blank">查看 Markdown</a></p>
            ${{hitHtml}}
            <table>
              <thead>
                <tr>
                  <th>词</th>
                  <th>置信度</th>
                  <th>行号</th>
                  <th>bbox</th>
                </tr>
              </thead>
              <tbody>${{rows}}</tbody>
            </table>
          </article>
        `;
      }});

      pagesEl.innerHTML = pageCards.length
        ? pageCards.join('')
        : '<div class="panel empty">没有可显示的页面。</div>';
    }};

    const render = (ocr) => {{
      const threshold = Math.min(100, Math.max(0, Number(thresholdEl.value) || {default_threshold}));
      const search = searchEl.value.trim().toLowerCase();
      renderStats(ocr, threshold, search);
      renderPages(ocr, threshold, search);
    }};

    load()
      .then((ocr) => {{
        render(ocr);
        thresholdEl.addEventListener('input', () => render(ocr));
        searchEl.addEventListener('input', () => render(ocr));
      }})
      .catch((error) => {{
        statsEl.innerHTML = `<article class="panel stat"><div class="warn">${{escapeHtml(error.message)}}</div></article>`;
        pagesEl.innerHTML = '<div class="panel empty">分析页初始化失败。</div>';
      }});
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
