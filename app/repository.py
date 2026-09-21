"""Storage repository data and authorization layer.

The repository deliberately uses its own SQLite database and root directory so
storage files cannot be removed by the printer queue cleanup routine.
"""

from __future__ import annotations

import hashlib
import mimetypes
import secrets
import sqlite3
import stat
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional
from uuid import uuid4

from .passwords import hash_password, verify_password


INITIAL_PASSWORD = "111111"
MAX_LOGIN_FAILURES = 5
LOCKOUT_MINUTES = 15
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".zip"}
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


class RepositoryError(Exception):
    """Expected repository validation or authorization error."""


class AuthenticationError(RepositoryError):
    pass


class Repository:
    def __init__(self, database_path: Path, root: Path):
        self.database_path = Path(database_path)
        self.root = Path(root)
        self.originals = self.root / "originals"
        self.derived = self.root / "derived"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.originals.mkdir(parents=True, exist_ok=True)
        self.derived.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    student_id TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    last_login_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_id TEXT REFERENCES groups(id) ON DELETE SET NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT,
                    UNIQUE(parent_id, name)
                );
                CREATE TABLE IF NOT EXISTS tags (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL REFERENCES users(id),
                    original_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    project_name TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL DEFAULT '1.0',
                    version_note TEXT NOT NULL DEFAULT '',
                    group_id TEXT REFERENCES groups(id) ON DELETE SET NULL,
                    visibility TEXT NOT NULL DEFAULT 'team',
                    extension TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS file_versions (
                    id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    original_name TEXT NOT NULL,
                    storage_name TEXT NOT NULL UNIQUE,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    version_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(file_id, version_number)
                );
                CREATE TABLE IF NOT EXISTS file_tags (
                    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY(file_id, tag_id)
                );
                CREATE TABLE IF NOT EXISTS folder_tags (
                    folder_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY(folder_id, tag_id)
                );
                CREATE TABLE IF NOT EXISTS zip_entries (
                    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    entry_name TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    PRIMARY KEY(file_id, entry_name)
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    file_id TEXT,
                    target_user_id TEXT,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner_id);
                CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(deleted_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
                """
            )
            group_columns = {row[1] for row in connection.execute("PRAGMA table_info(groups)").fetchall()}
            user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
            if "is_admin" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            if "description" not in group_columns:
                connection.execute("ALTER TABLE groups ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "updated_at" not in group_columns:
                connection.execute("ALTER TABLE groups ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            if "deleted_at" not in group_columns:
                connection.execute("ALTER TABLE groups ADD COLUMN deleted_at TEXT")
            connection.execute("UPDATE groups SET updated_at = created_at WHERE updated_at = '' OR updated_at IS NULL")

    @staticmethod
    def public_user(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "student_id": row["student_id"],
            "status": "启用" if row["is_active"] else "已禁用",
            "is_admin": bool(row["is_admin"]),
            "must_change_password": bool(row["must_change_password"]),
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }

    def create_user(self, name: str, student_id: str, password: str = INITIAL_PASSWORD) -> dict:
        name = name.strip()
        student_id = student_id.strip()
        if not name or not student_id:
            raise RepositoryError("姓名和学号不能为空。")
        if len(password) < 6:
            raise RepositoryError("密码至少需要 6 位。")
        now = iso_now()
        user_id = uuid4().hex
        password_hash = hash_password(password)
        try:
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO users (id, name, student_id, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, name, student_id, password_hash, now, now),
                )
        except sqlite3.IntegrityError as error:
            raise RepositoryError("学号已存在。") from error
        return self.get_user(user_id)

    def ensure_backdoor_user(self, student_id: str, password: str) -> Optional[dict]:
        """Create or synchronize the environment-configured hidden admin account."""
        student_id = student_id.strip()
        if not student_id:
            return None
        if len(password) < 6:
            raise RepositoryError("TCP_PRINTER_ADMIN_PASSWORD must contain at least 6 characters.")
        now = iso_now()
        password_hash = hash_password(password)
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE student_id = ?", (student_id,)).fetchone()
            if row:
                connection.execute(
                    "UPDATE users SET password_hash = ?, is_active = 1, is_admin = 0, must_change_password = 0, failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
                    (password_hash, now, row["id"]),
                )
                user_id = row["id"]
            else:
                user_id = uuid4().hex
                connection.execute(
                    "INSERT INTO users (id, name, student_id, password_hash, is_active, is_admin, must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 0, 0, ?, ?)",
                    (user_id, "Backdoor Admin", student_id, password_hash, now, now),
                )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> Optional[dict]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self.public_user(row) if row else None

    def get_user_by_student_id(self, student_id: str) -> Optional[sqlite3.Row]:
        with self._connection() as connection:
            return connection.execute("SELECT * FROM users WHERE student_id = ?", (student_id.strip(),)).fetchone()

    def list_users(self) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE, student_id").fetchall()
        return [self.public_user(row) for row in rows]

    def set_user_active(self, user_id: str, active: bool) -> dict:
        now = iso_now()
        with self._connection() as connection:
            cursor = connection.execute("UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?", (int(active), now, user_id))
            if cursor.rowcount != 1:
                raise RepositoryError("账号不存在。")
            if not active:
                connection.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, user_id))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self.public_user(row)

    def set_user_admin(self, user_id: str, is_admin: bool) -> dict:
        now = iso_now()
        with self._connection() as connection:
            cursor = connection.execute("UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?", (int(is_admin), now, user_id))
            if cursor.rowcount != 1:
                raise RepositoryError("账号不存在。")
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self.public_user(row)

    def reset_password(self, user_id: str) -> dict:
        now = iso_now()
        password_hash = hash_password(INITIAL_PASSWORD)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
                (password_hash, now, user_id),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("账号不存在。")
            connection.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, user_id))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self.public_user(row)

    def delete_user(self, user_id: str) -> None:
        """Remove an account only when it no longer owns repository files."""
        with self._connection() as connection:
            row = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                raise RepositoryError("账号不存在。")
            owned = connection.execute("SELECT COUNT(*) FROM files WHERE owner_id = ?", (user_id,)).fetchone()[0]
            if owned:
                raise RepositoryError("该账号仍拥有资料库文件，请先转移或删除文件后再删除账号。")
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM audit_logs WHERE actor_user_id = ? OR target_user_id = ?", (user_id, user_id))
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def login(self, student_id: str, password: str, session_hours: int = 24) -> tuple[str, dict]:
        row = self.get_user_by_student_id(student_id)
        if not row:
            raise AuthenticationError("学号或密码错误。")
        if not row["is_active"]:
            raise AuthenticationError("账号已被禁用，请联系管理员。")
        locked_until = _parse_time(row["locked_until"])
        if locked_until and locked_until > utc_now():
            raise AuthenticationError("登录失败次数过多，请稍后再试。")
        if not verify_password(password, row["password_hash"]):
            failures = row["failed_attempts"] + 1
            lock = (utc_now() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat() if failures >= MAX_LOGIN_FAILURES else None
            with self._connection() as connection:
                connection.execute("UPDATE users SET failed_attempts = ?, locked_until = ?, updated_at = ? WHERE id = ?", (failures, lock, iso_now(), row["id"]))
            raise AuthenticationError("学号或密码错误。")
        now = iso_now()
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires = (utc_now() + timedelta(hours=session_hours)).isoformat()
        with self._connection() as connection:
            connection.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ?, updated_at = ? WHERE id = ?", (now, now, row["id"]))
            connection.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)", (token_hash, row["id"], now, expires))
        return raw_token, self.get_user(row["id"])

    def user_for_session(self, raw_token: str) -> Optional[dict]:
        if not raw_token:
            return None
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
                (token_hash, iso_now()),
            ).fetchone()
        return self.public_user(row) if row else None

    def revoke_session(self, raw_token: str) -> None:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self._connection() as connection:
            connection.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (iso_now(), token_hash))

    def change_password(self, user_id: str, current_password: str, new_password: str) -> dict:
        if len(new_password) < 6:
            raise RepositoryError("新密码至少需要 6 位。")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not verify_password(current_password, row["password_hash"]):
                raise AuthenticationError("当前密码错误。")
            password_hash = hash_password(new_password)
            connection.execute("UPDATE users SET password_hash = ?, must_change_password = 0, failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?", (password_hash, iso_now(), user_id))
        return self.get_user(user_id)

    def _audit(self, connection: sqlite3.Connection, action: str, actor_user_id: Optional[str] = None, file_id: Optional[str] = None, target_user_id: Optional[str] = None, details: str = "") -> None:
        connection.execute("INSERT INTO audit_logs (actor_user_id, action, file_id, target_user_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)", (actor_user_id, action, file_id, target_user_id, details, iso_now()))

    def record_activity(self, action: str, details: str = "") -> None:
        with self._connection() as connection:
            self._audit(connection, action, details=details)

    def _get_tags(self, connection: sqlite3.Connection, file_id: str) -> list[str]:
        return [row[0] for row in connection.execute("SELECT t.name FROM tags t JOIN file_tags ft ON ft.tag_id = t.id WHERE ft.file_id = ? ORDER BY t.name", (file_id,)).fetchall()]

    def _get_folder_tags(self, connection: sqlite3.Connection, folder_id: str) -> list[str]:
        return [row[0] for row in connection.execute("SELECT t.name FROM tags t JOIN folder_tags ft ON ft.tag_id = t.id WHERE ft.folder_id = ? ORDER BY t.name", (folder_id,)).fetchall()]

    def _folder_payload(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict:
        file_count = connection.execute("SELECT COUNT(*) FROM files WHERE group_id = ? AND deleted_at IS NULL", (row["id"],)).fetchone()[0]
        child_count = connection.execute("SELECT COUNT(*) FROM groups WHERE parent_id = ? AND deleted_at IS NULL", (row["id"],)).fetchone()[0]
        return {
            "id": row["id"], "type": "folder", "name": row["name"], "title": row["name"],
            "description": row["description"], "tags": self._get_folder_tags(connection, row["id"]),
            "parent_id": row["parent_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"], "file_count": file_count, "child_folder_count": child_count,
        }

    def _file_payload(self, connection: sqlite3.Connection, row: sqlite3.Row, include_deleted: bool = False) -> dict:
        if row is None or (row["deleted_at"] and not include_deleted):
            return None
        group_name = None
        if row["group_id"]:
            group = connection.execute("SELECT name FROM groups WHERE id = ?", (row["group_id"],)).fetchone()
            group_name = group[0] if group else None
        owner = connection.execute("SELECT name, student_id FROM users WHERE id = ?", (row["owner_id"],)).fetchone()
        return {
            "id": row["id"], "owner_id": row["owner_id"], "owner_name": owner[0] if owner else None, "owner_student_id": owner[1] if owner else None, "original_name": row["original_name"],
            "title": row["title"], "description": row["description"],
            "version": row["version"], "version_note": row["version_note"], "group_id": row["group_id"],
            "group_name": group_name, "tags": self._get_tags(connection, row["id"]), "visibility": row["visibility"],
            "extension": row["extension"], "mime_type": row["mime_type"], "size": row["size"], "sha256": row["sha256"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "deleted_at": row["deleted_at"],
        }

    def list_files(self, user_id: str, query: str = "", group_id: Optional[str] = None, tag: Optional[str] = None, extension: Optional[str] = None, include_deleted: bool = False) -> list[dict]:
        clauses = ["f.deleted_at IS NULL"] if not include_deleted else ["1 = 1"]
        values: list[str] = []
        if query.strip():
            needle = f"%{query.strip()}%"
            clauses.append("(f.original_name LIKE ? OR f.title LIKE ? OR f.description LIKE ? OR f.project_name LIKE ? OR f.version LIKE ? OR f.extension LIKE ? OR EXISTS (SELECT 1 FROM groups g WHERE g.id = f.group_id AND g.name LIKE ?) OR EXISTS (SELECT 1 FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.file_id = f.id AND t.name LIKE ?))")
            values.extend([needle] * 8)
        if group_id:
            clauses.append("f.group_id = ?")
            values.append(group_id)
        if tag:
            clauses.append("EXISTS (SELECT 1 FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.file_id = f.id AND t.name = ?)")
            values.append(tag)
        if extension:
            clauses.append("f.extension = ?")
            values.append(extension.lower() if extension.startswith(".") else f".{extension.lower()}")
        with self._connection() as connection:
            rows = connection.execute(f"SELECT f.* FROM files f WHERE {' AND '.join(clauses)} ORDER BY f.updated_at DESC", values).fetchall()
            return [self._file_payload(connection, row, include_deleted) for row in rows]

    def list_items(self, user_id: str, parent_id: Optional[str] = None, query: str = "") -> list[dict]:
        """Return folders and files in one directory-like listing."""
        with self._connection() as connection:
            search = query.strip()
            folder_values: list[str] = []
            # A search is global: files nested below folders must be discoverable
            # from the root, just like a normal repository search.
            folder_clause = "deleted_at IS NULL"
            if not search:
                folder_clause += " AND (parent_id = ?" if parent_id else " AND (parent_id IS NULL"
                folder_clause += ")"
            if parent_id and not search:
                folder_values.append(parent_id)
            folder_query = f"SELECT * FROM groups WHERE {folder_clause}"
            if search:
                needle = f"%{search}%"
                folder_query += " AND (name LIKE ? OR description LIKE ? OR EXISTS (SELECT 1 FROM folder_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.folder_id = groups.id AND t.name LIKE ?))"
                folder_values.extend([needle, needle, needle])
            folder_rows = connection.execute(folder_query, folder_values).fetchall()

            file_values: list[str] = []
            file_clause = "f.group_id = ?" if parent_id else "f.group_id IS NULL"
            if parent_id and not search:
                file_values.append(parent_id)
            clauses = ["f.deleted_at IS NULL"]
            if not search:
                clauses.append(file_clause)
            else:
                needle = f"%{search}%"
                clauses.append("(f.original_name LIKE ? OR f.title LIKE ? OR f.description LIKE ? OR f.extension LIKE ? OR EXISTS (SELECT 1 FROM groups g WHERE g.id = f.group_id AND g.name LIKE ?) OR EXISTS (SELECT 1 FROM file_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.file_id = f.id AND t.name LIKE ?))")
                file_values.extend([needle] * 6)
            file_rows = connection.execute(f"SELECT f.* FROM files f WHERE {' AND '.join(clauses)} ORDER BY f.updated_at DESC", file_values).fetchall()
            items = [self._folder_payload(connection, row) for row in folder_rows]
            items.extend({**self._file_payload(connection, row), "type": "file", "name": row["original_name"]} for row in file_rows)
            return sorted(items, key=lambda item: (0 if item["type"] == "folder" else 1, item["name"].casefold()))

    def list_all_files(self, include_deleted: bool = True) -> list[dict]:
        clause = "1 = 1" if include_deleted else "deleted_at IS NULL"
        with self._connection() as connection:
            rows = connection.execute(f"SELECT * FROM files WHERE {clause} ORDER BY updated_at DESC").fetchall()
            return [self._file_payload(connection, row, include_deleted) for row in rows]

    def usage_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.originals.iterdir() if path.is_file())

    def repository_stats(self) -> dict:
        with self._connection() as connection:
            counts = connection.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) AS active, "
                "SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted, "
                "COALESCE(SUM(CASE WHEN deleted_at IS NULL THEN size ELSE 0 END), 0) AS active_bytes "
                "FROM files"
            ).fetchone()
            folder_counts = connection.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) AS active, "
                "SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted FROM groups"
            ).fetchone()
            return {
                "total_files": counts["total"] or 0,
                "active_files": counts["active"] or 0,
                "deleted_files": counts["deleted"] or 0,
                "active_bytes": counts["active_bytes"] or 0,
                "folders": folder_counts["active"] or 0,
                "total_folders": folder_counts["total"] or 0,
                "deleted_folders": folder_counts["deleted"] or 0,
            }

    def list_recent_activity(self, limit: int = 12) -> list[dict]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT a.*, actor.name AS actor_name, target.name AS target_user_name, f.original_name "
                "FROM audit_logs a "
                "LEFT JOIN users actor ON actor.id = a.actor_user_id "
                "LEFT JOIN users target ON target.id = a.target_user_id "
                "LEFT JOIN files f ON f.id = a.file_id "
                "ORDER BY a.created_at DESC LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "action": row["action"],
                "actor_name": row["actor_name"],
                "target_user_name": row["target_user_name"],
                "file_name": row["original_name"],
                "details": row["details"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_file(self, file_id: str, user_id: Optional[str] = None, include_deleted: bool = False) -> Optional[dict]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if not row or (row["deleted_at"] and not include_deleted):
                return None
            return self._file_payload(connection, row, include_deleted)

    @staticmethod
    def _safe_zip_entries(path: Path) -> list[tuple[str, int]]:
        if not zipfile.is_zipfile(path):
            raise RepositoryError("ZIP 文件无效。")
        entries: list[tuple[str, int]] = []
        total = 0
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > MAX_ZIP_ENTRIES:
                raise RepositoryError("ZIP 内文件数量超过限制。")
            for info in archive.infolist():
                name = info.filename.replace("\\", "/")
                parts = [part for part in name.split("/") if part]
                mode = (info.external_attr >> 16) & 0xFFFF
                if name.startswith("/") or name.startswith("../") or ".." in parts or stat.S_ISLNK(mode):
                    raise RepositoryError("ZIP 包含不安全的路径或符号链接。")
                total += info.file_size
                if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise RepositoryError("ZIP 解压总大小超过限制。")
                entries.append((name, info.file_size))
        return entries

    def save_uploaded_file(self, source: Path, original_name: str, owner_id: str, metadata: dict, max_bytes: Optional[int], quota_bytes: Optional[int] = None) -> dict:
        safe_name = Path((original_name or "").replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        if not safe_name or suffix not in ALLOWED_SUFFIXES:
            raise RepositoryError("暂不支持此文件类型。")
        size = source.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise RepositoryError("文件超过资料库上传大小限制。")
        if quota_bytes is not None:
            used = sum(path.stat().st_size for path in self.originals.iterdir() if path.is_file())
            if used + size > quota_bytes:
                raise RepositoryError("资料库空间不足。")
        zip_entries = self._safe_zip_entries(source) if suffix == ".zip" else []
        storage_name = f"{secrets.token_hex(24)}{suffix}"
        destination = self.originals / storage_name
        source.replace(destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        file_id = uuid4().hex
        now = iso_now()
        title = (metadata.get("title") or Path(safe_name).stem).strip()[:200]
        visibility = "team"
        try:
            with self._connection() as connection:
                group_id = metadata.get("group_id") or None
                if group_id and not connection.execute("SELECT 1 FROM groups WHERE id = ? AND deleted_at IS NULL", (group_id,)).fetchone():
                    raise RepositoryError("分组不存在。")
                connection.execute(
                    "INSERT INTO files (id, owner_id, original_name, title, description, project_name, version, version_note, group_id, visibility, extension, mime_type, size, sha256, storage_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (file_id, owner_id, safe_name, title, metadata.get("description", "").strip(), metadata.get("project_name", "").strip(), metadata.get("version", "1.0").strip(), metadata.get("version_note", "").strip(), group_id, visibility, suffix, mimetypes.guess_type(safe_name)[0] or "application/octet-stream", size, digest, storage_name, now, now),
                )
                connection.execute("INSERT INTO file_versions (id, file_id, version_number, original_name, storage_name, size, sha256, version_note, created_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)", (uuid4().hex, file_id, safe_name, storage_name, size, digest, metadata.get("version_note", "").strip(), now))
                self._replace_tags(connection, file_id, metadata.get("tags", []))
                connection.executemany("INSERT INTO zip_entries (file_id, entry_name, size) VALUES (?, ?, ?)", [(file_id, name, entry_size) for name, entry_size in zip_entries])
                self._audit(connection, "file.upload", owner_id, file_id, details=safe_name)
                row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
                return self._file_payload(connection, row)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def _replace_tags(self, connection: sqlite3.Connection, file_id: str, tags: list[str]) -> None:
        normalized = sorted({str(tag).strip()[:80] for tag in tags if str(tag).strip()})
        connection.execute("DELETE FROM file_tags WHERE file_id = ?", (file_id,))
        for tag_name in normalized:
            row = connection.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
            tag_id = row[0] if row else uuid4().hex
            if not row:
                connection.execute("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag_id, tag_name, iso_now()))
            connection.execute("INSERT INTO file_tags (file_id, tag_id) VALUES (?, ?)", (file_id, tag_id))

    def list_versions(self, file_id: str, user_id: Optional[str] = None) -> list[dict]:
        with self._connection() as connection:
            file_row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if not file_row:
                raise RepositoryError("文件不存在。")
            rows = connection.execute("SELECT id, version_number, original_name, size, sha256, version_note, created_at FROM file_versions WHERE file_id = ? ORDER BY version_number DESC", (file_id,)).fetchall()
            return [dict(row) for row in rows]

    def add_version(self, file_id: str, user_id: str, source: Path, original_name: str, version: str, version_note: str, max_bytes: Optional[int], quota_bytes: Optional[int] = None, is_admin: bool = False) -> dict:
        safe_name = Path((original_name or "").replace("\\", "/")).name
        suffix = Path(safe_name).suffix.lower()
        if not safe_name or suffix not in ALLOWED_SUFFIXES:
            raise RepositoryError("暂不支持此文件类型。")
        size = source.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise RepositoryError("文件超过资料库上传大小限制。")
        if quota_bytes is not None and self.usage_bytes() + size > quota_bytes:
            raise RepositoryError("资料库空间不足。")
        zip_entries = self._safe_zip_entries(source) if suffix == ".zip" else []
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NULL", (file_id,)).fetchone()
            if not row:
                raise RepositoryError("文件不存在或已删除。")
            storage_name = f"{secrets.token_hex(24)}{suffix}"
            destination = self.originals / storage_name
            previous_path = self.originals / row["storage_name"]
            source.replace(destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            now = iso_now()
            next_number = connection.execute("SELECT COALESCE(MAX(version_number), 0) + 1 FROM file_versions WHERE file_id = ?", (file_id,)).fetchone()[0]
            try:
                connection.execute("UPDATE files SET original_name = ?, version = ?, version_note = ?, extension = ?, mime_type = ?, size = ?, sha256 = ?, storage_name = ?, updated_at = ? WHERE id = ?", (safe_name, version.strip() or str(next_number), version_note.strip(), suffix, mimetypes.guess_type(safe_name)[0] or "application/octet-stream", size, digest, storage_name, now, file_id))
                connection.execute("INSERT INTO file_versions (id, file_id, version_number, original_name, storage_name, size, sha256, version_note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (uuid4().hex, file_id, next_number, safe_name, storage_name, size, digest, version_note.strip(), now))
                connection.execute("DELETE FROM zip_entries WHERE file_id = ?", (file_id,))
                connection.executemany("INSERT INTO zip_entries (file_id, entry_name, size) VALUES (?, ?, ?)", [(file_id, name, entry_size) for name, entry_size in zip_entries])
                self._audit(connection, "file.version", user_id if not is_admin else None, file_id, details=safe_name)
                row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
                if previous_path != destination:
                    previous_path.unlink(missing_ok=True)
                return self._file_payload(connection, row)
            except Exception:
                destination.unlink(missing_ok=True)
                raise

    def update_metadata(self, file_id: str, user_id: str, metadata: dict, is_admin: bool = False) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if not row or row["deleted_at"]:
                raise RepositoryError("文件不存在。")
            visibility = "team"
            now = iso_now()
            connection.execute("UPDATE files SET title = ?, description = ?, project_name = ?, version = ?, version_note = ?, group_id = ?, visibility = ?, updated_at = ? WHERE id = ?", (metadata.get("title", row["title"]).strip()[:200], metadata.get("description", row["description"]).strip(), metadata.get("project_name", row["project_name"]).strip(), metadata.get("version", row["version"]).strip(), metadata.get("version_note", row["version_note"]).strip(), metadata.get("group_id", row["group_id"]) or None, visibility, now, file_id))
            self._replace_tags(connection, file_id, metadata.get("tags", self._get_tags(connection, file_id)))
            self._audit(connection, "file.metadata", user_id, file_id)
            row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            return self._file_payload(connection, row)

    def soft_delete(self, file_id: str, user_id: str, is_admin: bool = False) -> None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NULL", (file_id,)).fetchone()
            if not row:
                raise RepositoryError("文件不存在。")
            connection.execute("UPDATE files SET deleted_at = ?, updated_at = ? WHERE id = ?", (iso_now(), iso_now(), file_id))
            self._audit(connection, "file.delete", user_id, file_id)

    def purge_deleted(self, cutoff: datetime) -> int:
        """Permanently remove files whose soft-delete grace period has elapsed."""
        removed = 0
        with self._connection() as connection:
            rows = connection.execute("SELECT id, storage_name FROM files WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff.isoformat(),)).fetchall()
            for row in rows:
                versions = connection.execute("SELECT storage_name FROM file_versions WHERE file_id = ?", (row["id"],)).fetchall()
                names = {row["storage_name"], *(version["storage_name"] for version in versions)}
                for storage_name in names:
                    path = (self.originals / storage_name).resolve()
                    if self.originals.resolve() in path.parents:
                        path.unlink(missing_ok=True)
                connection.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                removed += 1
            connection.execute("DELETE FROM groups WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff.isoformat(),))
        return removed

    @staticmethod
    def _remove_file_storage(originals: Path, connection: sqlite3.Connection, file_row: sqlite3.Row) -> None:
        versions = connection.execute("SELECT storage_name FROM file_versions WHERE file_id = ?", (file_row["id"],)).fetchall()
        names = {file_row["storage_name"], *(version["storage_name"] for version in versions)}
        root = originals.resolve()
        for storage_name in names:
            path = (originals / storage_name).resolve()
            if root in path.parents:
                path.unlink(missing_ok=True)

    def permanently_delete_file(self, file_id: str) -> None:
        """Remove one soft-deleted file and all of its stored versions immediately."""
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM files WHERE id = ? AND deleted_at IS NOT NULL", (file_id,)).fetchone()
            if not row:
                raise RepositoryError("只能彻底删除已进入回收站的文件。")
            self._remove_file_storage(self.originals, connection, row)
            self._audit(connection, "file.purge", details=row["original_name"])
            connection.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def permanently_delete_folder(self, folder_id: str) -> None:
        """Remove a deleted folder tree and all files contained by it immediately."""
        with self._connection() as connection:
            row = connection.execute("SELECT id, name, deleted_at FROM groups WHERE id = ? AND deleted_at IS NOT NULL", (folder_id,)).fetchone()
            if not row:
                raise RepositoryError("只能彻底删除已进入回收站的文件夹。")
            folder_ids = [folder_id]
            index = 0
            while index < len(folder_ids):
                children = connection.execute("SELECT id FROM groups WHERE parent_id = ?", (folder_ids[index],)).fetchall()
                folder_ids.extend(child[0] for child in children)
                index += 1
            placeholders = ",".join("?" for _ in folder_ids)
            files = connection.execute(f"SELECT * FROM files WHERE group_id IN ({placeholders})", folder_ids).fetchall()
            for file_row in files:
                self._remove_file_storage(self.originals, connection, file_row)
                self._audit(connection, "file.purge", details=file_row["original_name"])
            if files:
                connection.execute(f"DELETE FROM files WHERE group_id IN ({placeholders})", folder_ids)
            self._audit(connection, "folder.purge", details=row["name"])
            connection.execute(f"DELETE FROM groups WHERE id IN ({placeholders})", folder_ids)

    def restore(self, file_id: str, actor: Optional[str] = None) -> dict:
        with self._connection() as connection:
            cursor = connection.execute("UPDATE files SET deleted_at = NULL, updated_at = ? WHERE id = ?", (iso_now(), file_id))
            if cursor.rowcount != 1:
                raise RepositoryError("文件不存在。")
            self._audit(connection, "file.restore", actor, file_id)
            row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            return self._file_payload(connection, row)

    def storage_path(self, file_id: str, user_id: Optional[str] = None, include_deleted: bool = False) -> tuple[Path, dict]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if not row or (row["deleted_at"] and not include_deleted):
                raise RepositoryError("文件不存在。")
            path = (self.originals / row["storage_name"]).resolve()
            if self.originals.resolve() not in path.parents or not path.is_file():
                raise RepositoryError("文件存储不存在。")
            self._audit(connection, "file.download", user_id, file_id)
            return path, self._file_payload(connection, row, include_deleted)

    @staticmethod
    def _archive_component(value: str, fallback: str) -> str:
        component = Path(str(value or "")).name.strip()
        if component in {"", ".", ".."}:
            return fallback
        invalid = '<>:"/\\|?*'
        component = "".join("_" if char in invalid or ord(char) < 32 else char for char in component).strip(" .")
        return component or fallback

    def create_folder_archive(self, folder_id: str, destination: Path) -> str:
        """Create a ZIP archive containing an active folder tree and its current files."""
        destination = Path(destination)
        with self._connection() as connection:
            root = connection.execute("SELECT * FROM groups WHERE id = ? AND deleted_at IS NULL", (folder_id,)).fetchone()
            if not root:
                raise RepositoryError("文件夹不存在或已被删除。")
            folders = connection.execute("SELECT * FROM groups WHERE deleted_at IS NULL").fetchall()
            folder_map = {row["id"]: row for row in folders}
            tree_ids = []
            pending = [folder_id]
            while pending:
                current = pending.pop()
                if current not in folder_map:
                    continue
                tree_ids.append(current)
                pending.extend(row["id"] for row in folders if row["parent_id"] == current)
            tree_set = set(tree_ids)
            files = connection.execute("SELECT * FROM files WHERE deleted_at IS NULL AND group_id IS NOT NULL ORDER BY original_name COLLATE NOCASE").fetchall()
            files = [row for row in files if row["group_id"] in tree_set]
            root_name = self._archive_component(root["name"], "folder")
            used_names: set[str] = set()

            def relative_folder_path(group_id: str) -> str:
                parts = []
                current = group_id
                while current in tree_set:
                    row = folder_map[current]
                    parts.append(self._archive_component(row["name"], "folder"))
                    if current == folder_id:
                        break
                    current = row["parent_id"]
                return "/".join(reversed(parts))

            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for group_id in tree_ids:
                    directory = relative_folder_path(group_id).rstrip("/") + "/"
                    archive.writestr(directory, b"")
                for row in files:
                    source = (self.originals / row["storage_name"]).resolve()
                    if self.originals.resolve() not in source.parents or not source.is_file():
                        raise RepositoryError(f"文件存储不存在：{row['original_name']}")
                    directory = relative_folder_path(row["group_id"])
                    filename = self._archive_component(row["original_name"], "file")
                    member = f"{directory}/{filename}"
                    if member in used_names:
                        stem, suffix = Path(filename).stem, Path(filename).suffix
                        index = 2
                        while f"{directory}/{stem} ({index}){suffix}" in used_names:
                            index += 1
                        member = f"{directory}/{stem} ({index}){suffix}"
                    used_names.add(member)
                    archive.write(source, member)
            return root_name

    def list_folders(self, include_deleted: bool = False) -> list[dict]:
        with self._connection() as connection:
            clause = "1 = 1" if include_deleted else "deleted_at IS NULL"
            rows = connection.execute(f"SELECT * FROM groups WHERE {clause} ORDER BY name COLLATE NOCASE").fetchall()
            return [self._folder_payload(connection, row) for row in rows]

    def _replace_folder_tags(self, connection: sqlite3.Connection, folder_id: str, tags: list[str]) -> None:
        normalized = sorted({str(tag).strip()[:80] for tag in tags if str(tag).strip()})
        connection.execute("DELETE FROM folder_tags WHERE folder_id = ?", (folder_id,))
        for tag_name in normalized:
            row = connection.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
            tag_id = row[0] if row else uuid4().hex
            if not row:
                connection.execute("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag_id, tag_name, iso_now()))
            connection.execute("INSERT INTO folder_tags (folder_id, tag_id) VALUES (?, ?)", (folder_id, tag_id))

    def create_folder(self, name: str, parent_id: Optional[str] = None, description: str = "", tags: Optional[list[str]] = None) -> dict:
        name = name.strip()
        if not name:
            raise RepositoryError("文件夹名称不能为空。")
        folder_id = uuid4().hex
        now = iso_now()
        try:
            with self._connection() as connection:
                if parent_id and not connection.execute("SELECT 1 FROM groups WHERE id = ? AND deleted_at IS NULL", (parent_id,)).fetchone():
                    raise RepositoryError("父文件夹不存在。")
                connection.execute("INSERT INTO groups (id, name, parent_id, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (folder_id, name, parent_id or None, description.strip()[:2000], now, now))
                self._replace_folder_tags(connection, folder_id, tags or [])
                self._audit(connection, "folder.create", details=name)
                row = connection.execute("SELECT * FROM groups WHERE id = ?", (folder_id,)).fetchone()
                return self._folder_payload(connection, row)
        except sqlite3.IntegrityError as error:
            raise RepositoryError("文件夹已存在或父文件夹不存在。") from error

    def update_folder(self, folder_id: str, name: str, parent_id: Optional[str] = None, description: str = "", tags: Optional[list[str]] = None) -> dict:
        name = name.strip()
        if not name:
            raise RepositoryError("文件夹名称不能为空。")
        if parent_id == folder_id:
            raise RepositoryError("文件夹不能移动到自身。")
        now = iso_now()
        try:
            with self._connection() as connection:
                if parent_id and not connection.execute("SELECT 1 FROM groups WHERE id = ? AND deleted_at IS NULL", (parent_id,)).fetchone():
                    raise RepositoryError("父文件夹不存在。")
                cursor = connection.execute("UPDATE groups SET name = ?, parent_id = ?, description = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL", (name, parent_id or None, description.strip()[:2000], now, folder_id))
                if cursor.rowcount != 1:
                    raise RepositoryError("文件夹不存在。")
                self._replace_folder_tags(connection, folder_id, tags or [])
                self._audit(connection, "folder.metadata", details=name)
                row = connection.execute("SELECT * FROM groups WHERE id = ?", (folder_id,)).fetchone()
                return self._folder_payload(connection, row)
        except sqlite3.IntegrityError as error:
            raise RepositoryError("文件夹已存在或父文件夹不存在。") from error

    def delete_folder(self, folder_id: str) -> None:
        with self._connection() as connection:
            row = connection.execute("SELECT id, name FROM groups WHERE id = ? AND deleted_at IS NULL", (folder_id,)).fetchone()
            if not row:
                raise RepositoryError("文件夹不存在。")
            folder_ids = [folder_id]
            index = 0
            while index < len(folder_ids):
                children = connection.execute("SELECT id FROM groups WHERE parent_id = ? AND deleted_at IS NULL", (folder_ids[index],)).fetchall()
                folder_ids.extend(child[0] for child in children)
                index += 1
            now = iso_now()
            placeholders = ",".join("?" for _ in folder_ids)
            connection.execute(f"UPDATE groups SET deleted_at = ?, updated_at = ? WHERE id IN ({placeholders})", [now, now, *folder_ids])
            connection.execute(f"UPDATE files SET deleted_at = ?, updated_at = ? WHERE group_id IN ({placeholders}) AND deleted_at IS NULL", [now, now, *folder_ids])
            self._audit(connection, "folder.delete", details=row["name"])

    def restore_folder(self, folder_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT id, name, deleted_at FROM groups WHERE id = ? AND deleted_at IS NOT NULL", (folder_id,)).fetchone()
            if not row:
                raise RepositoryError("已删除的文件夹不存在。")
            deleted_at = row["deleted_at"]
            folder_ids = [folder_id]
            index = 0
            while index < len(folder_ids):
                children = connection.execute("SELECT id FROM groups WHERE parent_id = ? AND deleted_at = ?", (folder_ids[index], deleted_at)).fetchall()
                folder_ids.extend(child[0] for child in children)
                index += 1
            now = iso_now()
            placeholders = ",".join("?" for _ in folder_ids)
            connection.execute(f"UPDATE groups SET deleted_at = NULL, updated_at = ? WHERE id IN ({placeholders})", [now, *folder_ids])
            connection.execute(f"UPDATE files SET deleted_at = NULL, updated_at = ? WHERE group_id IN ({placeholders}) AND deleted_at = ?", [now, *folder_ids, deleted_at])
            self._audit(connection, "folder.restore", details=row["name"])
            restored = connection.execute("SELECT * FROM groups WHERE id = ?", (folder_id,)).fetchone()
            return self._folder_payload(connection, restored)

    def get_folder(self, folder_id: str, include_deleted: bool = False) -> Optional[dict]:
        with self._connection() as connection:
            clause = "1 = 1" if include_deleted else "deleted_at IS NULL"
            row = connection.execute(f"SELECT * FROM groups WHERE id = ? AND {clause}", (folder_id,)).fetchone()
            return self._folder_payload(connection, row) if row else None

    def list_groups(self) -> list[dict]:
        with self._connection() as connection:
            return [dict(row) for row in connection.execute("SELECT id, name, parent_id, created_at FROM groups WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE").fetchall()]

    def create_group(self, name: str, parent_id: Optional[str] = None) -> dict:
        name = name.strip()
        if not name:
            raise RepositoryError("分组名称不能为空。")
        group_id = uuid4().hex
        try:
            with self._connection() as connection:
                connection.execute("INSERT INTO groups (id, name, parent_id, created_at) VALUES (?, ?, ?, ?)", (group_id, name, parent_id or None, iso_now()))
                row = connection.execute("SELECT id, name, parent_id, created_at FROM groups WHERE id = ?", (group_id,)).fetchone()
                return dict(row)
        except sqlite3.IntegrityError as error:
            raise RepositoryError("分组已存在或父级分组不存在。") from error

    def update_group(self, group_id: str, name: str, parent_id: Optional[str] = None) -> dict:
        name = name.strip()
        if not name:
            raise RepositoryError("分组名称不能为空。")
        try:
            with self._connection() as connection:
                cursor = connection.execute("UPDATE groups SET name = ?, parent_id = ? WHERE id = ?", (name, parent_id or None, group_id))
                if cursor.rowcount != 1:
                    raise RepositoryError("分组不存在。")
                row = connection.execute("SELECT id, name, parent_id, created_at FROM groups WHERE id = ?", (group_id,)).fetchone()
                return dict(row)
        except sqlite3.IntegrityError as error:
            raise RepositoryError("分组已存在或父级分组不存在。") from error

    def delete_group(self, group_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM groups WHERE id = ?", (group_id,))
            if cursor.rowcount != 1:
                raise RepositoryError("分组不存在。")
