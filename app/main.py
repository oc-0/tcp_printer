import mimetypes
import secrets
import shutil
import json
import tempfile
import asyncio
import os
import platform
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, get_settings
from .jobs import ALLOWED_SUFFIXES, FileConverter, JobError, JobStore, PrintBackend, PrintWorker, TERMINAL_STATES, filter_page_set, parse_page_range, validate_source_file
from .repository import AuthenticationError, Repository, RepositoryError


settings = get_settings()
store = JobStore(settings.data_dir / "printer.db")
converter = FileConverter(settings)
backend = PrintBackend(settings)
worker = PrintWorker(store, backend, settings.storage_dir, settings.retention_hours)
repository = Repository(settings.data_dir / "repository.db", settings.storage_dir / "repository")
if settings.admin_student_id and settings.admin_password:
    repository.ensure_backdoor_user(settings.admin_student_id, settings.admin_password)


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.fail_interrupted_prints()
    repository.purge_deleted(datetime.now(timezone.utc) - timedelta(hours=settings.repository_deleted_retention_hours))
    worker.start()
    cleanup_task = asyncio.create_task(repository_cleanup_loop())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    worker.stop()


async def repository_cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        repository.purge_deleted(datetime.now(timezone.utc) - timedelta(hours=settings.repository_deleted_retention_hours))


# Windows and some Linux installations do not register .mjs by default.
# PDF.js is loaded as an ES module and browsers reject text/plain responses.
mimetypes.add_type("application/javascript", ".mjs")

app = FastAPI(title="TCP Printer", lifespan=lifespan)
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")), name="static")


@app.middleware("http")
async def disable_page_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class SubmitOptions(BaseModel):
    color_mode: str = Field("monochrome")
    copies: int = Field(1, ge=1, le=99)
    page_range: str = Field("all")
    orientation: str = Field("portrait")
    page_set: str = Field("all")


class StorageLogin(BaseModel):
    student_id: str
    password: str


class StoragePasswordChange(BaseModel):
    current_password: str
    new_password: str


class FileMetadata(BaseModel):
    title: str = ""
    description: str = ""
    version: str = "1.0"
    group_id: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    visibility: str = "team"
    version_note: str = ""


class FolderMetadata(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None


class AdminAccountCreate(BaseModel):
    name: str
    student_id: str
    password: str = "111111"


class AdminAccountActive(BaseModel):
    active: bool


class AdminAccountRole(BaseModel):
    is_admin: bool


class AdminLogin(BaseModel):
    student_id: str
    password: str
    admin_token: str


def system_memory() -> dict:
    """Return portable host memory telemetry without adding a dependency."""
    total = available = 0
    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_uint32), ("memory_load", ctypes.c_uint32), ("total", ctypes.c_uint64), ("available", ctypes.c_uint64), ("page_file_total", ctypes.c_uint64), ("page_file_available", ctypes.c_uint64), ("virtual_total", ctypes.c_uint64), ("virtual_available", ctypes.c_uint64), ("available_extended", ctypes.c_uint64)]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total, available = status.total, status.available
        except (AttributeError, OSError):
            pass
    else:
        try:
            values = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                key, raw = line.split(":", 1)
                values[key] = int(raw.strip().split()[0]) * 1024
            total, available = values.get("MemTotal", 0), values.get("MemAvailable", 0)
        except (OSError, ValueError):
            pass
    used = max(0, total - available)
    return {"total": total, "available": available, "used": used, "percent": round((used / total) * 100, 1) if total else None}


def session_id(request: Request, response: Optional[Response] = None) -> str:
    identifier = request.cookies.get("tcp_printer_session")
    if not identifier:
        identifier = secrets.token_urlsafe(24)
        if response is not None:
            response.set_cookie("tcp_printer_session", identifier, httponly=True, samesite="lax")
    return identifier


