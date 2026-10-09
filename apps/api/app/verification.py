from __future__ import annotations

import asyncio
import fnmatch
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


def _docker_status() -> tuple[bool, str | None]:
    docker = _docker_executable()
    if not docker:
        return False, "Docker CLI is not installed or could not be found by the API."
    environment = os.environ.copy()
    environment["PATH"] = str(Path(docker).parent) + os.pathsep + environment.get("PATH", "")
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "Docker CLI was found, but the Docker daemon did not respond."
    if result.returncode != 0:
        return False, "Docker CLI was found, but the Docker daemon is unavailable."
    return True, None


MAX_PACKAGE_MANIFESTS = 20
MAX_PACKAGE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_LOCKFILE_BYTES = 20 * 1024 * 1024
PACKAGE_LOCKFILES = {
    "npm": ("npm-shrinkwrap.json", "package-lock.json"),
    "pnpm": ("pnpm-lock.yaml",),
    "yarn": ("yarn.lock",),
    "bun": ("bun.lock", "bun.lockb"),
}


def _dependency_network_allowed() -> bool:
    return os.environ.get("VERIFICATION_ALLOW_DEPENDENCY_NETWORK", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _declared_package_manager(package: dict[str, Any]) -> str | None:
    value = package.get("packageManager")
    if not isinstance(value, str) or not value.strip():
        return None
    manager = value.strip().split("@", 1)[0].casefold()
    return manager or None


def _lockfiles(directory: Path) -> list[tuple[str, Path]]:
    found = []
    for manager, names in PACKAGE_LOCKFILES.items():
        for name in names:
            candidate = directory / name
            if not candidate.is_symlink() and candidate.is_file():
                found.append((manager, candidate))
    return found


def _workspace_patterns(package: dict[str, Any]) -> list[str]:
    workspaces = package.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if not isinstance(workspaces, list):
        return []
    return [
        item.strip().replace("\\", "/").removeprefix("./").strip("/")
        for item in workspaces
        if isinstance(item, str) and item.strip()
    ]


def _workspace_pattern_matches(relative: str, pattern: str) -> bool:
    path_parts = relative.split("/")
    pattern_parts = pattern.split("/")
    previous = [True, *([False] * len(path_parts))]
    for pattern_part in pattern_parts:
        current = [False] * (len(path_parts) + 1)
        if pattern_part == "**":
            current[0] = previous[0]
            for path_index in range(1, len(path_parts) + 1):
                current[path_index] = previous[path_index] or current[path_index - 1]
        else:
            for path_index in range(1, len(path_parts) + 1):
                current[path_index] = previous[path_index - 1] and fnmatch.fnmatchcase(
                    path_parts[path_index - 1],
                    pattern_part,
                )
        previous = current
    return previous[-1]


def _is_npm_workspace_member(workspace_root: Path, package_directory: Path, patterns: list[str]) -> bool:
    try:
        relative = package_directory.relative_to(workspace_root).as_posix()
    except ValueError:
        return False
    matched = False
    for pattern in patterns:
        excluded = pattern.startswith("!")
        candidate = pattern[1:] if excluded else pattern
        if _workspace_pattern_matches(relative, candidate):
            matched = not excluded
    return matched


def _npm_lockfile_problem(path: Path) -> str | None:
    if path.is_symlink():
        return "Symbolic links cannot be used as npm lockfiles."
    try:
        if path.stat().st_size > MAX_LOCKFILE_BYTES:
            return "The npm lockfile is larger than the 20 MB safety limit."
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return "The npm lockfile is missing or invalid."
    if not isinstance(lock, dict) or lock.get("lockfileVersion") not in {1, 2, 3}:
        return "Only npm lockfile versions 1, 2, and 3 are supported."

    pending: list[Any] = [lock]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            resolved = value.get("resolved")
            if isinstance(resolved, str):
                try:
                    parsed = urlsplit(resolved)
                    invalid_registry_url = (
                        parsed.scheme != "https"
                        or parsed.hostname != "registry.npmjs.org"
                        or parsed.username is not None
                        or parsed.password is not None
                        or parsed.port is not None
                        or bool(parsed.query)
                        or bool(parsed.fragment)
                    )
                except (TypeError, ValueError):
                    invalid_registry_url = True
                if invalid_registry_url:
                    return "The lockfile contains a dependency URL outside the approved npm registry."
            version = value.get("version")
            if isinstance(version, str) and version.strip().casefold().startswith(("file:", "git:", "git+", "http:", "https:", "github:")):
                return "The lockfile contains a non-registry dependency source."
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return None


def _verification_profile(root: Path) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    files = set(discover_project_files(root))
    package_paths = sorted((Path(name) for name in files if Path(name).name == "package.json"), key=lambda path: path.as_posix().casefold())
    checks: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    detected_managers: set[str] = set()
    if package_paths:
        if len(package_paths) > MAX_PACKAGE_MANIFESTS:
            blocked.append({"label": "Node.js checks", "reason": f"The repository has more than {MAX_PACKAGE_MANIFESTS} package manifests."})
            package_paths = []

        parsed_packages: dict[Path, dict[str, Any]] = {}
        labels = (("lint", "Lint"), ("typecheck", "Type check"), ("test", "Tests"), ("build", "Build"))
        for package_path in package_paths:
            manifest_path = root / package_path
            if manifest_path.is_symlink():
                blocked.append({"label": package_path.parent.as_posix() or "root", "reason": "package.json cannot be a symbolic link."})
                continue
            try:
                if manifest_path.stat().st_size > MAX_PACKAGE_MANIFEST_BYTES:
                    blocked.append({
                        "label": package_path.parent.as_posix() or "root",
                        "reason": "package.json exceeds the 2 MB verification profile limit.",
                    })
                    continue
            except OSError:
                blocked.append({"label": package_path.parent.as_posix() or "root", "reason": "package.json could not be read."})
                continue
            try:
                package = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                blocked.append({"label": package_path.parent.as_posix() or "root", "reason": "package.json is invalid."})
                continue
            if not isinstance(package, dict):
                blocked.append({"label": package_path.parent.as_posix() or "root", "reason": "package.json must contain a JSON object."})
                continue
            parsed_packages[package_path.parent] = package

        for package_dir, package in parsed_packages.items():
            manager = _declared_package_manager(package)
            lock_managers = {name for name, _ in _lockfiles(root / package_dir)}
            if manager:
                detected_managers.add(manager)
            detected_managers.update(lock_managers)

        for package_index, package_path in enumerate(package_paths, start=1):
            package_dir = package_path.parent
            package = parsed_packages.get(package_dir)
            if package is None:
                continue
            scripts = package.get("scripts", {})
            declared = [(name, label) for name, label in labels if isinstance(scripts, dict) and isinstance(scripts.get(name), str) and scripts[name].strip()]
            if not declared:
                continue

            manager = _declared_package_manager(package)
            own_lockfiles = _lockfiles(root / package_dir)
            install_cwd = package_dir.as_posix() or "."
            lock_path = next((path for lock_manager, path in own_lockfiles if lock_manager == "npm"), None)
            lock_managers = {lock_manager for lock_manager, _ in own_lockfiles}
            manager_problem = None
            if len(lock_managers) > 1:
                manager_problem = "Multiple package-manager lockfiles are present beside this package."
            elif manager and lock_managers and manager not in lock_managers:
                manager_problem = f"packageManager declares {manager}, but the adjacent lockfile belongs to another manager."
            elif manager is None and lock_managers:
                manager = next(iter(lock_managers))

            if not own_lockfiles and not manager_problem:
                ancestors = sorted(
                    (candidate for candidate in parsed_packages if candidate != package_dir and package_dir.is_relative_to(candidate)),
                    key=lambda candidate: len(candidate.parts),
                    reverse=True,
                )
                for workspace_root in ancestors:
                    workspace_package = parsed_packages[workspace_root]
                    patterns = _workspace_patterns(workspace_package)
                    has_pnpm_workspace = (root / workspace_root / "pnpm-workspace.yaml").is_file()
                    is_workspace = bool(patterns) and _is_npm_workspace_member(workspace_root, package_dir, patterns)
                    is_workspace = is_workspace or has_pnpm_workspace
                    if not is_workspace:
                        continue
                    workspace_manager = _declared_package_manager(workspace_package)
                    workspace_lockfiles = _lockfiles(root / workspace_root)
                    workspace_lock_managers = {lock_manager for lock_manager, _ in workspace_lockfiles}
                    effective_workspace_manager = workspace_manager or (
                        next(iter(workspace_lock_managers)) if len(workspace_lock_managers) == 1 else None
                    )
                    if len(workspace_lock_managers) > 1:
                        manager_problem = f"The workspace at {workspace_root.as_posix() or '.'} contains conflicting package-manager lockfiles."
                    elif workspace_manager and workspace_lock_managers and workspace_manager not in workspace_lock_managers:
                        manager_problem = f"The workspace packageManager declares {workspace_manager}, but its lockfile belongs to another manager."
                    elif manager and effective_workspace_manager and manager != effective_workspace_manager:
                        manager_problem = f"Package manager {manager} conflicts with the enclosing {effective_workspace_manager} workspace."
                    else:
                        manager = effective_workspace_manager or manager
                        lock_path = next((path for lock_manager, path in workspace_lockfiles if lock_manager == "npm"), None)
                        install_cwd = workspace_root.as_posix() or "."
                    break

            if manager is None:
                manager = "npm"
            detected_managers.add(manager)
            workspace_package = parsed_packages.get(Path(install_cwd), {})
            has_dependencies = any(
                isinstance(package.get(field), dict) and bool(package[field])
                for field in ("dependencies", "devDependencies", "optionalDependencies")
            )
            has_dependencies = has_dependencies or any(
                isinstance(workspace_package.get(field), dict) and bool(workspace_package[field])
                for field in ("dependencies", "devDependencies", "optionalDependencies")
            )
            lock_problem = manager_problem
            if not lock_problem and manager != "npm":
                lock_problem = f"Safe dependency preparation for {manager} lockfiles is not implemented; the check is blocked rather than running an npm install."
            if not lock_problem and has_dependencies and lock_path is None:
                lock_problem = "A supported npm lockfile is required before dependencies can be prepared safely."
            if not lock_problem and lock_path is not None:
                lock_problem = _npm_lockfile_problem(lock_path)
            if not lock_problem and has_dependencies and not _dependency_network_allowed():
                lock_problem = "Dependency preparation requires outbound network access, disabled by default. Set VERIFICATION_ALLOW_DEPENDENCY_NETWORK=true on the API host only for trusted demo repositories."
            if lock_problem:
                blocked.extend(
                    {"label": f"{package_dir.as_posix() or 'root'} · {label}", "reason": lock_problem}
                    for _, label in declared
                )
                continue

            for name, label in declared:
                script_label = f"{package_dir.as_posix() or 'root'} · {label}" if len(package_paths) > 1 else label
                checks.append({
                    "id": f"npm:{package_index}:{name}",
                    "label": script_label,
                    "manager": "npm",
                    "image": "node:22-alpine",
                    "cwd": package_dir.as_posix() or ".",
                    "install_cwd": install_cwd,
                    "install": has_dependencies,
                    "args": ["npm", "run", name],
                    "install_args": ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org"],
                })
    python_files = any(Path(name).suffix == ".py" for name in files)
    if python_files:
        detected_managers.add("python")
        syntax_scan = "import ast,pathlib; files=list(pathlib.Path('/workspace').rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print(f'Parsed {len(files)} Python files')"
        checks.append({"id": "python:compile", "label": "Python syntax", "manager": "python", "image": "python:3.12-alpine", "cwd": ".", "args": ["python", "-c", syntax_scan]})
        manifests = {Path(name).name.lower() for name in files}
        if "pyproject.toml" in manifests or "pytest.ini" in manifests or any("test" in Path(name).name.lower() for name in files):
            blocked.append({
                "label": "Python tests",
                "reason": "Python test dependencies are not prepared. Dependency installation is blocked until a safe locked-install workflow is available.",
            })
    manager = next(iter(detected_managers)) if len(detected_managers) == 1 else "mixed" if detected_managers else "unknown"
    return manager, checks, blocked


async def verification_options(workspace_id: str) -> dict[str, Any]:
    workspace = await get_workspace(workspace_id)
    root = active_project_directory(workspace_id, workspace)
    manager, checks, blocked_checks = _verification_profile(root)
    sandbox_available, sandbox_reason = _docker_status()
    return {
        "manager": manager,
        "checks": [{"id": item["id"], "label": item["label"], "dependency_install": item.get("install", False)} for item in checks],
        "blocked_checks": blocked_checks,
        "sandbox_available": sandbox_available,
        "sandbox_reason": sandbox_reason,
        "status": "ready" if checks else "blocked" if blocked_checks else "unsupported",
        "note": (
            "Some checks are blocked; review each reason below. npm downloads require validated lockfiles, disabled lifecycle scripts, "
            "and VERIFICATION_ALLOW_DEPENDENCY_NETWORK=true. That setting allows Docker bridge egress without hostname enforcement. "
            "Unsupported package-manager installers and Python test dependencies are not installed automatically."
            if blocked_checks and any("outbound network access" in item["reason"] for item in blocked_checks)
            else "npm dependencies are prepared only from validated npm lockfiles, with lifecycle scripts disabled, in a disposable copy. "
            "Dependency downloads require VERIFICATION_ALLOW_DEPENDENCY_NETWORK=true on the API host; this allows Docker bridge egress, "
            "which is not restricted to a hostname. The selected check then runs in a separate network-disabled container. "
            "Leave the setting off for untrusted repositories."
            if any(item.get("install") for item in checks)
            else "Some checks are blocked; see each reason below. Unsupported package-manager installers and Python test dependencies are not installed automatically."
            if blocked_checks
            else "Repository checks run in temporary Docker containers with networking disabled; no dependency download is performed."
        ),
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
    sandbox_available, sandbox_reason = _docker_status()
    if not sandbox_available:
        raise WorkspaceError(f"{sandbox_reason} No repository commands were executed.", 503)
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
