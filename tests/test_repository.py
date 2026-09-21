import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.repository import AuthenticationError, Repository, RepositoryError


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.repository = Repository(root / "repository.db", root / "repository")
        self.owner = self.repository.create_user("张工", "20240001")
        self.other = self.repository.create_user("李同学", "20240002")

    def tearDown(self):
        self.directory.cleanup()

    def test_initial_password_requires_change_and_new_password_works(self):
        token, user = self.repository.login("20240001", "111111")
        self.assertTrue(user["must_change_password"])
        changed = self.repository.change_password(user["id"], "111111", "new-password")
        self.assertFalse(changed["must_change_password"])
        self.repository.revoke_session(token)
        with self.assertRaises(AuthenticationError):
            self.repository.login("20240001", "111111")
        _, logged_in = self.repository.login("20240001", "new-password")
        self.assertEqual(logged_in["student_id"], "20240001")

    def test_backdoor_user_is_created_with_a_password(self):
        user = self.repository.ensure_backdoor_user("90000000", "backdoor-pass")
        self.assertEqual(user["student_id"], "90000000")
        self.assertFalse(user["must_change_password"])
        with self.assertRaises(AuthenticationError):
            self.repository.login("90000000", "wrong-password")
        _, logged_in = self.repository.login("90000000", "backdoor-pass")
        self.assertEqual(logged_in["student_id"], "90000000")

    def test_backdoor_password_is_synchronized(self):
        self.repository.ensure_backdoor_user("90000000", "first-pass")
        self.repository.ensure_backdoor_user("90000000", "second-pass")
        with self.assertRaises(AuthenticationError):
            self.repository.login("90000000", "first-pass")
        _, logged_in = self.repository.login("90000000", "second-pass")
        self.assertEqual(logged_in["student_id"], "90000000")

    def test_wrong_password_is_rejected_and_disabled_account_cannot_login(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self.repository.login("20240001", "wrong")
        with self.assertRaisesRegex(AuthenticationError, "次数过多"):
            self.repository.login("20240001", "111111")
        self.repository.set_user_active(self.other["id"], False)
        with self.assertRaisesRegex(AuthenticationError, "禁用"):
            self.repository.login("20240002", "111111")

    def test_upload_search_metadata_and_shared_permissions(self):
        root = Path(self.directory.name)
        source = root / "manual.docx"
        source.write_bytes(b"docx data")
        metadata = {"description": "机械设计说明", "tags": ["设计", "文档"]}
        item = self.repository.save_uploaded_file(source, "manual.docx", self.owner["id"], metadata, 1024 * 1024)
        self.assertNotIn("project_name", item)
        self.assertEqual(item["owner_name"], "张工")
        self.assertEqual(item["owner_student_id"], "20240001")
        self.assertEqual(self.repository.list_files(self.owner["id"], query="机械")[0]["id"], item["id"])
        self.repository.update_metadata(item["id"], self.other["id"], {"description": "updated", "tags": ["最新版"]})
        self.assertEqual(self.repository.get_file(item["id"], self.owner["id"])["tags"], ["最新版"])
        self.repository.soft_delete(item["id"], self.other["id"])
        self.assertIsNone(self.repository.get_file(item["id"], self.owner["id"]))

    def test_zip_rejects_path_traversal_and_accepts_safe_entries(self):
        root = Path(self.directory.name)
        unsafe = root / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        with self.assertRaisesRegex(RepositoryError, "不安全"):
            self.repository.save_uploaded_file(unsafe, "unsafe.zip", self.owner["id"], {}, 1024 * 1024)

        safe = root / "safe.zip"
        with zipfile.ZipFile(safe, "w") as archive:
            archive.writestr("assembly/part.txt", "ok")
        item = self.repository.save_uploaded_file(safe, "assembly.zip", self.owner["id"], {}, 1024 * 1024)
        self.assertEqual(item["extension"], ".zip")

    def test_versions_and_groups_are_isolated(self):
        root = Path(self.directory.name)
        group = self.repository.create_group("竞赛资料")
        renamed = self.repository.update_group(group["id"], "竞赛归档")
        self.assertEqual(renamed["name"], "竞赛归档")
        source = root / "v1.pdf"
        source.write_bytes(b"%PDF-1.7\nversion one")
        item = self.repository.save_uploaded_file(source, "report.pdf", self.owner["id"], {"group_id": group["id"]}, 1024 * 1024)
        self.repository.update_metadata(item["id"], self.owner["id"], {"title": "管理员更新", "tags": ["已审核"]}, is_admin=True)
        self.assertEqual(self.repository.get_file(item["id"], self.owner["id"])["group_id"], group["id"])
        source2 = root / "v2.pdf"
        source2.write_bytes(b"%PDF-1.7\nversion two")
        updated = self.repository.add_version(item["id"], self.owner["id"], source2, "report.pdf", "2.0", "修订公式", 1024 * 1024)
        self.assertEqual(updated["version"], "2.0")
        self.assertEqual(len(self.repository.list_versions(item["id"], self.owner["id"])), 2)
        self.repository.delete_group(group["id"])
        self.assertIsNone(self.repository.get_file(item["id"], self.owner["id"])["group_id"])

    def test_multiple_files_can_share_a_folder_and_be_filtered(self):
        root = Path(self.directory.name)
        folder = self.repository.create_group("课程资料")
        first = root / "first.pdf"
        second = root / "second.pdf"
        first.write_bytes(b"%PDF-1.7\nfirst")
        second.write_bytes(b"%PDF-1.7\nsecond")
        self.repository.save_uploaded_file(first, "first.pdf", self.owner["id"], {"group_id": folder["id"]}, 1024 * 1024)
        self.repository.save_uploaded_file(second, "second.pdf", self.owner["id"], {"group_id": folder["id"]}, 1024 * 1024)
        files = self.repository.list_files(self.owner["id"], group_id=folder["id"])
        self.assertEqual({item["original_name"] for item in files}, {"first.pdf", "second.pdf"})
        nested_search = self.repository.list_items(self.owner["id"], query="second")
        self.assertEqual([item["original_name"] for item in nested_search if item["type"] == "file"], ["second.pdf"])

    def test_folder_metadata_and_unified_items(self):
        folder = self.repository.create_folder("硬件资料", description="硬件说明", tags=["硬件", "规范"])
        self.assertEqual(folder["type"], "folder")
        self.assertEqual(set(folder["tags"]), {"规范", "硬件"})
        updated = self.repository.update_folder(folder["id"], "硬件归档", description="归档说明", tags=["归档"])
        self.assertEqual(updated["description"], "归档说明")
        self.assertEqual(updated["tags"], ["归档"])
        root_items = self.repository.list_items(self.owner["id"])
        self.assertEqual(root_items[0]["type"], "folder")
        self.assertEqual(root_items[0]["name"], "硬件归档")

    def test_folder_delete_hides_contents_until_restore(self):
        root = Path(self.directory.name)
        folder = self.repository.create_folder("临时资料")
        source = root / "inside.pdf"
        source.write_bytes(b"%PDF-1.7\ninside")
        item = self.repository.save_uploaded_file(source, "inside.pdf", self.owner["id"], {"group_id": folder["id"]}, None)
        self.repository.delete_folder(folder["id"])
        self.assertEqual(self.repository.list_items(self.owner["id"]), [])
        self.assertEqual(self.repository.list_folders(), [])
        self.assertTrue(self.repository.list_all_files()[0]["deleted_at"])
        self.repository.restore_folder(folder["id"])
        restored = self.repository.list_items(self.owner["id"])
        self.assertEqual(restored[0]["id"], folder["id"])
        restored_contents = self.repository.list_items(self.owner["id"], parent_id=folder["id"])
        self.assertEqual(restored_contents[0]["id"], item["id"])

    def test_deleted_file_is_kept_until_retention_cutoff_then_purged(self):
        root = Path(self.directory.name)
        source = root / "delete-me.pdf"
        source.write_bytes(b"%PDF-1.7\ndelete me")
        item = self.repository.save_uploaded_file(source, "delete-me.pdf", self.owner["id"], {}, 1024 * 1024)
        self.repository.soft_delete(item["id"], self.owner["id"])
        self.assertEqual(len(self.repository.list_all_files()), 1)
        with self.repository._connection() as connection:
            connection.execute("UPDATE files SET deleted_at = ? WHERE id = ?", ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), item["id"]))
        self.assertEqual(self.repository.purge_deleted(datetime.now(timezone.utc) - timedelta(days=7)), 1)
        self.assertEqual(self.repository.list_all_files(), [])
        self.assertFalse(any((self.repository.originals).iterdir()))

    def test_admin_can_permanently_delete_deleted_file(self):
        root = Path(self.directory.name)
        source = root / "purge-me.pdf"
        source.write_bytes(b"%PDF-1.7\npurge me")
        item = self.repository.save_uploaded_file(source, "purge-me.pdf", self.owner["id"], {}, None)
        self.repository.soft_delete(item["id"], self.owner["id"])
        self.repository.permanently_delete_file(item["id"])
        self.assertEqual(self.repository.list_all_files(), [])
        self.assertFalse(any(self.repository.originals.iterdir()))

    def test_admin_can_permanently_delete_deleted_folder_tree(self):
        root = Path(self.directory.name)
        folder = self.repository.create_folder("purge-folder")
        child = self.repository.create_folder("child", parent_id=folder["id"])
        source = root / "inside.pdf"
        source.write_bytes(b"%PDF-1.7\ninside")
        item = self.repository.save_uploaded_file(source, "inside.pdf", self.owner["id"], {"group_id": child["id"]}, None)
        stored_path, _ = self.repository.storage_path(item["id"], self.owner["id"])
        self.repository.delete_folder(folder["id"])
        self.repository.permanently_delete_folder(folder["id"])
        self.assertEqual(self.repository.list_all_files(), [])
        self.assertEqual(self.repository.list_folders(include_deleted=True), [])
        self.assertFalse(stored_path.exists())

    def test_folder_archive_preserves_nested_structure(self):
        root = Path(self.directory.name)
        folder = self.repository.create_folder("课程资料")
        child = self.repository.create_folder("第一章", parent_id=folder["id"])
        top_source = root / "overview.pdf"
        child_source = root / "formula.docx"
        top_source.write_bytes(b"%PDF-1.7\noverview")
        child_source.write_bytes(b"PK\x03\x04docx")
        self.repository.save_uploaded_file(top_source, "overview.pdf", self.owner["id"], {"group_id": folder["id"]}, None)
        self.repository.save_uploaded_file(child_source, "formula.docx", self.owner["id"], {"group_id": child["id"]}, None)
        archive_path = root / "course.zip"
        archive_name = self.repository.create_folder_archive(folder["id"], archive_path)
        self.assertEqual(archive_name, "课程资料")
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {"课程资料/", "课程资料/第一章/", "课程资料/overview.pdf", "课程资料/第一章/formula.docx"})

    def test_repository_stats_activity_and_account_delete(self):
        stats = self.repository.repository_stats()
        self.assertEqual(stats["total_files"], 0)
        self.assertEqual(stats["active_files"], 0)
        self.assertEqual(stats["folders"], 0)
        self.repository.delete_user(self.other["id"])
        self.assertIsNone(self.repository.get_user(self.other["id"]))


if __name__ == "__main__":
    unittest.main()