def storage_user(request: Request, allow_password_change: bool = False) -> dict:
    user = repository.user_for_session(request.cookies.get("tcp_storage_session", ""))
    if not user:
        raise HTTPException(status_code=401, detail="请先登录资料库。")
    if user["must_change_password"] and not allow_password_change:
        raise HTTPException(status_code=428, detail="首次登录请先修改密码。")
    return user


def repository_error(error: RepositoryError) -> HTTPException:
    if isinstance(error, AuthenticationError):
        return HTTPException(status_code=401, detail=str(error))
    message = str(error)
    status = 403 if "只有" in message or "无权" in message else 400
    return HTTPException(status_code=status, detail=message)


def parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return [str(item) for item in value]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in raw.split(",") if item.strip()]


def job_for_session(job_id: str, request: Request) -> dict:
    job = store.get(job_id)
    if not job or job["session_id"] != session_id(request):
        raise HTTPException(status_code=404, detail="未找到任务。")
    return job


def public_job(job: dict) -> dict:
    return {
        key: job[key]
        for key in (
            "id",
            "public_id",
            "file_name",
            "state",
            "message",
            "pages",
            "color_mode",
            "copies",
            "page_range",
            "orientation",
            "page_set",
            "created_at",
            "updated_at",
        )
    }


def admin_job(job: dict) -> dict:
    payload = public_job(job)
    payload["printer_job_id"] = job.get("cups_job_id")
    payload["session_id"] = job["session_id"]
    return payload


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    response = templates.TemplateResponse("index.html", {"request": request, "mode": settings.mode})
    session_id(request, response)
    return response


@app.get("/storage", response_class=HTMLResponse)
async def storage_login_page(request: Request):
    """Render the storage login shell; authentication is added with the storage API."""
    return templates.TemplateResponse("storage.html", {"request": request})


@app.get("/files", response_class=HTMLResponse)
async def files_login_page(request: Request):
    return templates.TemplateResponse("storage.html", {"request": request})


@app.get("/storage/files", response_class=HTMLResponse)
async def storage_files_page(request: Request):
    """Render the storage browser shell independently from the print workflow."""
    try:
        storage_user(request)
    except HTTPException:
        return RedirectResponse("/storage", status_code=303)
    return templates.TemplateResponse("storage_files.html", {"request": request})


def is_admin_user(user: Optional[dict]) -> bool:
    return bool(user and (user.get("is_admin") or (settings.admin_student_id and user.get("student_id") == settings.admin_student_id)))


def manageable_accounts() -> list[dict]:
    return [user for user in repository.list_users() if not (settings.admin_student_id and user.get("student_id") == settings.admin_student_id)]


def reject_backdoor_mutation(user_id: str) -> None:
    target = repository.get_user(user_id)
    if target and settings.admin_student_id and target.get("student_id") == settings.admin_student_id:
        raise HTTPException(status_code=400, detail="后门管理员账号不能被修改。")


def require_admin(request: Request) -> dict:
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="管理员功能未启用。")
    supplied = request.headers.get("x-admin-token", "")
    if not supplied or not secrets.compare_digest(supplied, settings.admin_token):
        raise HTTPException(status_code=401, detail="管理员令牌不正确。")
    user = repository.user_for_session(request.cookies.get("tcp_admin_session", ""))
    if not is_admin_user(user):
        raise HTTPException(status_code=401, detail="请先使用管理员账号登录。")
    return user


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="管理员功能未启用。")
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/health")
async def health():
    return {"ok": True, "mode": settings.mode}


@app.get("/api/printer")
async def printer_status():
    return backend.status()


