#!/usr/bin/python3
# coding: UTF-8
"""
Markdown 資料を配布用 HTML（および任意で PDF）へ変換する軽量スクリプト

外部パッケージ不要で、Python 3 標準ライブラリのみでスタンドアロン HTML を生成します。
--pdf を指定すると、Windows/Linux にインストールされている Microsoft Edge または
Google Chrome のヘッドレス印刷機能を使って PDF も同時に出力します。

実行例（EicDesignLab フォルダから）:
    python dev13/pc/render_manual.py dev13/docs/REMOTE_ACCESS_MANUAL.md
    python dev13/pc/render_manual.py dev13/docs/REMOTE_ACCESS_MANUAL.md --pdf
    python dev13/pc/render_manual.py dev13/docs/REMOTE_ACCESS_MANUAL.md -o out.html --pdf-output out.pdf
"""

import argparse
import html
import re
import shutil
import subprocess
from pathlib import Path


BROWSER_CANDIDATES = (
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
)

BROWSER_BINARIES = ('msedge', 'chrome', 'google-chrome', 'chromium', 'chromium-browser')

_HEADING_RE = re.compile(r'^(#{1,4})\s+(.*)$')
_FENCE_RE = re.compile(r'^```([A-Za-z0-9_+-]*)\s*$')
_UL_RE = re.compile(r'^(\s*)[-*]\s+(.*)$')
_OL_RE = re.compile(r'^(\s*)\d+\.\s+(.*)$')
_HR_RE = re.compile(r'^\s{0,3}([-*_])\1{2,}\s*$')
_TABLE_SEP_RE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')
_INLINE_CODE_RE = re.compile(r'(`[^`]+`)')
_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')


def _format_inline_plain(text):
    """インラインコード以外のテキストへエスケープ・太字・リンクを適用する。"""
    parts = []
    last = 0
    for match in _LINK_RE.finditer(text):
        before = text[last:match.start()]
        parts.append(_apply_bold(html.escape(before)))
        label = _apply_bold(html.escape(match.group(1)))
        url = html.escape(match.group(2).strip(), quote=True)
        parts.append('<a href="{}">{}</a>'.format(url, label))
        last = match.end()
    parts.append(_apply_bold(html.escape(text[last:])))
    return ''.join(parts)


def _apply_bold(escaped_text):
    return _BOLD_RE.sub(r'<strong>\1</strong>', escaped_text)


def format_inline(text):
    """インラインコード・太字・リンクを安全な HTML へ変換する。"""
    code_spans = []

    def _stash_code(match):
        idx = len(code_spans)
        code_spans.append('<code>{}</code>'.format(html.escape(match.group(1)[1:-1])))
        return '\x00CODE{}\x00'.format(idx)

    stashed = _INLINE_CODE_RE.sub(_stash_code, text)
    formatted = _format_inline_plain(stashed)
    for idx, code_html in enumerate(code_spans):
        formatted = formatted.replace('\x00CODE{}\x00'.format(idx), code_html)
    return formatted


def _split_table_row(line):
    stripped = line.strip()
    if stripped.startswith('|'):
        stripped = stripped[1:]
    if stripped.endswith('|'):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r'(?<!\\)\|', stripped)]


def _is_table_start(lines, idx):
    if idx + 1 >= len(lines):
        return False
    if '|' not in lines[idx]:
        return False
    return bool(_TABLE_SEP_RE.match(lines[idx + 1]))


def _slugify(text, seen):
    base = re.sub(r'[^\w\u3040-\u30ff\u4e00-\u9fff-]+', '-', text.strip().lower()).strip('-')
    if not base:
        base = 'section'
    slug = base
    cnt = 1
    while slug in seen:
        cnt += 1
        slug = '{}-{}'.format(base, cnt)
    seen.add(slug)
    return slug


