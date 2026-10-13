"""Read-only browsing of the four approved teacher source-file folders.

The files are deliberately not imported into the structured material tables.
Only authenticated users can list metadata or request an individual original.
"""

import mimetypes
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse


TEACHER_FOLDERS = ("黄平老师", "寇老师", "刘慰华老师", "袁老师")
INLINE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt"}
PREVIEW_SUFFIXES = INLINE_SUFFIXES | {".xlsx", ".xls", ".csv", ".tsv"}


def _root() -> Path:
    configured = os.environ.get("YNAAS_TEACHER_FILES_DIR", "").strip()
    if not configured:
        raise HTTPException(503, "院内原始文件目录尚未配置。")
    root = Path(configured).resolve()
    if not root.is_dir():
        raise HTTPException(503, "院内原始文件目录暂不可用。")
    return root


def _resolve(root: Path, relative: str, *, file_only: bool = False) -> Path:
    if len(relative) > 1024 or "\\" in relative or "\x00" in relative:
        raise HTTPException(400, "文件路径无效。")
    logical = PurePosixPath(relative)
    parts = logical.parts
    if (not parts or logical.is_absolute() or parts[0] not in TEACHER_FOLDERS
            or any(part in {".", ".."} or part.startswith(".") or part.startswith("~$") for part in parts)):
        raise HTTPException(400, "文件路径不在可浏览的老师目录中。")
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise HTTPException(403, "不允许访问符号链接。")
    try:
        target.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        raise HTTPException(404, "未找到该文件或目录。") from None
    if file_only and not target.is_file():
        raise HTTPException(404, "未找到该文件。")
    if not file_only and not target.is_dir():
        raise HTTPException(404, "未找到该目录。")
    return target


def _entry(path: Path, root: Path) -> dict:
    stat = path.stat()
    is_dir = path.is_dir()
    return {
        "name": path.name,
        "path": path.relative_to(root).as_posix(),
        "kind": "folder" if is_dir else "file",
        "extension": "" if is_dir else path.suffix.lower(),
        "size_bytes": None if is_dir else stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "can_preview": not is_dir and path.suffix.lower() in PREVIEW_SUFFIXES,
    }


def _check_source_version(path: Path, expected: str):
    if expected:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != expected:
            raise HTTPException(409, "原文件已更新，与回答时的来源版本不一致，请重新查询后查看。")


def browse_source_files(relative: str = "") -> dict:
    root = _root()
    if relative:
        folder = _resolve(root, relative)
        candidates = folder.iterdir()
    else:
        candidates = (root / name for name in TEACHER_FOLDERS)
    entries = []
    for path in candidates:
        if path.name.startswith(".") or path.name.startswith("~$") or path.is_symlink() or not path.exists():
            continue
        if not (path.is_dir() or path.is_file()):
            continue
        entries.append(_entry(path, root))
    entries.sort(key=lambda item: (item["kind"] != "folder", item["name"].casefold()))
    return {"path": relative, "entries": entries}


def create_teacher_source_files_router(require_user) -> APIRouter:
    router = APIRouter(prefix="/api/research/teacher-source-files", dependencies=[Depends(require_user)])

    @router.get("/browse")
    def browse(path: str = Query(default="", max_length=1024)):
        return browse_source_files(path)

    @router.get("/file")
    def original_file(path: str = Query(max_length=1024), expected_sha256: str = Query(default="", pattern=r"^(?:[0-9a-f]{64})?$")):
        target = _resolve(_root(), path, file_only=True)
        _check_source_version(target, expected_sha256)
        preview = target.suffix.lower() in INLINE_SUFFIXES
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            content_disposition_type="inline" if preview else "attachment",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/metadata")
    def metadata(path: str = Query(max_length=1024), expected_sha256: str = Query(default="", pattern=r"^(?:[0-9a-f]{64})?$")):
        root = _root()
        target = _resolve(root, path, file_only=True)
        _check_source_version(target, expected_sha256)
        return _entry(target, root)

    from .teacher_file_query import TeacherFileQuery, query_teacher_records
    from .teacher_workbook_index import index_summary

    @router.get("/catalog")
    def catalog():
        try:
            return index_summary()
        except (ValueError, OSError) as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post("/query")
    def query(payload: TeacherFileQuery):
        try:
            return query_teacher_records(payload)
        except (ValueError, OSError) as exc:
            raise HTTPException(503, str(exc)) from exc

    return router
