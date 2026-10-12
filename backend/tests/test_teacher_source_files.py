import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.teacher_source_files import create_teacher_source_files_router


class TeacherSourceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        teacher = self.root / "黄平老师"
        nested = teacher / "2025年"
        nested.mkdir(parents=True)
        (nested / "原始记录.xlsx").write_bytes(b"spreadsheet")
        (teacher / "照片.jpg").write_bytes(b"image")
        (self.root / "outputs").mkdir()
        (self.root / "outputs" / "private.txt").write_text("excluded")
        self.env = patch.dict(os.environ, {"YNAAS_TEACHER_FILES_DIR": str(self.root)})
        self.env.start()
        app = FastAPI()
        app.include_router(create_teacher_source_files_router(lambda: {"role": "researcher"}))
        self.client = TestClient(app)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_browse_only_teacher_folders_and_metadata(self):
        root = self.client.get("/api/research/teacher-source-files/browse")
        self.assertEqual(root.status_code, 200)
        self.assertEqual([item["name"] for item in root.json()["entries"]], ["黄平老师"])
        listing = self.client.get("/api/research/teacher-source-files/browse", params={"path": "黄平老师/2025年"})
        item = listing.json()["entries"][0]
        self.assertEqual(item["name"], "原始记录.xlsx")
        self.assertEqual(item["size_bytes"], len(b"spreadsheet"))
        self.assertNotIn("spreadsheet", str(listing.json()))

    def test_download_and_inline_preview(self):
        excel = self.client.get("/api/research/teacher-source-files/file", params={"path": "黄平老师/2025年/原始记录.xlsx"})
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(excel.content, b"spreadsheet")
        self.assertIn("attachment", excel.headers["content-disposition"])
        self.assertEqual(excel.headers["cache-control"], "private, no-store")
        image = self.client.get("/api/research/teacher-source-files/file", params={"path": "黄平老师/照片.jpg"})
        self.assertEqual(image.status_code, 200)
        self.assertIn("inline", image.headers["content-disposition"])

    def test_reject_outside_paths_and_symlinks(self):
        for path in ("../outputs", "outputs/private.txt", "黄平老师/../../outputs/private.txt", "黄平老师\\2025年"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/api/research/teacher-source-files/file", params={"path": path}).status_code, 400)
        link = self.root / "黄平老师" / "outside.txt"
        try:
            link.symlink_to(self.root / "outputs" / "private.txt")
        except (OSError, NotImplementedError):
            return
        self.assertEqual(self.client.get("/api/research/teacher-source-files/file", params={"path": "黄平老师/outside.txt"}).status_code, 403)

    def test_authorization_required(self):
        app = FastAPI()
        def denied():
            raise HTTPException(403, "无权限")
        app.include_router(create_teacher_source_files_router(denied))
        client = TestClient(app)
        self.assertEqual(client.get("/api/research/teacher-source-files/browse").status_code, 403)
        self.assertEqual(client.get("/api/research/teacher-source-files/file", params={"path": "黄平老师/照片.jpg"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