def markdown_to_html(md_text):
    """Markdown 文字列を本文用 HTML 断片へ変換する。"""
    lines = md_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out = []
    seen_ids = set()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        # 1. フェンス付きコードブロック
        fence_match = _FENCE_RE.match(line.strip())
        if fence_match:
            lang = fence_match.group(1)
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            cls_attr = ' class="language-{}"'.format(html.escape(lang, quote=True)) if lang else ''
            out.append(
                '<pre><code{}>{}</code></pre>'.format(
                    cls_attr, html.escape('\n'.join(code_lines))
                )
            )
            continue

        # 2. 見出し
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            raw_title = heading_match.group(2).strip()
            anchor = _slugify(raw_title, seen_ids)
            out.append(
                '<h{lvl} id="{anc}">{txt}</h{lvl}>'.format(
                    lvl=level, anc=anchor, txt=format_inline(raw_title)
                )
            )
            i += 1
            continue

        # 3. 水平線
        if _HR_RE.match(line):
            out.append('<hr>')
            i += 1
            continue

        # 4. 引用（コールアウト）
        if line.lstrip().startswith('>'):
            quote_lines = []
            while i < n and lines[i].lstrip().startswith('>'):
                stripped = lines[i].lstrip()[1:]
                if stripped.startswith(' '):
                    stripped = stripped[1:]
                quote_lines.append(stripped)
                i += 1
            inner_html = markdown_to_html('\n'.join(quote_lines))
            out.append('<blockquote>{}</blockquote>'.format(inner_html))
            continue

        # 5. テーブル
        if _is_table_start(lines, i):
            headers = _split_table_row(lines[i])
            i += 2
            rows = []
            while i < n and '|' in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            tbl = ['<table>', '<thead><tr>']
            for cell in headers:
                tbl.append('<th>{}</th>'.format(format_inline(cell.replace(r'\|', '|'))))
            tbl.append('</tr></thead>')
            if rows:
                tbl.append('<tbody>')
                for row in rows:
                    tbl.append('<tr>')
                    for col_idx in range(len(headers)):
                        val = row[col_idx] if col_idx < len(row) else ''
                        tbl.append('<td>{}</td>'.format(format_inline(val.replace(r'\|', '|'))))
                    tbl.append('</tr>')
                tbl.append('</tbody>')
            tbl.append('</table>')
            out.append(''.join(tbl))
            continue

        # 6. 箇条書き・番号付きリスト（ネスト対応）
        ul_match = _UL_RE.match(line)
        ol_match = _OL_RE.match(line)
        if ul_match or ol_match:
            list_html, i = _parse_list(lines, i)
            out.append(list_html)
            continue

        # 7. 段落
        para_lines = []
        while i < n:
            cur = lines[i]
            if (
                not cur.strip()
                or _FENCE_RE.match(cur.strip())
                or _HEADING_RE.match(cur)
                or _HR_RE.match(cur)
                or cur.lstrip().startswith('>')
                or _is_table_start(lines, i)
                or _UL_RE.match(cur)
                or _OL_RE.match(cur)
            ):
                break
            para_lines.append(cur.strip())
            i += 1
        joined = '<br>\n'.join(format_inline(p[:-2]) if p.endswith('  ') else format_inline(p) for p in para_lines)
        # 2スペース改行以外の行結合を自然にする
        if not any(p.endswith('  ') for p in para_lines[:-1]):
            joined = ' '.join(format_inline(p) for p in para_lines)
        out.append('<p>{}</p>'.format(joined))

    return '\n'.join(out)


