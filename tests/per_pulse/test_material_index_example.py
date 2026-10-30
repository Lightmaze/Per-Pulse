"""Exercise the runnable material-index example with hostile and multilingual input.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from html.parser import HTMLParser
import unittest

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "examples/material-index/build_index.py"
_spec = importlib.util.spec_from_file_location("material_index_example", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
render = _module.render


class Document(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.links = []
        self.tags = []
        self.text = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "a":
            self.links.append(dict(attrs)["href"])

    def handle_data(self, text):
        self.text.append(text)


class IndexTests(unittest.TestCase):
    def test_imported_markup_and_active_urls_stay_literal(self):
        urls = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<h1>hello</h1>",
                "java\nscript:alert(1)", "//example.org", "https://user:pass@example.org",
                "https://example.org:99999", "https://[broken", "https://example.org\\other"]
        label = '<img src=x onerror=alert(1)> & "quoted"'
        doc = Document(render([{"title": label, "url": url, "note": "<script>1</script>"} for url in urls]))
        self.assertEqual(len(doc.links), 2)  # Only the fixed method references.
        self.assertNotIn("img", doc.tags)
        self.assertNotIn("script", doc.tags)
        self.assertEqual("".join(doc.text).count(label), len(urls))

    def test_a_different_material_set_keeps_unicode_queries_and_literal_notes(self):
        url = 'https://example.org/search?q=%E6%9D%90%E6%96%99&tag="notes"'
        note = "Straße / 材料 / e\u0301 / 🧭\nA < B & C > D"
        doc = Document(render([{"title": "Field notes", "url": url, "note": note}]))
        self.assertEqual(doc.links[0], url)
        self.assertIn(note, "".join(doc.text))
        self.assertIn("No materials yet.", render([]))

    def test_invalid_material_is_rejected_before_output(self):
        for value in [{}, [None], [{"title": ""}], [{"title": "X", "note": 12}]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                render(value)


if __name__ == "__main__":
    unittest.main()
