"""Guide acceptance using real Node/Marked/MathJax/sanitize-html rendering.

Every Markdown document, asset and application database is a disposable fixture.
No renderer is mocked and no account creation or invitation login is exercised.
"""
from __future__ import annotations

from contextlib import closing
from html.parser import HTMLParser
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import unittest
from urllib.parse import parse_qs, unquote, urlsplit
import zlib

from fastapi.testclient import TestClient

from math_learning.api import create_app
from math_learning.config import Settings
from math_learning.guide import GuideReader
from math_learning.service import DomainError, LearningService
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory


class HTMLProbe(HTMLParser):
    void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs), "parents": tuple(self.stack)})
        if tag not in self.void_tags:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data):
        self.text.append((tuple(self.stack), data))

    def tags(self, tag):
        return [element for element in self.elements if element["tag"] == tag]

    def text_inside(self, tag):
        return "".join(data for parents, data in self.text if tag in parents)


def png_pixel(red, green, blue):
    """Produce a real one-pixel PNG without an image-library dependency."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes((0, red, green, blue)))) + chunk(b"IEND", b""))


class GuideFixture(unittest.TestCase):
    asset_base = "https://fixture.trycloudflare.com/guide-assets"

    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.guide_root = self.root / "guide"
        self.guide_root.mkdir()
        self.source = self.guide_root / "guide.md"
        self.reader = GuideReader(self.source)
        self.assertIsNotNone(shutil.which("node"), "Guide tests require the real installed Node renderer")

    def _asset_file(self, relative, data=None):
        target = self.guide_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_pixel(255, 0, 0) if data is None else data)
        return target

    def _render(self, markdown):
        self.source.write_text(markdown, encoding="utf-8")
        result = self.reader.read(self.asset_base)
        self.assertEqual(result["status"], "ready", result)
        self.assertTrue(result["html"])
        self.assertRegex(result["version"], r"^[a-f0-9]{64}$")
        self.assertTrue(result["modified_at"])
        return result

    def _missing_asset(self, relative, reader=None):
        with self.assertRaises(DomainError) as caught:
            (reader or self.reader).asset(relative)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.code, "guide_asset_not_found")

    def _unavailable(self, result):
        self.assertEqual(result["status"], "unavailable", result.get("message"))
        self.assertEqual(result["html"], "")
        self.assertEqual(result["math_css"], "")
        self.assertIsNone(result["version"])
        self.assertTrue(result["message"])


class GuideReaderAcceptanceTests(GuideFixture):
    def test_standard_markdown_structure_and_code_survive_real_rendering(self):
        result = self._render(r'''# 使用指南

## 同一标题

## 同一标题

- 父级项目
  - 子级项目
    1. 深层项目
- 第二项目

| 列名 | 数值 |
| :--- | ---: |
| 第一行 | 5 |
| 第二行 | 8 |

**加粗**、*斜体*、~~删除~~ 和 `literal $x$`。

```javascript
const literal = "$x$ <script>fixture()</script>";
const tex = "\\href{https://example.invalid}{literal code}";
```

[站外说明](https://example.invalid/docs?q=1 "站外标题") 和 [页内跳转](#使用指南)。
''')
        tree = HTMLProbe(result["html"])
        for tag in ("h1", "h2", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "strong", "em", "del", "pre", "code", "a"):
            self.assertTrue(tree.tags(tag), tag)
        self.assertTrue(any(element["parents"].count("ul") >= 2 for element in tree.tags("ol")))
        self.assertEqual(len(tree.tags("tr")), 3)
        headings = [element["attrs"]["id"] for element in tree.tags("h2")]
        self.assertEqual(len(set(headings)), 2)
        self.assertIn('const literal = "$x$ <script>fixture()</script>";', tree.text_inside("code"))
        self.assertIn("literal $x$", tree.text_inside("code"))
        self.assertFalse(tree.tags("script"))
        self.assertFalse(tree.tags("svg"))
        external = next(element for element in tree.tags("a") if element["attrs"].get("href", "").startswith("https:"))
        self.assertEqual(external["attrs"]["href"], "https://example.invalid/docs?q=1")
        self.assertEqual(external["attrs"]["target"], "_blank")
        self.assertTrue({"noopener", "noreferrer"} <= set(external["attrs"]["rel"].split()))

    def test_inline_and_display_math_are_real_svg_not_raw_tex(self):
        result = self._render(r'''# 数学

行内 $x^2+1$ 与 \(\frac{1}{2}\)。

$$
\sum_{k=1}^{n} k = \frac{n(n+1)}{2}
$$

\[
\sqrt{9}=3
\]
''')
        tree = HTMLProbe(result["html"])
        self.assertEqual(len(tree.tags("svg")), 4)
        self.assertGreater(len(tree.tags("path")), 4)
        self.assertEqual(len([element for element in tree.elements if element["attrs"].get("class") == "mathInline"]), 2)
        self.assertEqual(len([element for element in tree.elements if element["attrs"].get("class") == "mathDisplay"]), 2)
        self.assertTrue(result["math_css"])
        self.assertNotIn("GUIDEMATHPLACEHOLDER", result["html"])
        self.assertNotIn('data-mml-node="merror"', result["html"])
        self.assertFalse(tree.tags("script"))
        common_formulas = [
            r"\begin{aligned}a+b&=c\\a&=c-b\end{aligned}",
            r"f(x)=\begin{cases}x^2,&x\ge0\\-x,&x<0\end{cases}",
            r"\begin{pmatrix}1&2\\3&4\end{pmatrix}",
            r"\int_0^1 x\,dx=\frac{1}{2}",
            r"x\in\mathbb{R},\quad\operatorname{rank}(A)=2",
        ]
        for latex in common_formulas:
            with self.subTest(latex=latex):
                compatible = self._render("# 常见公式\n\n$$\n" + latex + "\n$$\n")
                rendered = HTMLProbe(compatible["html"])
                self.assertEqual(len(rendered.tags("svg")), 1)
                self.assertTrue(rendered.tags("path"))
                self.assertNotIn('data-mml-node="merror"', compatible["html"])

    def test_local_images_and_allowed_downloads_use_versioned_asset_urls(self):
        files = {"图片/示意 图.png": png_pixel(0, 0, 255), "notes.txt": b"PUBLIC_NOTE",
                 "values.csv": b"x,y\n1,2\n", "worksheet.pdf": b"%PDF-1.4\n%public fixture\n"}
        for relative, content in files.items():
            self._asset_file(relative, content)
        result = self._render('![示意图](<图片/示意 图.png> "图片标题")\n\n'
                              '[文字](notes.txt) [表格](values.csv) [练习](worksheet.pdf)')
        tree = HTMLProbe(result["html"])
        urls = [element["attrs"].get("src", element["attrs"].get("href", ""))
                for element in tree.elements if element["tag"] in ("img", "a")]
        self.assertEqual(len(urls), 4)
        for url in urls:
            self.assertTrue(url.startswith(self.asset_base + "/"), url)
            parts = urlsplit(url)
            self.assertEqual(parse_qs(parts.query)["v"], [result["version"]])
            relative = unquote(parts.path.removeprefix("/guide-assets/"))
            self.assertEqual(self.reader.asset(relative).read_bytes(), files[relative])
        self.assertEqual(tree.tags("img")[0]["attrs"]["loading"], "lazy")

    def test_external_https_images_remain_valid_without_becoming_local_assets(self):
        result = self._render('![外部图](https://images.example.invalid/diagram.png?size=2)')
        image = HTMLProbe(result["html"]).tags("img")[0]
        self.assertEqual(image["attrs"]["src"], "https://images.example.invalid/diagram.png?size=2")
        self._missing_asset("diagram.png")

    def test_scripts_events_and_active_url_schemes_cannot_become_executable_html(self):
        result = self._render(r'''# 安全示例

<script>window.GUIDE_EXECUTED = true</script>
<img src="https://evil.invalid/raw.png" onerror="window.GUIDE_EXECUTED = true">
<iframe src="https://evil.invalid/frame"></iframe>

[脚本](javascript:alert%281%29)
[编码脚本](javascript&#58;alert%281%29)
[数据](data:text/html;base64,PHNjcmlwdD4=)
![数据图](data:image/png;base64,iVBORw0KGgo=)
![非安全图片](http://images.example.invalid/plain.png)
![协议相对](//images.example.invalid/plain.png)
![事件标题](https://images.example.invalid/safe.png "onerror=alert(1)")
''')
        tree = HTMLProbe(result["html"])
        self.assertFalse({element["tag"] for element in tree.elements} & {"script", "iframe", "object", "embed", "style"})
        for element in tree.elements:
            for name, value in element["attrs"].items():
                self.assertFalse(name.lower().startswith("on"), (name, value))
                if name in ("href", "src", "xlink:href") and value:
                    self.assertFalse(value.lower().startswith(("javascript:", "data:", "//")), value)
                    if element["tag"] == "img":
                        self.assertTrue(value.startswith("https:"), value)
        self.assertNotIn("https://evil.invalid/raw.png", [element["attrs"].get("src") for element in tree.tags("img")])

    def test_invalid_and_active_tex_fail_without_returning_stale_or_invented_body(self):
        invalid = [r'$\frac{1$', r'$\definitelyUnknownGuideCommand{a}$',
                   r'$\href{https://evil.invalid}{x}$', r'$\url{https://evil.invalid}$',
                   r'$\class{some-class}{x}$', r'$\style{color:red}{x}$',
                   r'$\htmlId{fixture}{x}$', r'$\require{html}$', r'$\includegraphics{photo.png}$',
                   r'$\color{url(https://evil.invalid/paint.svg#c)}x$']
        for expression in invalid:
            with self.subTest(expression=expression):
                self._render("# PREVIOUS_BODY\n\n![旧图](photo.png)")
                self.source.write_text("# INVALID_DOCUMENT\n\n" + expression, encoding="utf-8")
                result = self.reader.read(self.asset_base)
                active_attributes = [(element["tag"], name, value)
                                     for element in HTMLProbe(result["html"]).elements
                                     for name, value in element["attrs"].items()
                                     if name in {"href", "src", "xlink:href", "fill", "stroke", "style"}
                                     and value and "evil.invalid" in value]
                self.assertFalse(active_attributes, active_attributes)
                self._unavailable(result)
                self._missing_asset("photo.png")

    def test_empty_missing_oversized_and_invalid_utf8_sources_have_no_fake_body(self):
        self._unavailable(self.reader.read(self.asset_base))
        for empty in (b"", b"  \r\n\t", b"\xef\xbb\xbf\n"):
            with self.subTest(empty=empty):
                self.source.write_bytes(empty)
                result = self.reader.read(self.asset_base)
                self.assertEqual(result["status"], "empty", result)
                self.assertEqual(result["html"], "")
                self.assertEqual(result["math_css"], "")
                self.assertTrue(result["message"])
        for invalid in (b"\xff\xfeBROKEN_UTF8", b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(invalid)):
                self.source.write_bytes(invalid)
                self._unavailable(self.reader.read(self.asset_base))

    def test_markdown_hot_update_changes_version_even_with_original_mtime(self):
        first = self._render("# 第一版\n\n旧正文")
        before = self.source.stat()
        self.source.write_text("# 第二版\n\n新正文", encoding="utf-8")
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        second = self.reader.read(self.asset_base)
        self.assertEqual(second["status"], "ready")
        self.assertNotEqual(second["version"], first["version"])
        self.assertIn("第二版", second["html"])
        self.assertNotIn("旧正文", second["html"])

    def test_same_named_image_content_update_changes_version_even_with_same_size_and_mtime(self):
        red, blue = png_pixel(255, 0, 0), png_pixel(0, 0, 255)
        self.assertEqual(len(red), len(blue))
        image = self._asset_file("photo.png", red)
        first = self._render("# 图片\n\n![图片](photo.png)")
        before = image.stat()
        image.write_bytes(blue)
        os.utime(image, ns=(before.st_atime_ns, before.st_mtime_ns))
        second = self.reader.read(self.asset_base)
        self.assertNotEqual(second["version"], first["version"])
        self.assertIn(second["version"], second["html"])
        self.assertEqual(self.reader.asset("photo.png").read_bytes(), blue)

    def test_missing_image_arrival_and_reference_removal_update_access(self):
        first = self._render("# 待补图片\n\n![图片](photo.png)")
        self._missing_asset("photo.png")
        self._asset_file("photo.png")
        ready = self.reader.read(self.asset_base)
        self.assertNotEqual(ready["version"], first["version"])
        self.assertTrue(self.reader.asset("photo.png").is_file())
        self.source.write_text("# 已移除图片\n\n只保留正文。", encoding="utf-8")
        # Asset requests must re-read the source even before the page refreshes.
        self._missing_asset("photo.png")
        updated = self.reader.read(self.asset_base)
        self.assertNotEqual(updated["version"], ready["version"])
        self.assertNotIn("photo.png", updated["html"])

    def test_restoring_identical_source_after_read_failure_restores_its_assets(self):
        self._asset_file("photo.png")
        original = "# 可恢复说明\n\n![图片](photo.png)"
        self._render(original)
        self.assertTrue(self.reader.asset("photo.png").is_file())
        self.source.unlink()
        self._unavailable(self.reader.read(self.asset_base))
        self._missing_asset("photo.png")
        self.source.write_text(original, encoding="utf-8")
        restored = self.reader.read(self.asset_base)
        self.assertEqual(restored["status"], "ready", restored)
        self.assertTrue(self.reader.asset("photo.png").is_file())

    def test_only_referenced_allowed_files_are_exposed(self):
        self._asset_file("public.png")
        for name in ("unreferenced.txt", ".env", "settings.json", "learning.sqlite3", "secret.key", "guide.svg", "page.html", "script.js"):
            self._asset_file(name, b"PRIVATE_FIXTURE_ONLY")
        self._render("# 只公开图片\n\n![图片](public.png)\n\n"
                     "[环境](.env) [配置](settings.json) [脚本](script.js) [页面](page.html) ![矢量](guide.svg)")
        self.assertEqual(self.reader.asset("public.png"), (self.guide_root / "public.png").resolve())
        for name in ("unreferenced.txt", ".env", "settings.json", "learning.sqlite3", "secret.key", "guide.svg", "page.html", "script.js", "guide.md"):
            with self.subTest(name=name):
                self._missing_asset(name)

    def test_source_itself_cannot_be_downloaded_even_when_it_has_an_allowed_suffix(self):
        text_source = self.guide_root / "source.txt"
        text_source.write_text("# 原稿不作为资源\n\n[原稿](source.txt)", encoding="utf-8")
        reader = GuideReader(text_source)
        self.assertEqual(reader.read(self.asset_base)["status"], "ready")
        self._missing_asset("source.txt", reader)

    def test_path_traversal_encoded_paths_and_absolute_paths_are_rejected(self):
        self._asset_file("images/safe.png")
        outside = self.root / "outside.png"
        outside.write_bytes(png_pixel(0, 255, 0))
        paths = ["../outside.png", "%2e%2e/outside.png", "%2E%2E%2Foutside.png",
                 "images/../../outside.png", "images/%2e%2e/%2e%2e/outside.png",
                 "/outside.png", "%2Foutside.png", "C:/outside.png", "C:%5Coutside.png",
                 "file:///C:/outside.png", "//host/share/outside.png", "..%5Coutside.png"]
        markdown = "# 路径\n\n![允许](images/safe.png)\n\n" + "\n".join(f"![拒绝]({path})" for path in paths)
        result = self._render(markdown)
        published = [element["attrs"].get("src") for element in HTMLProbe(result["html"]).tags("img")]
        published = [value for value in published if value]
        self.assertEqual(len(published), 1, published)
        self.assertIn("/images/safe.png?", published[0])
        for path in [*paths, str(outside), "%252e%252e/outside.png"]:
            with self.subTest(path=path):
                self._missing_asset(path)

    def test_symlink_or_windows_junction_cannot_expose_assets_outside_guide_root(self):
        outside = self.root / "private-directory"
        outside.mkdir()
        (outside / "outside.png").write_bytes(png_pixel(0, 255, 0))
        link = self.guide_root / "linked-directory"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            # Directory junctions provide the equivalent Windows reparse-point
            # escape when the host does not permit unprivileged symlinks.
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                                    capture_output=True, text=True, timeout=10,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertFalse((link / "outside.png").resolve().is_relative_to(self.guide_root.resolve()))
        self._render("# 禁止越界\n\n![外部目录](linked-directory/outside.png)")
        self._missing_asset("linked-directory/outside.png")

    def test_oversized_asset_is_not_available(self):
        large = self.guide_root / "large.png"
        with large.open("wb") as stream:
            stream.truncate(GuideReader.MAX_ASSET_BYTES + 1)
        self._render("# 图片上限\n\n![过大图片](large.png)")
        self._missing_asset("large.png")

    def test_rendering_and_asset_reads_do_not_modify_source_or_assets(self):
        self._asset_file("photo.png")
        self.source.write_text("# 只读测试\n\n![图片](photo.png)\n\n$x+1$", encoding="utf-8")
        files = [self.source, self.guide_root / "photo.png"]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
        result = self.reader.read(self.asset_base)
        self.assertEqual(result["status"], "ready", result)
        self.reader.asset("photo.png").read_bytes()
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}, before)
        self.assertEqual(set(self.guide_root.iterdir()), set(files))


class GuideHTTPAcceptanceTests(GuideFixture):
    public_origin = "https://fixture.trycloudflare.com"
    github_origin = "https://guide-acceptance.github.io"

    def setUp(self):
        super().setUp()
        self.picture = self._asset_file("图片/photo.png")
        self.note = self._asset_file("note.txt", b"PUBLIC_GUIDE_DOWNLOAD")
        self._asset_file("private.txt", b"PRIVATE_FIXTURE_ONLY")
        self.source.write_text("# GUIDE_PRIVATE_BODY\n\n![图片](图片/photo.png)\n\n[附件](note.txt)\n\n$x+1$", encoding="utf-8")
        self.data_dir = self.root / "data"
        self.service = LearningService(synthetic_course(), self.data_dir, DeterministicJudge())
        self.settings = Settings(data_dir=self.data_dir, port=8767, guide_path=self.source,
                                 allowed_origins=(self.github_origin,), public_api_origin=self.public_origin)
        self.app = create_app(self.settings, self.service)
        self.client = TestClient(self.app, base_url=self.public_origin)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _headers(self, identity=None):
        headers = {"Origin": self.github_origin}
        if identity is not None:
            headers["X-Learning-Identity"] = identity
        return headers

    def _guest(self):
        response = self.client.post("/api/access/guest", json={}, headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["access"]["role"], "guest")
        return response.json()["identity_token"]

    def _rows(self, query):
        with closing(sqlite3.connect(self.service.db_path)) as con:
            return con.execute(query).fetchall()

    def test_public_guide_requires_identity_and_retains_origin_host_boundaries(self):
        for identity, code in [(None, "identity_required"), ("forged-guide-token", "invalid_identity")]:
            response = self.client.get("/api/guide", headers=self._headers(identity))
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json()["detail"]["code"], code)
            self.assertNotIn("GUIDE_PRIVATE_BODY", response.text)
        identity = self._guest()
        response = self.client.get("/api/guide", headers=self._headers(identity))
        self.assertEqual(response.status_code, 200, response.text)
        guide = response.json()
        self.assertEqual(guide["status"], "ready", guide)
        self.assertIn("GUIDE_PRIVATE_BODY", guide["html"])
        self.assertIn("<svg", guide["html"])
        self.assertIn(self.public_origin + "/guide-assets/", guide["html"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        for headers, status, code in [
            ({**self._headers(identity), "Origin": "https://attacker.invalid"}, 403, "origin_forbidden"),
            ({**self._headers(identity), "Host": "attacker.invalid", "X-Forwarded-Host": "fixture.trycloudflare.com"}, 400, "invalid_host"),
        ]:
            rejected = self.client.get("/api/guide", headers=headers)
            self.assertEqual(rejected.status_code, status, rejected.text)
            self.assertEqual(rejected.json()["detail"]["code"], code)
        self.assertEqual(self._rows("SELECT role, COUNT(*) FROM access_principals GROUP BY role"), [("guest", 1)])

    def test_referenced_assets_are_public_read_only_and_do_not_create_identities(self):
        before_topics = self._rows("SELECT learner, topic, body FROM topics")
        self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), [(0,)])
        response = self.client.get("/guide-assets/%E5%9B%BE%E7%89%87/photo.png")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.picture.read_bytes())
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(response.headers["Content-Disposition"].startswith("inline;"))
        response = self.client.get("/guide-assets/note.txt", headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.note.read_bytes())
        self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.github_origin)
        before = self.picture.read_bytes()
        for method in ("POST", "PUT", "DELETE"):
            rejected = self.client.request(method, "/guide-assets/%E5%9B%BE%E7%89%87/photo.png", content=b"overwrite")
            self.assertIn(rejected.status_code, {404, 405}, rejected.text)
        self.assertEqual(self.picture.read_bytes(), before)
        self.assertEqual(self._rows("SELECT COUNT(*) FROM access_principals"), [(0,)])
        self.assertEqual(self._rows("SELECT learner, topic, body FROM topics"), before_topics)

    def test_asset_http_access_revokes_removed_references_and_denies_private_paths(self):
        response = self.client.get("/guide-assets/%E5%9B%BE%E7%89%87/photo.png?v=old-version")
        self.assertEqual(response.status_code, 200, response.text)
        self.source.write_text("# 文档已更新\n\n不再公开任何附件。", encoding="utf-8")
        for path in ("%E5%9B%BE%E7%89%87/photo.png?v=old-version", "note.txt", "private.txt", "guide.md",
                     "%2e%2e/private.txt", "%252e%252e/private.txt", "%2Fprivate.txt", "C:%5Cprivate.txt"):
            with self.subTest(path=path):
                response = self.client.get("/guide-assets/" + path)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertNotIn("PRIVATE_FIXTURE_ONLY", response.text)
                self.assertNotIn("PUBLIC_GUIDE_DOWNLOAD", response.text)

    def test_guide_and_same_named_image_updates_are_visible_without_restarting_app(self):
        identity = self._guest()
        first = self.client.get("/api/guide", headers=self._headers(identity)).json()
        self.assertEqual(first["status"], "ready", first)
        old = self.picture.stat()
        replacement = png_pixel(0, 0, 255)
        self.picture.write_bytes(replacement)
        os.utime(self.picture, ns=(old.st_atime_ns, old.st_mtime_ns))
        second = self.client.get("/api/guide", headers=self._headers(identity)).json()
        self.assertNotEqual(second["version"], first["version"])
        response = self.client.get("/guide-assets/%E5%9B%BE%E7%89%87/photo.png?v=" + first["version"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, replacement)
        self.source.write_text("# UPDATED_GUIDE_BODY\n\n正文已更新。", encoding="utf-8")
        third = self.client.get("/api/guide", headers=self._headers(identity)).json()
        self.assertNotEqual(third["version"], second["version"])
        self.assertIn("UPDATED_GUIDE_BODY", third["html"])
        self.assertNotIn("GUIDE_PRIVATE_BODY", third["html"])


if __name__ == "__main__":
    unittest.main()