def _parse_list(lines, start_idx):
    """箇条書きまたは番号付きリストをネスト・コードブロック付きで HTML 化する。"""
    n = len(lines)
    first_ul = _UL_RE.match(lines[start_idx])
    first_ol = _OL_RE.match(lines[start_idx])
    base_indent = len((first_ul or first_ol).group(1))
    tag = 'ul' if first_ul else 'ol'
    items = []
    i = start_idx

    while i < n:
        line = lines[i]
        if not line.strip():
            # 空行の後に同レベル以上のリスト項目またはインデントされた継続があれば継続
            if i + 1 < n:
                nxt = lines[i + 1]
                m_ul = _UL_RE.match(nxt)
                m_ol = _OL_RE.match(nxt)
                if (m_ul and len(m_ul.group(1)) >= base_indent) or (
                    m_ol and len(m_ol.group(1)) >= base_indent
                ):
                    i += 1
                    continue
            break

        m_ul = _UL_RE.match(line)
        m_ol = _OL_RE.match(line)
        cur_match = m_ul or m_ol
        if cur_match:
            indent = len(cur_match.group(1))
            cur_tag = 'ul' if m_ul else 'ol'
            if indent < base_indent or (indent == base_indent and cur_tag != tag):
                break
            if indent > base_indent:
                sub_html, i = _parse_list(lines, i)
                if items:
                    items[-1].append(sub_html)
                continue

            item_parts = [format_inline(cur_match.group(2).strip())]
            i += 1
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    break
                sub_ul = _UL_RE.match(nxt)
                sub_ol = _OL_RE.match(nxt)
                if sub_ul or sub_ol:
                    sub_indent = len((sub_ul or sub_ol).group(1))
                    if sub_indent > base_indent:
                        sub_html, i = _parse_list(lines, i)
                        item_parts.append(sub_html)
                        continue
                    break
                nxt_indent = len(nxt) - len(nxt.lstrip(' '))
                if nxt_indent > base_indent:
                    fence = _FENCE_RE.match(nxt.strip())
                    if fence:
                        lang = fence.group(1)
                        i += 1
                        code_lines = []
                        while i < n and not lines[i].strip().startswith('```'):
                            raw_code = lines[i]
                            if len(raw_code) >= nxt_indent and raw_code[:nxt_indent].isspace():
                                raw_code = raw_code[nxt_indent:]
                            code_lines.append(raw_code)
                            i += 1
                        if i < n:
                            i += 1
                        cls_attr = ' class="language-{}"'.format(html.escape(lang, quote=True)) if lang else ''
                        item_parts.append(
                            '<pre><code{}>{}</code></pre>'.format(
                                cls_attr, html.escape('\n'.join(code_lines))
                            )
                        )
                    else:
                        item_parts.append('<br>' + format_inline(nxt.strip()))
                        i += 1
                else:
                    break
            items.append(item_parts)
        else:
            break

    rendered_items = ''.join('<li>{}</li>'.format(''.join(parts)) for parts in items)
    return '<{t}>{body}</{t}>'.format(t=tag, body=rendered_items), i


def render_html_document(title, body_html):
    """印刷対応 CSS 付きのスタンドアロン HTML 文書を返す。"""
    escaped_title = html.escape(title)
    return """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    --fg: #1f2937;
    --muted: #4b5563;
    --bg: #ffffff;
    --surface: #f8fafc;
    --border: #cbd5e1;
    --accent: #0f766e;
    --warn-bg: #f0fdfa;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Meiryo", "Yu Gothic UI", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP", sans-serif;
    color: var(--fg);
    background: var(--bg);
    line-height: 1.65;
    max-width: 920px;
    margin: 0 auto;
    padding: 28px 24px 48px;
    font-size: 14.5px;
  }}
  h1 {{
    font-size: 1.75rem;
    border-bottom: 3px solid var(--accent);
    padding-bottom: 8px;
    margin-top: 0;
  }}
  h2 {{
    font-size: 1.32rem;
    border-left: 5px solid var(--accent);
    padding-left: 10px;
    margin-top: 2rem;
    break-after: avoid;
  }}
  h3 {{
    font-size: 1.08rem;
    color: #0f172a;
    margin-top: 1.4rem;
    break-after: avoid;
  }}
  h4 {{
    font-size: 0.98rem;
    color: var(--muted);
    margin-top: 1.1rem;
    break-after: avoid;
  }}
  hr {{
    border: none;
    border-top: 1px solid var(--border);
    margin: 1.4rem 0;
  }}
  p {{ margin: 0.65rem 0; }}
  ul, ol {{
    margin: 0.5rem 0 0.8rem;
    padding-left: 1.55rem;
  }}
  li {{ margin: 0.28rem 0; }}
  code {{
    font-family: "Consolas", "Cascadia Mono", "DejaVu Sans Mono", monospace;
    background: #f1f5f9;
    padding: 0.12em 0.36em;
    border-radius: 4px;
    font-size: 0.91em;
  }}
  pre {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 4px solid var(--accent);
    border-radius: 6px;
    padding: 10px 14px;
    overflow-x: auto;
    line-height: 1.45;
    margin: 0.65rem 0;
    break-inside: avoid;
  }}
  pre code {{
    background: transparent;
    padding: 0;
    font-size: 0.88em;
    white-space: pre-wrap;
    word-break: break-all;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 0.9rem 0;
    font-size: 0.93em;
    break-inside: avoid;
  }}
  th, td {{
    border: 1px solid var(--border);
    padding: 7px 10px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #eef2f6;
    font-weight: 700;
  }}
  blockquote {{
    margin: 0.9rem 0;
    padding: 10px 14px;
    background: var(--warn-bg);
    border-left: 4px solid var(--accent);
    border-radius: 4px;
    break-inside: avoid;
  }}
  blockquote p:first-child {{ margin-top: 0; }}
  blockquote p:last-child {{ margin-bottom: 0; }}
  a {{ color: var(--accent); }}
  @page {{
    size: A4;
    margin: 15mm;
  }}
  @media print {{
    body {{
      max-width: none;
      padding: 0;
      font-size: 10.5pt;
    }}
    pre {{
      border-color: #94a3b8;
    }}
  }}
</style>
</head>
<body>
{body}
</body>
</html>
""".format(title=escaped_title, body=body_html)


