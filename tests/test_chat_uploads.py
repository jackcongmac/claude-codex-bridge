import importlib
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


PNG_BYTES = b"\x89PNG\r\n\x1a\npayload"
GIF_BYTES = b"GIF89apayload"


class ChatUploadsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.uploads = importlib.import_module("_chat_uploads")
        except ModuleNotFoundError:
            self.fail("scripts/_chat_uploads.py is missing")

        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = pathlib.Path(self.tmpdir.name)
        self.collab = self.project / ".collab"
        self.collab.mkdir()
        (self.collab / "collaboration_signal.json").write_text(
            json.dumps({"update_id": 0})
        )
        (self.collab / "collaboration.md").write_text("# Board\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_save_png_persists_under_chat_uploads_and_loads_with_content_type(self):
        ref = self.uploads.save_image(str(self.project), PNG_BYTES)

        self.assertRegex(ref, r"^[0-9a-f]{32}\.png$")
        path = self.collab / "chat_uploads" / ref
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), PNG_BYTES)
        self.assertEqual(
            self.uploads.load_image(str(self.project), ref),
            (PNG_BYTES, "image/png"),
        )

    def test_save_uses_sniffed_bytes_when_content_type_hint_lies(self):
        ref = self.uploads.save_image(
            str(self.project), GIF_BYTES, content_type="image/png"
        )

        self.assertRegex(ref, r"^[0-9a-f]{32}\.gif$")
        self.assertEqual(
            self.uploads.load_image(str(self.project), ref),
            (GIF_BYTES, "image/gif"),
        )

    def test_save_rejects_empty_bytes(self):
        with self.assertRaises(ValueError):
            self.uploads.save_image(str(self.project), b"")

    def test_save_rejects_bytes_over_max_upload_size(self):
        too_large = (
            b"\x89PNG\r\n\x1a\n"
            + b"x" * (self.uploads.MAX_UPLOAD_BYTES + 1 - 8)
        )

        with self.assertRaises(ValueError):
            self.uploads.save_image(str(self.project), too_large)

    def test_save_rejects_non_image_bytes_even_with_allowed_hint(self):
        with self.assertRaises(ValueError):
            self.uploads.save_image(
                str(self.project), b"<script>not an image", content_type="image/png"
            )

    def test_load_rejects_unsafe_or_bad_ids(self):
        for image_id in ("../secret", "/etc/passwd", "abcd.txt", "nope"):
            with self.subTest(image_id=image_id):
                self.assertIsNone(self.uploads.load_image(str(self.project), image_id))

    def test_load_returns_none_for_missing_well_formed_id(self):
        self.assertIsNone(
            self.uploads.load_image(str(self.project), "01234567.png")
        )


if __name__ == "__main__":
    unittest.main()