@app.get("/api/admin/status")
async def admin_status(request: Request):
    require_admin(request)
    usage = shutil.disk_usage(settings.storage_dir)
    printer_details = backend.diagnostics()
    repository_stats = repository.repository_stats()
    return {
        "printer": printer_details["status"],
        "printer_details": printer_details,
        "mode": settings.mode,
        "server": {
            "platform": platform.platform(),
            "hostname": platform.node(),
            "pid": os.getpid(),
            "memory": system_memory(),
        },
        "queue": {"paused": store.queue_paused(), **store.queue_counts()},
        "retention_hours": settings.retention_hours,
        "storage": {"used": usage.used, "free": usage.free, "total": usage.total},
        "repository": {"used": repository.usage_bytes(), "quota": settings.repository_quota_bytes, **repository_stats},
        "repository_deleted_retention_hours": settings.repository_deleted_retention_hours,
        "repository_files": repository.list_all_files(),
        "repository_folders": repository.list_folders(include_deleted=True),
        "recycle_bin": {
            "files": [item for item in repository.list_all_files() if item.get("deleted_at")],
            "folders": [item for item in repository.list_folders(include_deleted=True) if item.get("deleted_at")],
        },
        "activities": repository.list_recent_activity(),
        "accounts": manageable_accounts(),
        "jobs": [admin_job(job) for job in store.list_recent()],
    }


@app.get("/api/admin/accounts")
async def admin_accounts(request: Request):
    require_admin(request)
    return manageable_accounts()


@app.post("/api/admin/accounts")
async def admin_create_account(payload: AdminAccountCreate, request: Request):
    require_admin(request)
    try:
        return repository.create_user(payload.name, payload.student_id, payload.password)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.patch("/api/admin/accounts/{user_id}")
async def admin_set_account_active(user_id: str, payload: AdminAccountActive, request: Request):
    require_admin(request)
    reject_backdoor_mutation(user_id)
    try:
        return repository.set_user_active(user_id, payload.active)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.post("/api/admin/accounts/{user_id}/role")
async def admin_set_account_role(user_id: str, payload: AdminAccountRole, request: Request):
    current_admin = require_admin(request)
    if current_admin.get("id") == user_id and not payload.is_admin:
        raise HTTPException(status_code=400, detail="不能收回当前登录账号的管理员权限。")
    target = repository.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="账号不存在。")
    if settings.admin_student_id and target.get("student_id") == settings.admin_student_id:
        raise HTTPException(status_code=400, detail="后门管理员账号不能取消管理员权限。")
    try:
        return repository.set_user_admin(user_id, payload.is_admin)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.post("/api/admin/accounts/{user_id}/reset-password")
async def admin_reset_account_password(user_id: str, request: Request):
    require_admin(request)
    reject_backdoor_mutation(user_id)
    try:
        return repository.reset_password(user_id)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.delete("/api/admin/accounts/{user_id}")
async def admin_delete_account(user_id: str, request: Request):
    require_admin(request)
    target = repository.get_user(user_id)
    if target and settings.admin_student_id and target.get("student_id") == settings.admin_student_id:
        raise HTTPException(status_code=400, detail="后门管理员账号不能删除。")
    try:
        repository.delete_user(user_id)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": user_id}


@app.get("/api/admin/files")
async def admin_files(request: Request):
    require_admin(request)
    return repository.list_all_files()


@app.patch("/api/admin/folders/{folder_id}")
async def admin_update_folder(folder_id: str, payload: FolderMetadata, request: Request):
    require_admin(request)
    try:
        return repository.update_folder(folder_id, payload.name, payload.parent_id, payload.description, payload.tags)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.delete("/api/admin/folders/{folder_id}")
async def admin_delete_folder(folder_id: str, request: Request):
    require_admin(request)
    try:
        repository.delete_folder(folder_id)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": folder_id}


@app.post("/api/admin/folders/{folder_id}/restore")
async def admin_restore_folder(folder_id: str, request: Request):
    require_admin(request)
    try:
        return repository.restore_folder(folder_id)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.patch("/api/admin/files/{file_id}/metadata")
async def admin_update_file_metadata(file_id: str, payload: FileMetadata, request: Request):
    require_admin(request)
    try:
        return repository.update_metadata(file_id, None, payload.model_dump(exclude_unset=True), is_admin=True)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.delete("/api/admin/files/{file_id}")
