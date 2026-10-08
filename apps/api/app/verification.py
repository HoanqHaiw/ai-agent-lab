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
from urllib.parse import urlsplit
from uuid import uuid4

from .project_overview import discover_project_files, is_sensitive_project_file
from .workspaces import WorkspaceError, active_project_directory, get_workspace

MAX_VERIFY_FILES = 2_000
MAX_VERIFY_BYTES = 100 * 1024 * 1024
VERIFY_TIMEOUT_SECONDS = 120
DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 300
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


MAX_PACKAGE_MANIFESTS = 20
MAX_LOCKFILE_BYTES = 20 * 1024 * 1024


def _npm_lockfile_problem(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_LOCKFILE_BYTES:
            return "The npm lockfile is larger than the 20 MB safety limit."
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "The npm lockfile is missing or invalid."
    if not isinstance(lock, dict) or lock.get("lockfileVersion") not in {1, 2, 3}:
        return "Only npm lockfile versions 1, 2, and 3 are supported."

    pending: list[Any] = [lock]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            resolved = value.get("resolved")
            if isinstance(resolved, str):
                parsed = urlsplit(resolved)
                try:
                    invalid_registry_url = (
                        parsed.scheme != "https"
                        or parsed.hostname != "registry.npmjs.org"
                        or parsed.username is not None
                        or parsed.password is not None
                        or parsed.port is not None
                    )
                except ValueError:
                    invalid_registry_url = True
                if invalid_registry_url:
                    return "The lockfile contains a dependency URL outside the approved npm registry."
            elif isinstance(value.get("version"), str) and value["version"].startswith(("file:", "git:", "git+", "http:", "https:", "github:")):
                return "The lockfile contains a non-registry dependency source."
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return None


def _verification_profile(root: Path) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    files = set(discover_project_files(root))
    package_paths = sorted((Path(name) for name in files if Path(name).name == "package.json"), key=lambda path: path.as_posix().casefold())
    if package_paths:
        if len(package_paths) > MAX_PACKAGE_MANIFESTS:
            return "node", [], [{"label": "Node.js checks", "reason": f"The repository has more than {MAX_PACKAGE_MANIFESTS} package manifests."}]
        checks: list[dict[str, Any]] = []
        blocked: list[dict[str, str]] = []
        labels = (("lint", "Lint"), ("typecheck", "Type check"), ("test", "Tests"), ("build", "Build"))
        for package_index, package_path in enumerate(package_paths, start=1):
            try:
                package = json.loads((root / package_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                blocked.append({"label": package_path.parent.as_posix() or "root", "reason": "package.json is invalid."})
                continue
            if not isinstance(package, dict):
                continue
            scripts = package.get("scripts", {})
            declared = [(name, label) for name, label in labels if isinstance(scripts, dict) and isinstance(scripts.get(name), str) and scripts[name].strip()]
            if not declared:
                continue

            package_dir = package_path.parent
            lock_path = next((root / package_dir / lock_name for lock_name in ("package-lock.json", "npm-shrinkwrap.json") if (root / package_dir / lock_name).is_file()), None)
            install_cwd = package_dir.as_posix() or "."
            if lock_path is None:
                ancestors = [candidate for candidate in package_paths if candidate.parent != package_dir and package_dir.is_relative_to(candidate.parent)]
                nearest_parent = max((candidate.parent for candidate in ancestors), key=lambda path: len(path.parts), default=None)
                if nearest_parent is not None:
                    candidate_lock = next((root / nearest_parent / lock_name for lock_name in ("package-lock.json", "npm-shrinkwrap.json") if (root / nearest_parent / lock_name).is_file()), None)
                    if candidate_lock is not None:
                        try:
                            parent_package = json.loads((root / nearest_parent / "package.json").read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                            parent_package = {}
                        if isinstance(parent_package, dict) and parent_package.get("workspaces"):
                            lock_path = candidate_lock
                            install_cwd = nearest_parent.as_posix() or "."
            requires_install = bool(package.get("dependencies") or package.get("devDependencies") or package.get("optionalDependencies"))
            lock_problem = None
            if requires_install and lock_path is None:
                lock_problem = "A package lockfile is required before dependencies can be prepared safely."
            elif lock_path is not None:
                lock_problem = _npm_lockfile_problem(lock_path)
            if lock_problem:
                blocked.extend({"label": f"{package_dir.as_posix() or 'root'} · {label}", "reason": lock_problem} for _, label in declared)
                continue

            for name, label in declared:
                checks.append({
                    "id": f"npm:{package_index}:{name}",
                    "label": f"{package_dir.as_posix() or 'root'} · {label}" if len(package_paths) > 1 else label,
                    "manager": "npm",
                    "image": "node:22-alpine",
                    "cwd": package_dir.as_posix() or ".",
                    "install_cwd": install_cwd,
                    "install": requires_install,
                    "args": ["npm", "run", name, "--if-present"],
                    "install_args": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org"],
                })
        return "node", checks, blocked

    python_files = any(Path(name).suffix == ".py" for name in files)
    if python_files:
        syntax_scan = "import ast,pathlib; files=list(pathlib.Path('/workspace').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print(f'Parsed {len(files)} Python files')"
        checks = [{"id": "python:compile", "label": "Python syntax", "manager": "python", "image": "python:3.12-alpine", "cwd": ".", "args": ["python", "-c", syntax_scan]}]
        manifests = {Path(name).name.lower() for name in files}
        if "pyproject.toml" in manifests or "pytest.ini" in manifests or any("test" in Path(name).name.lower() for name in files):
            checks.append({"id": "python:pytest", "label": "pytest", "manager": "python", "image": "python:3.12-alpine", "cwd": ".", "args": ["python", "-m", "pytest", "-q"]})
        return "python", checks, []
    return "unknown", [], []


async def verification_options(workspace_id: str) -> dict[str, Any]:
    workspace = await get_workspace(workspace_id)
    root = active_project_directory(workspace_id, workspace)
    manager, checks, blocked_checks = _verification_profile(root)
    return {
        "manager": manager,
        "checks": [{"id": item["id"], "label": item["label"], "dependency_install": item.get("install", False)} for item in checks],
        "blocked_checks": blocked_checks,
        "sandbox_available": _docker_executable() is not None,
        "note": "For npm projects with a supported lockfile, dependencies are prepared in a disposable copy with lifecycle scripts disabled. That preparation container has internet access and is configured to use registry.npmjs.org; the selected repository check then runs in a separate network-disabled container. The temporary copy is discarded.",
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


def _run_container(
    image: str,
    args: list[str],
    snapshot: Path,
    relative_cwd: str,
    *,
    network: str = "none",
    timeout_seconds: int = VERIFY_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    docker = _docker_executable()
    if not docker:
        raise WorkspaceError("Docker is required to run repository checks. No source commands were executed.", 503)
    environment = os.environ.copy()
    docker_directory = str(Path(docker).parent)
    environment["PATH"] = docker_directory + os.pathsep + environment.get("PATH", "")
    container_name = f"agent-lab-verify-{uuid4().hex[:16]}"
    command = [
        docker, "run", "--rm", "--name", container_name, "--pull=missing", f"--network={network}",
        *( ["--read-only"] if network == "none" else [] ),
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
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=2)
        subprocess.run([docker, "kill", container_name], capture_output=True, timeout=5, check=False, shell=False, env=environment)
        subprocess.run([docker, "rm", "-f", container_name], capture_output=True, timeout=5, check=False, shell=False, env=environment)
        return 124, ("".join(output_parts) + f"\nContainer command timed out after {timeout_seconds} seconds.").strip()
    reader.join(timeout=2)
    output = "".join(output_parts)
    if output_size >= 20_000:
        output += "\n[Output truncated at 20,000 characters.]"
    return return_code, output.strip()


async def run_verification_check(workspace_id: str, check_id: str) -> dict[str, Any]:
    workspace = await get_workspace(workspace_id)
    root = active_project_directory(workspace_id, workspace)
    manager, checks, _ = _verification_profile(root)
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
            if selected.get("install"):
                install_args = selected["install_args"]
                try:
                    install_code, install_output = await asyncio.wait_for(
                        asyncio.to_thread(
                            _run_container,
                            selected["image"],
                            install_args,
                            snapshot,
                            selected["install_cwd"],
                            network="bridge",
                            timeout_seconds=DEPENDENCY_INSTALL_TIMEOUT_SECONDS,
                        ),
                        timeout=DEPENDENCY_INSTALL_TIMEOUT_SECONDS + 10,
                    )
                except TimeoutError:
                    install_code, install_output = 124, "Dependency preparation exceeded the 300-second safety limit."
                if install_code != 0:
                    output = "Dependency preparation failed; the repository check was not run.\n\n" + (install_output or "npm ci returned no output.")
                    return {
                        "check_id": selected["id"], "label": selected["label"], "status": "failed",
                        "exit_code": install_code, "output": output, "manager": manager,
                        "phase": "dependency-install",
                    }
            try:
                return_code, output = await asyncio.wait_for(
                    asyncio.to_thread(
                        _run_container,
                        selected["image"],
                        selected["args"],
                        snapshot,
                        selected["cwd"],
                        network="none",
                        timeout_seconds=VERIFY_TIMEOUT_SECONDS,
                    ),
                    timeout=VERIFY_TIMEOUT_SECONDS + 10,
                )
            except TimeoutError:
                return_code, output = 124, "Check exceeded the 120-second safety limit."
    return {
        "check_id": selected["id"], "label": selected["label"], "status": "passed" if return_code == 0 else "failed",
        "exit_code": return_code, "output": output or "Command completed with no output.", "manager": manager,
        "phase": "check",
    }
