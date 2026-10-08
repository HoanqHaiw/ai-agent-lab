from __future__ import annotations

import asyncio
import json
import os
import subprocess
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from .project_overview import discover_project_files, is_sensitive_project_file
from .workspaces import WorkspaceError, active_project_directory, get_workspace

MAX_VERIFY_FILES = 2_000
MAX_VERIFY_BYTES = 100 * 1024 * 1024
VERIFY_TIMEOUT_SECONDS = 120
_verification_slots = asyncio.Semaphore(2)


def _docker_executable() -> str | None:
    configured = os.environ.get("DOCKER_EXECUTABLE")
    candidates = [configured, shutil.which("docker")]
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        program_files = os.environ.get("ProgramFiles")
        if local_app_data:
            candidates.append(str(Path(local_app_data) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"))
        if program_files:
            candidates.append(str(Path(program_files) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


def _verification_profile(root: Path) -> tuple[str, list[dict[str, Any]]]:
    files = set(discover_project_files(root))
    package_paths = [Path(name) for name in files if Path(name).name == "package.json"]
    if package_paths:
        package_path = sorted(package_paths, key=lambda path: len(path.parts))[0]
        try:
            package = json.loads((root / package_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            package = {}
        scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
        checks = [
            {"id": f"npm:{name}", "label": label, "manager": "npm", "image": "node:22-alpine", "cwd": package_path.parent.as_posix(), "args": ["npm", "run", name, "--if-present"]}
            for name, label in (("lint", "Lint"), ("typecheck", "Type check"), ("test", "Tests"), ("build", "Build"))
            if isinstance(scripts, dict) and isinstance(scripts.get(name), str) and scripts[name].strip()
        ]
        return "node", checks

    python_files = any(Path(name).suffix == ".py" for name in files)
    if python_files:
        syntax_scan = "import ast,pathlib; files=list(pathlib.Path('/workspace').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print(f'Parsed {len(files)} Python files')"
        checks = [{"id": "python:compile", "label": "Python syntax", "manager": "python", "image": "python:3.12-alpine", "cwd": ".", "args": ["python", "-c", syntax_scan]}]
        manifests = {Path(name).name.lower() for name in files}
        if "pyproject.toml" in manifests or "pytest.ini" in manifests or any("test" in Path(name).name.lower() for name in files):
            checks.append({"id": "python:pytest", "label": "pytest", "manager": "python", "image": "python:3.12-alpine", "cwd": ".", "args": ["python", "-m", "pytest", "-q"]})
        return "python", checks
    return "unknown", []


async def verification_options(workspace_id: str) -> dict[str, Any]:
    workspace = await get_workspace(workspace_id)
    root = active_project_directory(workspace_id, workspace)
    manager, checks = _verification_profile(root)
    return {
        "manager": manager,
        "checks": [{"id": item["id"], "label": item["label"]} for item in checks],
        "sandbox_available": _docker_executable() is not None,
        "note": "Checks run only in a disposable Docker container with no network. They can write only to a temporary copy of non-sensitive source files, which is discarded afterward. Package dependencies are not installed automatically.",
    }


def _copy_verification_snapshot(root: Path, destination: Path) -> None:
    paths = discover_project_files(root)
    if len(paths) > MAX_VERIFY_FILES:
        raise WorkspaceError("This repository has too many files for a verification snapshot.", 413)
    total_bytes = 0
    for relative in paths:
        if is_sensitive_project_file(relative):
            continue
        source = root / Path(relative)
        if source.is_symlink() or not source.is_file():
            continue
        total_bytes += source.stat().st_size
        if total_bytes > MAX_VERIFY_BYTES:
            raise WorkspaceError("The safe verification snapshot exceeds 100 MB.", 413)
        target = destination / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.parent.chmod(0o777)
        shutil.copyfile(source, target)
        target.chmod(0o666)
    for directory in (path for path in destination.rglob("*") if path.is_dir() and not path.is_symlink()):
        directory.chmod(0o777)
    destination.chmod(0o777)


def _run_container(image: str, args: list[str], snapshot: Path, relative_cwd: str) -> tuple[int, str]:
    docker = _docker_executable()
    if not docker:
        raise WorkspaceError("Docker is required to run repository checks. No source commands were executed.", 503)
    environment = os.environ.copy()
    docker_directory = str(Path(docker).parent)
    environment["PATH"] = docker_directory + os.pathsep + environment.get("PATH", "")
    container_name = f"agent-lab-verify-{uuid4().hex[:16]}"
    command = [
        docker, "run", "--rm", "--name", container_name, "--pull=missing", "--network=none", "--read-only",
        "--memory=768m", "--cpus=1", "--pids-limit=128", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--user=10001:10001",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
        "--mount", f"type=bind,src={snapshot.resolve()},dst=/workspace",
        "--tmpfs", "/root:rw,nosuid,size=64m",
        "--env", "HOME=/tmp",
        "--env", "npm_config_cache=/tmp/npm-cache",
        "--workdir", "/workspace" if relative_cwd == "." else f"/workspace/{relative_cwd}", image, *args,
    ]
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False, env=environment)
    except OSError as exc:
        raise WorkspaceError(f"Could not start Docker: {exc}", 503) from exc

    output_parts: list[str] = []
    output_size = 0

    def collect_output() -> None:
        nonlocal output_size
        if process.stdout is None:
            return
        while chunk := process.stdout.read(4096):
            remaining = 20_000 - output_size
            if remaining > 0:
                decoded = chunk.decode("utf-8", errors="replace")[:remaining]
                output_parts.append(decoded)
                output_size += len(decoded)

    reader = threading.Thread(target=collect_output, daemon=True)
    reader.start()
    try:
        return_code = process.wait(timeout=VERIFY_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=2)
        subprocess.run([docker, "kill", container_name], capture_output=True, timeout=5, check=False, shell=False, env=environment)
        subprocess.run([docker, "rm", "-f", container_name], capture_output=True, timeout=5, check=False, shell=False, env=environment)
        return 124, ("".join(output_parts) + "\nCheck timed out after 120 seconds.").strip()
    reader.join(timeout=2)
    output = "".join(output_parts)
    if output_size >= 20_000:
        output += "\n[Output truncated at 20,000 characters.]"
    return return_code, output.strip()


async def run_verification_check(workspace_id: str, check_id: str) -> dict[str, Any]:
    workspace = await get_workspace(workspace_id)
    root = active_project_directory(workspace_id, workspace)
    manager, checks = _verification_profile(root)
    selected = next((item for item in checks if item["id"] == check_id), None)
    if not selected:
        raise WorkspaceError("This verification check is not allowed for the detected project.", 400)
    if not _docker_executable():
        raise WorkspaceError("Docker is required to run repository checks. No source commands were executed.", 503)
    async with _verification_slots:
        with tempfile.TemporaryDirectory(prefix="agent-lab-verify-") as temporary:
            snapshot = Path(temporary) / "source"
            snapshot.mkdir()
            await asyncio.to_thread(_copy_verification_snapshot, root, snapshot)
            try:
                return_code, output = await asyncio.wait_for(
                    asyncio.to_thread(_run_container, selected["image"], selected["args"], snapshot, selected["cwd"]),
                    timeout=VERIFY_TIMEOUT_SECONDS + 10,
                )
            except TimeoutError:
                return_code, output = 124, "Check exceeded the 120-second safety limit."
    return {
        "check_id": selected["id"], "label": selected["label"], "status": "passed" if return_code == 0 else "failed",
        "exit_code": return_code, "output": output or "Command completed with no output.", "manager": manager,
    }