async def admin_delete_file(file_id: str, request: Request):
    require_admin(request)
    try:
        repository.soft_delete(file_id, None, is_admin=True)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": file_id}


@app.post("/api/admin/files/{file_id}/restore")
async def admin_restore_file(file_id: str, request: Request):
    require_admin(request)
    try:
        return repository.restore(file_id, None)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.delete("/api/admin/files/{file_id}/purge")
async def admin_purge_file(file_id: str, request: Request):
    require_admin(request)
    try:
        repository.permanently_delete_file(file_id)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": file_id, "permanent": True}


@app.delete("/api/admin/folders/{folder_id}/purge")
async def admin_purge_folder(folder_id: str, request: Request):
    require_admin(request)
    try:
        repository.permanently_delete_folder(folder_id)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": folder_id, "permanent": True}


@app.post("/api/storage/login")
async def storage_login(payload: StorageLogin, response: Response):
    try:
        token, user = repository.login(payload.student_id, payload.password, settings.storage_session_hours)
    except RepositoryError as error:
        raise repository_error(error) from error
    response.set_cookie("tcp_storage_session", token, max_age=settings.storage_session_hours * 3600, httponly=True, samesite="lax")
    return {"user": user, "must_change_password": user["must_change_password"]}


@app.post("/api/admin/login")
async def admin_login(payload: AdminLogin, response: Response):
    if settings.admin_student_id and payload.student_id.strip() == settings.admin_student_id and not settings.admin_password:
        raise HTTPException(status_code=503, detail="后门账号尚未配置 TCP_PRINTER_ADMIN_PASSWORD。")
    if not settings.admin_token or not secrets.compare_digest(payload.admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="管理员令牌不正确。")
    try:
        token, user = repository.login(payload.student_id, payload.password, settings.storage_session_hours)
    except RepositoryError as error:
        raise repository_error(error) from error
    if not is_admin_user(user):
        repository.revoke_session(token)
        raise HTTPException(status_code=403, detail="该账号没有管理员权限。")
    response.set_cookie("tcp_admin_session", token, max_age=settings.storage_session_hours * 3600, httponly=True, samesite="lax")
    return {"user": user, "must_change_password": user["must_change_password"]}


@app.post("/api/storage/logout")
async def storage_logout(request: Request, response: Response):
    token = request.cookies.get("tcp_storage_session", "")
    if token:
        repository.revoke_session(token)
    response.delete_cookie("tcp_storage_session")
    return {"ok": True}


@app.get("/api/storage/me")
async def storage_me(request: Request):
    user = storage_user(request, allow_password_change=True)
    user["deleted_retention_hours"] = settings.repository_deleted_retention_hours
    return user


@app.post("/api/storage/password")
async def storage_change_password(payload: StoragePasswordChange, request: Request):
    user = storage_user(request, allow_password_change=True)
    try:
        return repository.change_password(user["id"], payload.current_password, payload.new_password)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.get("/api/files")
async def list_repository_files(request: Request, q: str = "", group_id: Optional[str] = None, tag: Optional[str] = None, extension: Optional[str] = None):
    user = storage_user(request)
    return repository.list_files(user["id"], query=q, group_id=group_id, tag=tag, extension=extension)


