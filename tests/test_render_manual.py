# coding: UTF-8
"""
遠隔操作マニュアルおよび Markdown → HTML/PDF 変換スクリプトの単体テスト
"""

import re
import tempfile
import unittest
from pathlib import Path

from pc import render_manual


MANUAL_PATH = (
    Path(__file__).resolve().parent.parent / 'docs' / 'REMOTE_ACCESS_MANUAL.md'
)


class TestRenderManual(unittest.TestCase):
    def test_markdown_to_html_handles_blocks_tables_escaping_and_lists(self):
        sample = (
            '# テスト題名\n\n'
            '本文に `a < b & c` と **太字** と [リンク](https://example.com) を含む。\n\n'
            '> 注意: 重要な囲みです。\n\n'
            '| 列1 | 列2 |\n'
            '|---|---|\n'
            '| 値A | `code` |\n\n'
            '1. 手順1\n'
            '   ```powershell\n'
            '   echo "<ok>"\n'
            '   ```\n'
            '2. 手順2\n'
            '3. **インラインコード `x` を含む太字**\n'
        )
        html_body = render_manual.markdown_to_html(sample)
        self.assertIn('<h1 id="テスト題名">テスト題名</h1>', html_body)
        self.assertIn('<code>a &lt; b &amp; c</code>', html_body)
        self.assertIn('<strong>太字</strong>', html_body)
        self.assertIn('<a href="https://example.com">リンク</a>', html_body)
        self.assertIn('<blockquote><p>注意: 重要な囲みです。</p></blockquote>', html_body)
        self.assertIn('<table><thead><tr><th>列1</th><th>列2</th></tr></thead>', html_body)
        self.assertIn('<ol><li>手順1<pre><code class="language-powershell">echo &quot;&lt;ok&gt;&quot;</code></pre></li><li>手順2</li><li><strong>インラインコード <code>x</code> を含む太字</strong></li></ol>', html_body)

    def test_remote_access_manual_renders_clean_html_and_has_no_plaintext_password(self):
        md_text = MANUAL_PATH.read_text(encoding='utf-8')
        # 配布用マニュアル内に平文パスワード（group13）が記載されていないことを確認
        self.assertIsNone(
            re.search(r'(パスワード|password)\s*[:：]\s*`?group13`?', md_text, re.IGNORECASE)
        )
        self.assertIn('ssh-keygen -t ed25519', md_text)
        self.assertIn('authorized_keys', md_text)
        self.assertIn('-Encoding ascii', md_text)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_html = Path(tmpdir) / 'REMOTE_ACCESS_MANUAL.html'
            written_html, generated_pdf = render_manual.render_manual(
                MANUAL_PATH, html_path=out_html, pdf=False
            )
            self.assertEqual(written_html, out_html)
            self.assertIsNone(generated_pdf)
            rendered = out_html.read_text(encoding='utf-8')
            self.assertIn('<html lang="ja">', rendered)
            self.assertIn('グループ13 車載 Raspberry Pi Zero 2 W 遠隔操作マニュアル', rendered)
            self.assertIn('<table>', rendered)
            self.assertIn('<blockquote>', rendered)
            self.assertIn('<pre><code class="language-powershell">', rendered)


if __name__ == '__main__':
    unittest.main()
