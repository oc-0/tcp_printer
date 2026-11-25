import tempfile
import unittest
from pathlib import Path

from app.storage_preview import preview_kind, read_text_preview
from pathlib import Path


class StoragePreviewTests(unittest.TestCase):
    def test_preview_kind_supports_requested_formats_case_insensitively(self):
        expected = {
            "report.PDF": "pdf",
            "notes.docx": "word",
            "legacy.DOC": "word",
            "readme.md": "text",
            "notes.txt": "text",
            "diagram.PNG": "image",
            "photo.jpg": "image",
        }
        for filename, kind in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(preview_kind(filename), kind)

    def test_preview_kind_rejects_arbitrary_and_active_image_formats(self):
        self.assertIsNone(preview_kind("archive.zip"))
        self.assertIsNone(preview_kind("active.svg"))
        self.assertIsNone(preview_kind("drawing.sldprt"))

    def test_text_preview_decodes_utf8_bom_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.md"
            path.write_bytes(b"\xef\xbb\xbfhello world")
            text, truncated = read_text_preview(path, max_bytes=5)
        self.assertEqual(text, "hello")
        self.assertTrue(truncated)

    def test_text_preview_preserves_complete_short_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_bytes("资料内容".encode("utf-8"))
            text, truncated = read_text_preview(path)
        self.assertEqual(text, "资料内容")
        self.assertFalse(truncated)

    def test_pdfjs_temporary_word_previews_are_deleted_after_viewing(self):
        viewer_script = Path(__file__).parents[1] / "app" / "static" / "vendor" / "pdfjs" / "viewer.js"
        source = viewer_script.read_text(encoding="utf-8")
        storage_script = (Path(__file__).parents[1] / "app" / "static" / "storage-files.js").read_text(encoding="utf-8")
        self.assertIn('params.get("disableRange") === "true"', source)
        self.assertIn("disableRange,", source)
        self.assertIn("disableStream: disableRange", source)
        self.assertIn("URL.createObjectURL(pdf)", storage_script)
        self.assertIn("URL.revokeObjectURL(previewObjectUrl)", storage_script)
        self.assertIn('method: "DELETE"', storage_script)
        self.assertIn('response.headers.get("X-Preview-Id")', storage_script)
        self.assertNotIn("storage-preview-copy", storage_script)
        self.assertNotIn("复制全文", storage_script)
        self.assertIn('"X-Preview-Id": preview_id', (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8"))
        self.assertIn("正在启动 Word 并转换文件", storage_script)
        self.assertIn("正在接收 PDF", storage_script)
        self.assertIn("正在加载预览", storage_script)
        self.assertIn("response.body.getReader()", storage_script)
        self.assertIn("uploadWithRetry", storage_script)
        self.assertIn("失败 ${failed.length} 个", storage_script)
        self.assertIn("正在重试", storage_script)


if __name__ == "__main__":
    unittest.main()