def _extract_title(md_text, fallback):
    for line in md_text.splitlines():
        m = re.match(r'^#\s+(.*)$', line.strip())
        if m:
            return m.group(1).strip()
    return fallback


def find_browser_executable():
    """PDF 変換に使用可能な Edge / Chrome 実行ファイルを探す。"""
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in BROWSER_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    return None


def convert_html_to_pdf(html_path, pdf_path, browser_path=None):
    """ブラウザのヘッドレス印刷機能を用いて HTML を PDF 化する。"""
    browser = browser_path or find_browser_executable()
    if not browser:
        raise RuntimeError(
            'PDF 変換用の Edge / Chrome が見つかりません。'
            '出力された HTML ({}) をブラウザで開き、Ctrl+P →「PDF として保存」を利用してください。'.format(
                html_path
            )
        )

    html_abs = Path(html_path).resolve()
    pdf_abs = Path(pdf_path).resolve()
    pdf_abs.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        browser,
        '--headless=new',
        '--disable-gpu',
        '--no-pdf-header-footer',
        '--print-to-pdf={}'.format(pdf_abs),
        html_abs.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not pdf_abs.is_file() or pdf_abs.stat().st_size == 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(
            'PDF 変換に失敗しました (code={}): {}。'
            'HTML ({}) をブラウザで開いて Ctrl+P →「PDF として保存」をお試しください。'.format(
                result.returncode, detail, html_abs
            )
        )
    return pdf_abs


def render_manual(md_path, html_path=None, pdf=False, pdf_path=None, browser_path=None):
    """Markdown ファイルを HTML（および任意で PDF）へ変換する。"""
    md_path = Path(md_path)
    md_text = md_path.read_text(encoding='utf-8')
    title = _extract_title(md_text, md_path.stem)
    body_html = markdown_to_html(md_text)
    full_html = render_html_document(title, body_html)

    if html_path is None:
        html_path = md_path.with_suffix('.html')
    else:
        html_path = Path(html_path)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(full_html, encoding='utf-8')

    generated_pdf = None
    if pdf or pdf_path is not None:
        target_pdf = Path(pdf_path) if pdf_path is not None else html_path.with_suffix('.pdf')
        generated_pdf = convert_html_to_pdf(html_path, target_pdf, browser_path=browser_path)

    return html_path, generated_pdf


def build_parser():
    parser = argparse.ArgumentParser(
        description='Markdown 資料を配布用 HTML（および任意で PDF）へ変換します'
    )
    parser.add_argument('md_file', type=Path, help='入力 Markdown ファイル')
    parser.add_argument('-o', '--output', type=Path, default=None, help='出力 HTML パス')
    parser.add_argument('--pdf', action='store_true', help='HTML と同じ場所に PDF も出力する')
    parser.add_argument('--pdf-output', type=Path, default=None, help='出力 PDF パス')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.md_file.is_file():
        parser.error('Markdown ファイルが見つかりません: {}'.format(args.md_file))

    try:
        html_out, pdf_out = render_manual(
            args.md_file,
            html_path=args.output,
            pdf=args.pdf,
            pdf_path=args.pdf_output,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('エラー: {}'.format(exc), file=__import__('sys').stderr)
        raise SystemExit(1) from None

    print('HTML を生成しました: {}'.format(html_out))
    if pdf_out is not None:
        print('PDF  を生成しました: {}'.format(pdf_out))


if __name__ == '__main__':
    main()
