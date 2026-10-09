from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
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
from .project_overview import PROJECT_OVERVIEW_VERSION, analyze_project, discover_project_files, is_sensitive_project_file

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
RETENTION_DAYS = 7
MAX_WORKSPACE_LIST = 100
CHUNK_SIZE = 1024 * 1024
IGNORED_PARTS = {
    ".git", "node_modules", "dist", "build", ".next", ".venv", "venv",
    "coverage", "vendor", "__pycache__", ".cache",
}

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT") or Path(__file__).resolve().parents[1] / ".workspaces").resolve()
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)


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


async def create_workspace_from_zip(upload: UploadFile, owner_id: str | None = None) -> dict[str, Any]:
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
                    relative_name = relative_path.as_posix()
                    if not is_sensitive_project_file(relative_name):
                        extracted_files.append(relative_name)
        except BadZipFile as exc:
            raise WorkspaceError("The selected file is not a valid ZIP archive.") from exc

        if not extracted_files:
            raise WorkspaceError("The ZIP has no supported project files after generated folders are excluded.")

        extracted_files.sort(key=str.casefold)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(days=RETENTION_DAYS)
        result = {
            "id": workspace_id,
            "owner_id": owner_id,
            "name": project_name,
            "source": "ZIP upload",
            "status": "ready",
            "file_count": len(extracted_files),
            "files_preview": extracted_files[:100],
            "project_overview": analyze_project(directory, extracted_files),
            "project_overview_version": PROJECT_OVERVIEW_VERSION,
            "analyses": [],
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
            if is_sensitive_project_file(relative):
                continue
            paths.append(relative)
            if len(paths) > MAX_ARCHIVE_ENTRIES:
                raise WorkspaceError("The repository has more than 10,000 supported files.", 413)
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise WorkspaceError("The repository has more than 200 MB of supported files.", 413)
    if not paths:
        raise WorkspaceError("This repository has no supported project files.")
    paths.sort(key=str.casefold)
    return paths, total_bytes


async def create_workspace_from_github(repo_url: str, owner_id: str | None = None) -> dict[str, Any]:
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
        try:
            process = await asyncio.to_thread(
                subprocess.run,
                [
                    git_executable,
                    "-c", f"core.hooksPath={hooks_directory}",
                    "-c", "protocol.file.allow=never",
                    "clone", "--quiet", "--depth=1", "--single-branch", "--no-tags", "--no-recurse-submodules",
                    normalized_url,
                    str(directory),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError("GitHub clone timed out after 3 minutes.", 504) from exc
        if process.returncode != 0:
            message = process.stderr.decode("utf-8", errors="replace")[-1200:].strip()
            raise WorkspaceError(f"Could not clone this public repository. {message or 'Check the URL and try again.'}", 422)

        files, _ = _scan_cloned_files(directory)
        created_at = datetime.now(timezone.utc)
        result = {
            "id": workspace_id,
            "owner_id": owner_id,
            "name": project_name,
            "source": "GitHub",
            "status": "ready",
            "file_count": len(files),
            "files_preview": files[:100],
            "project_overview": analyze_project(directory, files),
            "project_overview_version": PROJECT_OVERVIEW_VERSION,
            "analyses": [],
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + timedelta(days=RETENTION_DAYS)).isoformat(),
        }
        await _persist_metadata(result, directory)
        return result
    except WorkspaceError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        logger.exception("Unexpected failure importing GitHub workspace %s", workspace_id)
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
        shutil.rmtree(WORKSPACE_ROOT / ".agent-lab-drafts" / workspace_id, ignore_errors=True)
        shutil.rmtree(WORKSPACE_ROOT / ".agent-lab-worktrees" / workspace_id, ignore_errors=True)
        raise WorkspaceError("This workspace has expired. Import the project again to continue.", 410)
    project_directory = active_project_directory(workspace_id, metadata)
    if metadata.get("project_overview_version") != PROJECT_OVERVIEW_VERSION:
        files = discover_project_files(project_directory)
        metadata["file_count"] = len(files)
        metadata["files_preview"] = files[:100]
        metadata["project_overview"] = analyze_project(project_directory, files)
        metadata["project_overview_version"] = PROJECT_OVERVIEW_VERSION
        await _persist_metadata(metadata, directory)
    if "analyses" not in metadata:
        metadata["analyses"] = []
        await _persist_metadata(metadata, directory)
    return metadata


def _workspace_listing_record(metadata: dict[str, Any]) -> dict[str, Any] | None:
    if (
        not isinstance(metadata.get("id"), str)
        or not isinstance(metadata.get("name"), str)
        or metadata.get("source") not in {"GitHub", "ZIP upload"}
    ):
        return None
    try:
        if str(UUID(metadata["id"])) != metadata["id"]:
            return None
    except ValueError:
        return None
    try:
        expires_at = datetime.fromisoformat(metadata["expires_at"])
        created_at = datetime.fromisoformat(metadata["created_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return None
    fields = (
        "id", "name", "source", "status", "file_count", "files_preview",
        "project_overview", "analyses", "chat_messages", "code_reviews",
        "implementation_branch", "created_at", "expires_at",
    )
    record = {field: metadata[field] for field in fields if field in metadata}
    record["created_at"] = created_at.astimezone(timezone.utc).isoformat()
    record["expires_at"] = expires_at.astimezone(timezone.utc).isoformat()
    return record


async def list_workspaces(owner_id: str | None) -> list[dict[str, Any]]:
    if owner_id is None:
        return []
    mongo_uri = os.environ.get("MONGODB_URI")
    if mongo_uri:
        try:
            from pymongo import AsyncMongoClient

            client = AsyncMongoClient(mongo_uri, serverSelectionTimeoutMS=4000)
            try:
                database_name = os.environ.get("MONGODB_DATABASE") or "agent_lab"
                collection = client[database_name].workspaces
                cursor = (
                    collection.find({
                        "owner_id": owner_id,
                        "expires_at": {"$gt": datetime.now(timezone.utc).isoformat()},
                    })
                    .sort("created_at", -1)
                    .limit(MAX_WORKSPACE_LIST)
                )
                records = await cursor.to_list(length=MAX_WORKSPACE_LIST)
            finally:
                await client.close()
        except Exception as exc:
            raise WorkspaceError("Could not list workspaces from MongoDB. Check MONGODB_URI and Atlas network access.", 503) from exc
    else:
        records = []
        directories = []
        for directory in WORKSPACE_ROOT.iterdir():
            if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r"[0-9a-f-]{36}", directory.name):
                continue
            try:
                modified_at = directory.stat().st_mtime
            except OSError:
                continue
            directories.append((modified_at, directory))
        directories.sort(key=lambda item: item[0], reverse=True)
        for _, directory in directories[:MAX_WORKSPACE_LIST * 5]:
            metadata = await _read_metadata(directory.name, directory)
            if (
                isinstance(metadata, dict)
                and metadata.get("id") == directory.name
                and metadata.get("owner_id") == owner_id
            ):
                records.append(metadata)

    listed = [
        item
        for metadata in records
        if isinstance(metadata, dict) and (item := _workspace_listing_record(metadata)) is not None
    ]
    listed.sort(key=lambda item: datetime.fromisoformat(item["created_at"]), reverse=True)
    return listed[:MAX_WORKSPACE_LIST]


def active_project_directory(workspace_id: str, metadata: dict[str, Any]) -> Path:
    safe_id = _safe_workspace_id(workspace_id)
    if metadata.get("implementation_worktree"):
        worktree = WORKSPACE_ROOT / ".agent-lab-worktrees" / safe_id
        if not worktree.is_dir() or worktree.is_symlink():
            raise WorkspaceError("The isolated implementation worktree is missing. Re-import the repository to continue.", 410)
        return worktree
    return WORKSPACE_ROOT / safe_id


async def _run_workspace_git(workspace_id: str, cwd: Path, arguments: list[str], input_data: bytes | None = None) -> tuple[int, str, str]:
    git_executable = shutil.which("git")
    if not git_executable:
        raise WorkspaceError("Git is required to prepare an isolated implementation workspace.", 503)
    _safe_workspace_id(workspace_id)
    app_git_root = WORKSPACE_ROOT / ".agent-lab-git"
    hooks_directory = app_git_root / "empty-hooks"
    template_directory = app_git_root / "empty-template"
    global_config = app_git_root / "empty-config"
    app_git_root.mkdir(parents=True, exist_ok=True)
    hooks_directory.mkdir(exist_ok=True)
    template_directory.mkdir(exist_ok=True)
    global_config.touch(exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(global_config), "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    process = await asyncio.create_subprocess_exec(
        git_executable, "-c", f"core.hooksPath={hooks_directory}", "-c", f"init.templateDir={template_directory}",
        *arguments, cwd=cwd, stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=environment,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(input=input_data), timeout=120)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise WorkspaceError("Git took too long to prepare the implementation workspace.", 504) from exc
    return process.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


async def ensure_implementation_worktree(value: str) -> tuple[dict[str, Any], Path]:
    workspace_id = _safe_workspace_id(value)
    metadata = await get_workspace(workspace_id)
    base_directory = WORKSPACE_ROOT / workspace_id
    worktree_directory = WORKSPACE_ROOT / ".agent-lab-worktrees" / workspace_id
    if metadata.get("implementation_worktree"):
        if not worktree_directory.is_dir() or worktree_directory.is_symlink():
            raise WorkspaceError("The isolated implementation worktree is missing. Re-import the repository to continue.", 410)
        return metadata, worktree_directory
    worktree_directory.parent.mkdir(parents=True, exist_ok=True)
    if worktree_directory.exists() or worktree_directory.is_symlink():
        raise WorkspaceError("A stale implementation worktree already exists. Re-import the repository before generating another draft.", 409)
    git_directory = base_directory / ".git"
    if git_directory.is_symlink():
        raise WorkspaceError("A symbolic .git directory is not supported for isolated code changes.", 422)
    is_existing_repository = git_directory.is_dir() or git_directory.is_file()
    if not is_existing_repository:
        status, _, _ = await _run_workspace_git(workspace_id, base_directory, ["init", "--quiet"])
        if status != 0:
            raise WorkspaceError("Could not initialize a temporary Git baseline for this ZIP workspace.", 422)

    files = discover_project_files(base_directory)
    if not files:
        raise WorkspaceError("No supported source files are available for an isolated implementation worktree.", 422)
    pathspecs = b"\0".join(path.encode("utf-8") for path in files) + b"\0"
    status, _, _ = await _run_workspace_git(
        workspace_id, base_directory,
        ["add", "--all", "--force", "--pathspec-from-file=-", "--pathspec-file-nul"], pathspecs,
    )
    if status != 0:
        raise WorkspaceError("Could not stage the safe project files for an isolated implementation worktree.", 422)

    status, _, _ = await _run_workspace_git(workspace_id, base_directory, ["diff", "--cached", "--quiet"])
    if status == 1 or not is_existing_repository:
        branch = f"agent-lab/impl-{workspace_id[:8]}"
        status, _, _ = await _run_workspace_git(
            workspace_id, base_directory,
            ["-c", "user.name=AI Agent Lab", "-c", "user.email=agent-lab@localhost", "commit", "--quiet", "--allow-empty", "-m", "Agent Lab workspace checkpoint"],
        )
        if status != 0:
            raise WorkspaceError("Could not create a private checkpoint for the isolated implementation worktree.", 422)
    elif status != 0:
        raise WorkspaceError("Could not inspect the Git baseline for the isolated implementation worktree.", 422)
    else:
        branch = f"agent-lab/impl-{workspace_id[:8]}"

    status, _, _ = await _run_workspace_git(
        workspace_id, base_directory,
        ["worktree", "add", "--quiet", "-B", branch, str(worktree_directory), "HEAD"],
    )
    if status != 0:
        raise WorkspaceError("Could not create the isolated Git worktree for implementation.", 422)
    metadata["implementation_worktree"] = True
    metadata["implementation_branch"] = branch
    await _persist_metadata(metadata, base_directory)
    files = discover_project_files(worktree_directory)
    metadata["file_count"] = len(files)
    metadata["files_preview"] = files[:100]
    metadata["project_overview"] = analyze_project(worktree_directory, files)
    metadata["project_overview_version"] = PROJECT_OVERVIEW_VERSION
    await _persist_metadata(metadata, base_directory)
    return metadata, worktree_directory


def ensure_workspace_owner(metadata: dict[str, Any], user_id: str | None) -> None:
    owner_id = metadata.get("owner_id")
    if owner_id != user_id:
        raise WorkspaceError("Workspace not found.", 404)


async def read_workspace_file(value: str, file_path: str) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(value)
    metadata = await get_workspace(workspace_id)
    directory = active_project_directory(workspace_id, metadata)
    if not file_path or "\x00" in file_path or "\\" in file_path:
        raise WorkspaceError("Enter a valid workspace file path.")
    relative_path = PurePosixPath(file_path)
    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise WorkspaceError("Enter a valid workspace file path.")
    if file_path not in discover_project_files(directory):
        raise WorkspaceError("Workspace file not found or unavailable for preview.", 404)

    basename = relative_path.name.casefold()
    sensitive_parts = {".aws", ".azure", ".gcloud", ".ssh", ".vercel", "secrets", "certificates"}
    if (
        any(part.casefold() in sensitive_parts for part in relative_path.parts[:-1])
        or basename.startswith(".env")
        or basename in {".npmrc", ".pypirc", ".netrc", "credentials", "service-account.json"}
        or any(marker in basename for marker in ("secret", "credential", "token"))
        or basename.endswith((".pem", ".key", ".p12", ".pfx"))
    ):
        raise WorkspaceError("Sensitive configuration files cannot be previewed.", 403)

    target = directory.joinpath(*relative_path.parts)
    if any((directory / Path(*relative_path.parts[:index])).is_symlink() for index in range(1, len(relative_path.parts) + 1)):
        raise WorkspaceError("Symbolic links cannot be previewed.", 403)
    resolved = target.resolve()
    if not resolved.is_relative_to(directory.resolve()) or not resolved.is_file():
        raise WorkspaceError("Workspace file not found.", 404)
    if resolved.stat().st_size > 1024 * 1024:
        raise WorkspaceError("Files larger than 1 MB cannot be previewed.", 413)
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError("This file is not UTF-8 text and cannot be previewed.", 415) from exc
    except OSError as exc:
        raise WorkspaceError("Could not read this workspace file.", 500) from exc
    return {"path": relative_path.as_posix(), "content": content, "line_count": len(content.splitlines())}


async def save_workspace_analysis(value: str, task: str, result: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(value)
    directory = WORKSPACE_ROOT / workspace_id
    metadata = await get_workspace(workspace_id)
    record = {
        "id": str(uuid4()),
        "task": task,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    analyses = metadata.setdefault("analyses", [])
    analyses.append(record)
    metadata["analyses"] = analyses[-20:]
    await _persist_metadata(metadata, directory)
    return record


async def approve_workspace_analysis(value: str, analysis_id: str) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(value)
    directory = WORKSPACE_ROOT / workspace_id
    metadata = await get_workspace(workspace_id)
    for record in metadata.get("analyses", []):
        if record.get("id") == analysis_id:
            record["approved_at"] = record.get("approved_at") or datetime.now(timezone.utc).isoformat()
            await _persist_metadata(metadata, directory)
            return record
    raise WorkspaceError("Analysis not found in this workspace.", 404)


async def save_workspace_chat_turn(value: str, question: str, result: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(value)
    directory = WORKSPACE_ROOT / workspace_id
    metadata = await get_workspace(workspace_id)
    turn = {
        "id": str(uuid4()),
        "question": question,
        "answer": result["answer"],
        "relevant_files": result["relevant_files"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    history = metadata.setdefault("chat_messages", [])
    history.append(turn)
    metadata["chat_messages"] = history[-50:]
    await _persist_metadata(metadata, directory)
    return turn


async def save_workspace_code_review(value: str, result: dict[str, Any]) -> dict[str, Any]:
    workspace_id = _safe_workspace_id(value)
    directory = WORKSPACE_ROOT / workspace_id
    metadata = await get_workspace(workspace_id)
    record = {
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    reviews = metadata.setdefault("code_reviews", [])
    reviews.append(record)
    metadata["code_reviews"] = reviews[-20:]
    await _persist_metadata(metadata, directory)
    return record


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
            shutil.rmtree(WORKSPACE_ROOT / ".agent-lab-drafts" / directory.name, ignore_errors=True)
            shutil.rmtree(WORKSPACE_ROOT / ".agent-lab-worktrees" / directory.name, ignore_errors=True)
        await asyncio.sleep(0)

    draft_root = WORKSPACE_ROOT / ".agent-lab-drafts"
    if draft_root.is_dir():
        for draft_workspace in draft_root.iterdir():
            if re.fullmatch(r"[0-9a-f-]{36}", draft_workspace.name) and not (WORKSPACE_ROOT / draft_workspace.name).is_dir():
                shutil.rmtree(draft_workspace, ignore_errors=True)
    worktree_root = WORKSPACE_ROOT / ".agent-lab-worktrees"
    if worktree_root.is_dir():
        for worktree in worktree_root.iterdir():
            if re.fullmatch(r"[0-9a-f-]{36}", worktree.name) and not (WORKSPACE_ROOT / worktree.name).is_dir():
                shutil.rmtree(worktree, ignore_errors=True)
