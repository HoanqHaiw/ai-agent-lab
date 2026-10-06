from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any
from uuid import UUID, uuid4
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile, ZipInfo

from fastapi import UploadFile
from .project_overview import analyze_project, discover_project_files

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
RETENTION_DAYS = 7
CHUNK_SIZE = 1024 * 1024
IGNORED_PARTS = {
    ".git", "node_modules", "dist", "build", ".next", ".venv", "venv",
    "coverage", "vendor", "__pycache__", ".cache",
}

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT") or Path(__file__).resolve().parents[1] / ".workspaces").resolve()
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)


class WorkspaceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _safe_workspace_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkspaceError("Workspace not found.", 404) from exc


def _archive_path(info: ZipInfo) -> tuple[PurePosixPath, bool] | None:
    raw_name = info.filename
    if "\x00" in raw_name or "\\" in raw_name:
        raise WorkspaceError("The ZIP contains an unsafe file path.")
    posix_path = PurePosixPath(raw_name)
    windows_path = PureWindowsPath(raw_name)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise WorkspaceError("The ZIP contains an absolute file path.")
    if any(part in {"..", ""} for part in posix_path.parts):
        raise WorkspaceError("The ZIP contains a path that escapes the workspace.")
    if not posix_path.parts:
        return None

    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise WorkspaceError("The ZIP contains a symbolic link, which is not supported.")
    if info.flag_bits & 0x1:
        raise WorkspaceError("Password-protected ZIP files are not supported.")
    if any(part.lower() in IGNORED_PARTS for part in posix_path.parts):
        return None
    return posix_path, info.is_dir()


async def _persist_metadata(metadata: dict[str, Any], directory: Path) -> None:
    metadata_path = directory / "workspace.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        return
    try:
        from pymongo import AsyncMongoClient

        client = AsyncMongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
        database_name = os.environ.get("MONGODB_DATABASE") or "agent_lab"
        await client.admin.command("ping")
        await client[database_name].workspaces.replace_one(
            {"_id": metadata["id"]}, metadata, upsert=True
        )
        await client.close()
    except Exception as exc:
        raise WorkspaceError("Could not save workspace metadata to MongoDB. Check MONGODB_URI and Atlas network access.", 503) from exc


async def _read_metadata(workspace_id: str, directory: Path) -> dict[str, Any] | None:
    mongo_uri = os.environ.get("MONGODB_URI")
    if mongo_uri:
        try:
            from pymongo import AsyncMongoClient

            client = AsyncMongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
            database_name = os.environ.get("MONGODB_DATABASE") or "agent_lab"
            doc = await client[database_name].workspaces.find_one({"_id": workspace_id})
            await client.close()
            if doc:
                doc.pop("_id", None)
                return doc
        except Exception as exc:
            raise WorkspaceError("Could not read workspace metadata from MongoDB.", 503) from exc
    metadata_path = directory / "workspace.json"
    if not metadata_path.is_file():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


async def _delete_metadata(workspace_id: str) -> None:
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        return
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
    database_name = os.environ.get("MONGODB_DATABASE") or "agent_lab"
    await client[database_name].workspaces.delete_one({"_id": workspace_id})
    await client.close()


async def create_workspace_from_zip(upload: UploadFile) -> dict[str, Any]:
    filename = Path(upload.filename or "project.zip").name
    project_name = re.sub(r"[^A-Za-z0-9._ -]", "", re.sub(r"\.zip$", "", filename, flags=re.IGNORECASE)).strip(" .") or "uploaded-project"
    workspace_id = str(uuid4())
    directory = WORKSPACE_ROOT / workspace_id
    directory.mkdir(parents=True, exist_ok=False)
    temp_file: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(prefix="agent-lab-", suffix=".zip", delete=False, dir=WORKSPACE_ROOT) as temp:
            temp_file = Path(temp.name)
            total_bytes = 0
            while chunk := await upload.read(CHUNK_SIZE):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise WorkspaceError("ZIP files must be 50 MB or smaller.", 413)
                temp.write(chunk)
        if total_bytes == 0:
            raise WorkspaceError("The ZIP file is empty.")

        try:
            with ZipFile(temp_file) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ARCHIVE_ENTRIES:
                    raise WorkspaceError("The ZIP contains more than 10,000 entries.", 413)

                plan: list[tuple[ZipInfo, PurePosixPath, bool]] = []
                total_uncompressed = 0
                seen_paths: set[str] = set()
                for info in entries:
                    safe_entry = _archive_path(info)
                    if safe_entry is None:
                        continue
                    path, is_directory = safe_entry
                    normalized = path.as_posix().casefold()
                    if normalized in seen_paths:
                        raise WorkspaceError("The ZIP contains duplicate file paths.")
                    seen_paths.add(normalized)
                    if not is_directory:
                        total_uncompressed += info.file_size
                        if total_uncompressed > MAX_EXTRACTED_BYTES:
                            raise WorkspaceError("The extracted project must be 200 MB or smaller.", 413)
                    plan.append((info, path, is_directory))

                extracted_files: list[str] = []
                for info, relative_path, is_directory in plan:
                    target = (directory / Path(*relative_path.parts)).resolve()
                    if not target.is_relative_to(directory.resolve()):
                        raise WorkspaceError("The ZIP contains a path that escapes the workspace.")
                    if is_directory:
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with archive.open(info) as source, target.open("xb") as destination:
                        while chunk := source.read(CHUNK_SIZE):
                            written += len(chunk)
                            if written > info.file_size or written > MAX_EXTRACTED_BYTES:
                                raise WorkspaceError("The ZIP contains inconsistent file sizes.")
                            destination.write(chunk)
                    if written != info.file_size:
                        raise WorkspaceError("A file in the ZIP could not be extracted completely.")
                    extracted_files.append(relative_path.as_posix())
        except BadZipFile as exc:
            raise WorkspaceError("The selected file is not a valid ZIP archive.") from exc

        if not extracted_files:
            raise WorkspaceError("The ZIP has no supported project files after generated folders are excluded.")

        extracted_files.sort(key=str.casefold)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(days=RETENTION_DAYS)
        result = {
            "id": workspace_id,
            "name": project_name,
            "source": "ZIP upload",
            "status": "ready",
            "file_count": len(extracted_files),
            "files_preview": extracted_files[:100],
            "project_overview": analyze_project(directory, extracted_files),
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        await _persist_metadata(result, directory)
        return result
    except WorkspaceError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise WorkspaceError("Could not process this ZIP archive.", 400) from exc
    finally:
        if temp_file:
            temp_file.unlink(missing_ok=True)


def _normalize_github_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value.strip())
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.query
            or parsed.fragment
            or len(parts) != 2
        ):
            raise ValueError
        owner, repository = parts
        repository = re.sub(r"\.git$", "", repository, flags=re.IGNORECASE)
        if (
            owner in {".", ".."}
            or repository in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repository)
        ):
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise WorkspaceError("Enter a public GitHub repository URL in the form https://github.com/owner/repository.") from exc
    return f"https://github.com/{owner}/{repository}.git", repository