@app.post("/api/files")
async def upload_repository_file(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    description: str = Form(""),
    version: str = Form("1.0"),
    group_id: Optional[str] = Form(None),
    tags: str = Form(""),
    visibility: str = Form("team"),
    version_note: str = Form(""),
):
    user = storage_user(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择文件。")
    incoming = None
    try:
        with tempfile.NamedTemporaryFile(dir=repository.root, prefix="upload-", suffix=".tmp", delete=False) as target:
            incoming = Path(target.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if settings.repository_max_upload_bytes is not None and size > settings.repository_max_upload_bytes:
                    raise RepositoryError("文件超过资料库上传大小限制。")
                target.write(chunk)
        metadata = {
            "title": title, "description": description,
            "version": version, "group_id": group_id, "tags": parse_tags(tags),
            "visibility": visibility, "version_note": version_note,
        }
        result = repository.save_uploaded_file(incoming, file.filename, user["id"], metadata, settings.repository_max_upload_bytes, settings.repository_quota_bytes)
        incoming = None
        return result
    except RepositoryError as error:
        raise repository_error(error) from error
    finally:
        if incoming:
            incoming.unlink(missing_ok=True)


@app.get("/api/files/{file_id}/versions")
async def list_repository_versions(file_id: str, request: Request):
    user = storage_user(request)
    try:
        return repository.list_versions(file_id, user["id"])
    except RepositoryError as error:
        raise repository_error(error) from error


@app.post("/api/files/{file_id}/versions")
async def upload_repository_version(
    file_id: str,
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(""),
    version_note: str = Form(""),
):
    user = storage_user(request)
    incoming = None
    try:
        with tempfile.NamedTemporaryFile(dir=repository.root, prefix="version-", suffix=".tmp", delete=False) as target:
            incoming = Path(target.name)
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if settings.repository_max_upload_bytes is not None and size > settings.repository_max_upload_bytes:
                    raise RepositoryError("文件超过资料库上传大小限制。")
                target.write(chunk)
        result = repository.add_version(file_id, user["id"], incoming, file.filename or "", version, version_note, settings.repository_max_upload_bytes, settings.repository_quota_bytes)
        incoming = None
        return result
    except RepositoryError as error:
        raise repository_error(error) from error
    finally:
        if incoming:
            incoming.unlink(missing_ok=True)


@app.get("/api/files/{file_id}")
async def get_repository_file(file_id: str, request: Request):
    user = storage_user(request)
    result = repository.get_file(file_id, user["id"])
    if not result:
        raise HTTPException(status_code=404, detail="文件不存在。")
    return result


@app.patch("/api/files/{file_id}/metadata")
async def update_repository_metadata(file_id: str, payload: FileMetadata, request: Request):
    user = storage_user(request)
    try:
        return repository.update_metadata(file_id, user["id"], payload.model_dump(exclude_unset=True))
    except RepositoryError as error:
        raise repository_error(error) from error


@app.get("/api/files/{file_id}/download")
async def download_repository_file(file_id: str, request: Request):
    user = storage_user(request)
    try:
        path, metadata = repository.storage_path(file_id, user["id"])
    except RepositoryError as error:
        raise repository_error(error) from error
    filename = quote(metadata["original_name"])
    return FileResponse(path, media_type=metadata["mime_type"], headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@app.delete("/api/files/{file_id}")
async def delete_repository_file(file_id: str, request: Request):
    user = storage_user(request)
    try:
        repository.soft_delete(file_id, user["id"])
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": file_id}


@app.get("/api/groups")
async def list_repository_groups(request: Request):
    storage_user(request)
    return repository.list_groups()


@app.get("/api/storage/items")
async def list_storage_items(request: Request, parent_id: Optional[str] = None, q: str = ""):
    user = storage_user(request)
    return repository.list_items(user["id"], parent_id=parent_id, query=q)


@app.get("/api/folders")
async def list_repository_folders(request: Request):
    storage_user(request)
    return repository.list_folders()


@app.post("/api/folders")
async def create_repository_folder(payload: FolderMetadata, request: Request):
    storage_user(request)
    try:
        return repository.create_folder(payload.name, payload.parent_id, payload.description, payload.tags)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.get("/api/folders/{folder_id}/download")
async def download_repository_folder(folder_id: str, request: Request):
    storage_user(request)
    archive_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=repository.root, prefix="folder-", suffix=".zip", delete=False) as target:
            archive_path = Path(target.name)
        archive_name = repository.create_folder_archive(folder_id, archive_path)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"{archive_name}.zip",
            background=BackgroundTask(archive_path.unlink, missing_ok=True),
        )
    except RepositoryError as error:
        if archive_path:
            archive_path.unlink(missing_ok=True)
        raise repository_error(error) from error
    except Exception:
        if archive_path:
            archive_path.unlink(missing_ok=True)
        raise


@app.get("/api/folders/{folder_id}")
async def get_repository_folder(folder_id: str, request: Request):
    storage_user(request)
    folder = repository.get_folder(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在。")
    return folder


@app.patch("/api/folders/{folder_id}")
async def update_repository_folder(folder_id: str, payload: FolderMetadata, request: Request):
    storage_user(request)
    try:
        return repository.update_folder(folder_id, payload.name, payload.parent_id, payload.description, payload.tags)
    except RepositoryError as error:
        raise repository_error(error) from error


@app.delete("/api/folders/{folder_id}")
async def delete_repository_folder(folder_id: str, request: Request):
    storage_user(request)
    try:
        repository.delete_folder(folder_id)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": folder_id}


@app.post("/api/groups")
async def create_repository_group(payload: dict, request: Request):
    storage_user(request)
    try:
        return repository.create_group(str(payload.get("name", "")), payload.get("parent_id"))
    except RepositoryError as error:
        raise repository_error(error) from error


@app.patch("/api/groups/{group_id}")
async def update_repository_group(group_id: str, payload: dict, request: Request):
    storage_user(request)
    try:
        return repository.update_group(group_id, str(payload.get("name", "")), payload.get("parent_id"))
    except RepositoryError as error:
        raise repository_error(error) from error


@app.delete("/api/groups/{group_id}")
async def delete_repository_group(group_id: str, request: Request):
    storage_user(request)
    try:
        repository.delete_group(group_id)
    except RepositoryError as error:
        raise repository_error(error) from error
    return {"deleted": group_id}


@app.post("/api/admin/queue/pause")
async def admin_pause_queue(request: Request):
    require_admin(request)
    store.set_queue_paused(True)
    return {"paused": True}


@app.post("/api/admin/queue/resume")
async def admin_resume_queue(request: Request):
    require_admin(request)
    store.set_queue_paused(False)
    return {"paused": False}


@app.post("/api/admin/cleanup")
async def admin_cleanup(request: Request):
    require_admin(request)
    removed = store.purge_expired(
        datetime.now(timezone.utc), settings.storage_dir, all_finished=True
    )
    repository.record_activity("system.cleanup", f"清理了 {removed} 个已结束任务")
    return {"removed_jobs": removed, "message": f"已清理 {removed} 个已结束任务"}


@app.post("/api/admin/jobs/{job_id}/cancel")
async def admin_cancel(job_id: str, request: Request):
    require_admin(request)
    job = store.get(job_id)
    if not job or job["state"] not in {"converting", "ready", "pending"}:
        raise HTTPException(status_code=409, detail="任务当前无法取消。")
    return public_job(store.update(job_id, state="cancelled", message="管理员已取消任务"))


@app.post("/api/admin/jobs/{job_id}/stop")
async def admin_stop(job_id: str, request: Request):
    require_admin(request)
    job = store.get(job_id)
    if not job or job["state"] != "printing":
        raise HTTPException(status_code=409, detail="任务当前没有在打印。")
    backend.cancel(job.get("cups_job_id"))
    return public_job(store.update(job_id, state="stopped", message="管理员已停止后续打印"))


@app.post("/api/admin/printer/jobs/{printer_job_id}/cancel")
async def admin_cancel_printer_job(printer_job_id: str, request: Request):
    require_admin(request)
    if settings.mode != "windows":
        raise HTTPException(status_code=409, detail="当前打印模式不支持直接管理 Windows 打印作业。")
    try:
        backend.cancel(printer_job_id)
    except JobError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"cancelled": printer_job_id}


@app.post("/api/uploads")
async def upload_file(request: Request, file: UploadFile = File(...)):
    current_session = request.cookies.get("tcp_printer_session")
    new_session = not current_session
    if not current_session:
        current_session = secrets.token_urlsafe(24)
    if not file.filename:
        raise HTTPException(status_code=400, detail="请选择文件。")
    safe_name = Path(file.filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="暂不支持此文件类型。")

    upload_dir = settings.storage_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{secrets.token_hex(12)}{suffix}"
    size = 0
    with destination.open("wb") as target:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                target.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="文件超过服务器允许的上传大小。")
            target.write(chunk)

    try:
        validate_source_file(destination)
    except JobError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error)) from error

    job = store.create_draft(current_session, safe_name, destination)
    try:
        pdf_path, pages = converter.convert(job)
        job = store.update(job["id"], state="ready", message="文件已准备完成", pdf_path=str(pdf_path), pages=pages)
    except JobError as error:
        job = store.update(job["id"], state="failed", message=str(error))
    payload = public_job(job)
    if job["state"] == "ready":
        payload["preview_url"] = f"/api/jobs/{job['id']}/preview"
    response = JSONResponse(payload)
    if new_session:
        response.set_cookie("tcp_printer_session", current_session, httponly=True, samesite="lax")
    return response


