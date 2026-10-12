from __future__ import annotations

from pathlib import Path
from typing import Any

from .project_overview import is_sensitive_project_file
from .workspaces import WorkspaceError, active_project_directory, get_workspace, _run_workspace_git

MAX_GIT_STATUS_ENTRIES = 200


def _safe_display(value: str, limit: int = 500) -> str:
    return "".join(character for character in value if character >= " " and character != "\x7f")[:limit]


def _parse_porcelain_status(output: str) -> tuple[str, list[dict[str, str]]]:
    records = output.split("\0")
    branch = "unknown"
    changes: list[dict[str, str]] = []
    for index, record in enumerate(records):
        if not record:
            continue
        if index == 0 and record.startswith("## "):
            branch = _safe_display(record[3:], 200)
            continue
        if len(record) < 4:
            continue
        status = record[:2]
        path = record[3:].replace("\\", "/")
        if not path or path.startswith("/") or any(part == ".." for part in Path(path).parts):
            continue
        if path.casefold() == "workspace.json" or is_sensitive_project_file(path):
            continue
        changes.append({"path": _safe_display(path), "status": status})
    return branch, changes


async def workspace_git_status(workspace_id: str) -> dict[str, Any]:
    metadata = await get_workspace(workspace_id)
    root = active_project_directory(workspace_id, metadata)
    git_directory = root / ".git"
    if git_directory.is_symlink():
        raise WorkspaceError("A symbolic .git path is not supported.", 422)
    if not (git_directory.is_dir() or git_directory.is_file()):
        return {
            "initialized": False,
            "branch": None,
            "clean": True,
            "changes": [],
            "truncated": False,
            "note": "This ZIP workspace does not have a Git baseline yet. Generating a draft from an approved plan creates an isolated Git baseline.",
        }

    exit_code, output, _ = await _run_workspace_git(
        workspace_id,
        root,
        ["status", "--porcelain=v1", "-z", "--branch", "--untracked-files=normal", "--no-renames"],
    )
    if exit_code != 0:
        raise WorkspaceError("Could not read Git status for this workspace.", 422)

    branch, changes = _parse_porcelain_status(output)
    truncated = len(changes) > MAX_GIT_STATUS_ENTRIES
    changes = changes[:MAX_GIT_STATUS_ENTRIES]
    return {
        "initialized": True,
        "branch": branch,
        "clean": not changes,
        "changes": changes,
        "truncated": truncated,
        "note": "Read-only status from the Agent Lab workspace. No files were changed; this view does not commit, checkout, or push.",
    }