def _scan_cloned_files(root: Path) -> tuple[list[str], int]:
    paths: list[str] = []
    total_bytes = 0
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if name.lower() not in IGNORED_PARTS
            and not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            file_path = current_path / filename
            if file_path.is_symlink() or ".git" in file_path.relative_to(root).parts:
                continue
            relative = file_path.relative_to(root).as_posix()
            try:
                total_bytes += file_path.stat().st_size
            except OSError as exc:
                raise WorkspaceError("Could not inspect a file in the cloned repository.") from exc
            paths.append(relative)
            if len(paths) > MAX_ARCHIVE_ENTRIES:
                raise WorkspaceError("The repository has more than 10,000 supported files.", 413)
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise WorkspaceError("The repository has more than 200 MB of supported files.", 413)
    if not paths:
        raise WorkspaceError("This repository has no supported project files.")
    paths.sort(key=str.casefold)
    return paths, total_bytes


async def create_workspace_from_github(repo_url: str) -> dict[str, Any]:
    normalized_url, project_name = _normalize_github_url(repo_url)
    git_executable = shutil.which("git")
    if not git_executable:
        raise WorkspaceError("Git is not installed on the API host.", 503)

    workspace_id = str(uuid4())
    directory = WORKSPACE_ROOT / workspace_id
    directory.mkdir(parents=True, exist_ok=False)
    hooks_directory = Path(tempfile.mkdtemp(prefix="agent-lab-hooks-", dir=WORKSPACE_ROOT))
    try:
        environment = os.environ.copy()
        environment.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
        })
        process = await asyncio.create_subprocess_exec(
            git_executable,
            "-c", f"core.hooksPath={hooks_directory}",
            "-c", "protocol.file.allow=never",
            "clone", "--depth=1", "--single-branch", "--no-tags", "--no-recurse-submodules",
            normalized_url,
            str(directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise WorkspaceError("GitHub clone timed out after 3 minutes.", 504) from exc
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-1200:].strip()
            raise WorkspaceError(f"Could not clone this public repository. {message or 'Check the URL and try again.'}", 422)

        files, _ = _scan_cloned_files(directory)
        created_at = datetime.now(timezone.utc)
        result = {
            "id": workspace_id,
            "name": project_name,
            "source": "GitHub",
            "status": "ready",
            "file_count": len(files),
            "files_preview": files[:100],
            "project_overview": analyze_project(directory, files),
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + timedelta(days=RETENTION_DAYS)).isoformat(),
        }
        await _persist_metadata(result, directory)
        return result
    except WorkspaceError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise WorkspaceError("Could not import this GitHub repository.", 502) from exc
    finally:
        shutil.rmtree(hooks_directory, ignore_errors=True)


async def get_workspace(value: str) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(value)
    directory = WORKSPACE_ROOT / workspace_id
    metadata = await _read_metadata(workspace_id, directory)
    if not metadata:
        raise WorkspaceError("Workspace not found.", 404)
    expires_at = datetime.fromisoformat(metadata["expires_at"])
    if expires_at <= datetime.now(timezone.utc):
        shutil.rmtree(directory, ignore_errors=True)
        await _delete_metadata(workspace_id)
        raise WorkspaceError("This workspace has expired. Import the project again to continue.", 410)
    if "project_overview" not in metadata:
        files = discover_project_files(directory)
        metadata["file_count"] = len(files)
        metadata["files_preview"] = files[:100]
        metadata["project_overview"] = analyze_project(directory, files)
        await _persist_metadata(metadata, directory)
    return metadata


async def remove_expired_workspaces() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    for directory in WORKSPACE_ROOT.iterdir():
        if not directory.is_dir() or not re.fullmatch(r"[0-9a-f-]{36}", directory.name):
            continue
        metadata = await _read_metadata(directory.name, directory)
        expired = False
        if metadata:
            try:
                expired = datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(timezone.utc)
            except (KeyError, ValueError, TypeError):
                expired = directory.stat().st_mtime < cutoff.timestamp()
        else:
            expired = directory.stat().st_mtime < cutoff.timestamp()
        if expired:
            shutil.rmtree(directory, ignore_errors=True)
            await _delete_metadata(directory.name)
        await asyncio.sleep(0)