@app.get("/api/jobs")
async def list_jobs(request: Request):
    return [public_job(job) for job in store.list_session(session_id(request))]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    return public_job(job_for_session(job_id, request))


@app.get("/api/jobs/{job_id}/preview")
async def preview(job_id: str, request: Request):
    job = job_for_session(job_id, request)
    if not job.get("pdf_path") or not Path(job["pdf_path"]).exists():
        raise HTTPException(status_code=404, detail="预览文件不存在。")
    # 明确要求浏览器内嵌预览；微信等内置 WebView 仍可能强制交给系统浏览器处理。
    return FileResponse(
        job["pdf_path"],
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@app.post("/api/jobs/{job_id}/submit")
async def submit(job_id: str, options: SubmitOptions, request: Request):
    job = job_for_session(job_id, request)
    if job["state"] != "ready":
        raise HTTPException(status_code=409, detail="该任务当前不能提交。")
    if options.color_mode not in {"monochrome", "color"}:
        raise HTTPException(status_code=400, detail="颜色模式不正确。")
    if options.orientation not in {"portrait", "landscape"}:
        raise HTTPException(status_code=400, detail="Invalid print orientation")
    if options.page_set not in {"all", "odd", "even"}:
        raise HTTPException(status_code=400, detail="Invalid page selection")
    try:
        page_range, _ = parse_page_range(options.page_range, job["pages"])
        page_numbers = list(range(job["pages"])) if page_range == "all" else PrintBackend._windows_page_numbers(page_range, job["pages"])
        if not filter_page_set(page_numbers, options.page_set):
            raise JobError("所选范围没有符合条件的页面")
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    job = store.update(
        job_id,
        state="pending",
        message="已加入打印队列",
        color_mode=options.color_mode,
        copies=options.copies,
        page_range=page_range,
        orientation=options.orientation,
        page_set=options.page_set,
    )
    return public_job(job)


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    job = job_for_session(job_id, request)
    if job["state"] not in {"converting", "ready", "pending"}:
        raise HTTPException(status_code=409, detail="任务当前无法取消。")
    return public_job(store.update(job_id, state="cancelled", message="任务已取消"))


@app.post("/api/jobs/{job_id}/stop")
async def stop(job_id: str, request: Request):
    job = job_for_session(job_id, request)
    if job["state"] != "printing":
        raise HTTPException(status_code=409, detail="任务当前没有在打印。")
    backend.cancel(job.get("cups_job_id"))
    return public_job(store.update(job_id, state="stopped", message="已停止后续打印"))
