import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.verification import _docker_status, _lockfiles, _npm_lockfile_problem, _verification_profile


def _write(root: Path, relative_path: str, content: str) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _package(scripts: dict[str, str], **fields: object) -> str:
    return json.dumps({"name": "fixture", "scripts": scripts, **fields})


def _npm_lock(resolved: str = "https://registry.npmjs.org/example/-/example-1.0.0.tgz") -> str:
    return json.dumps({
        "name": "fixture",
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/example": {
                "version": "1.0.0",
                "resolved": resolved,
                "integrity": "sha512-example",
            },
        },
    })


class VerificationProfileTests(unittest.TestCase):
    def profile(self, root: Path, allow_network: bool) -> tuple[str, list[dict], list[dict]]:
        with patch.dict(os.environ, {"VERIFICATION_ALLOW_DEPENDENCY_NETWORK": str(allow_network).lower()}):
            return _verification_profile(root)

    def test_npm_dependency_install_is_blocked_without_explicit_network_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({"test": "vitest run"}, devDependencies={"vitest": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())

            manager, checks, blocked = self.profile(root, allow_network=False)

        self.assertEqual(manager, "npm")
        self.assertEqual(checks, [])
        self.assertIn("disabled by default", blocked[0]["reason"])

    def test_npm_dependencies_use_the_declared_manifest_script_after_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({"test": "vitest run"}, devDependencies={"vitest": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())

            manager, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(manager, "npm")
        self.assertEqual(blocked, [])
        self.assertEqual(checks[0]["install_cwd"], ".")
        self.assertEqual(checks[0]["args"], ["npm", "run", "test"])
        self.assertTrue(checks[0]["install"])

    def test_npm_workspaces_install_from_the_nearest_declared_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({}, workspaces=["packages/*"], devDependencies={"eslint": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())
            _write(root, "packages/api/package.json", _package({"test": "node --test"}))

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(blocked, [])
        self.assertEqual(checks[0]["cwd"], "packages/api")
        self.assertEqual(checks[0]["install_cwd"], ".")

    def test_single_star_workspace_pattern_does_not_match_nested_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({}, workspaces=["./packages/*"], devDependencies={"eslint": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())
            _write(
                root,
                "packages/api/tools/package.json",
                _package({"test": "vitest run"}, devDependencies={"vitest": "1.0.0"}),
            )

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(checks, [])
        self.assertIn("supported npm lockfile is required", blocked[0]["reason"])

    def test_oversized_package_manifest_is_blocked_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(
                root,
                "package.json",
                '{"scripts":{"test":"node --test"},"padding":"' + ("x" * (2 * 1024 * 1024)) + '"}',
            )

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(checks, [])
        self.assertIn("exceeds the 2 MB", blocked[0]["reason"])

    def test_double_star_workspace_pattern_matches_nested_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({}, workspaces=["packages/**"], devDependencies={"eslint": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())
            _write(root, "packages/api/tools/package.json", _package({"test": "node --test"}))

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(blocked, [])
        self.assertEqual(checks[0]["install_cwd"], ".")

    def test_package_outside_workspace_does_not_inherit_root_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({}, workspaces=["packages/*"], devDependencies={"eslint": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())
            _write(root, "apps/web/package.json", _package({"test": "vitest run"}, devDependencies={"vitest": "1.0.0"}))

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(checks, [])
        self.assertIn("supported npm lockfile is required", blocked[0]["reason"])

    def test_pnpm_workspace_is_identified_and_blocked_instead_of_run_as_npm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({}, packageManager="pnpm@9.0.0"))
            _write(root, "pnpm-workspace.yaml", "packages:\n  - packages/*\n")
            _write(root, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
            _write(root, "packages/web/package.json", _package({"test": "vitest run"}, devDependencies={"vitest": "1.0.0"}))

            manager, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(manager, "pnpm")
        self.assertEqual(checks, [])
        self.assertIn("preparation for pnpm", blocked[0]["reason"])

    def test_conflicting_manager_declaration_and_lockfile_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({"test": "vitest run"}, packageManager="pnpm@9.0.0", devDependencies={"vitest": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(checks, [])
        self.assertIn("declares pnpm", blocked[0]["reason"])

    def test_nested_package_manager_conflicting_with_npm_workspace_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({}, workspaces=["packages/*"], packageManager="npm@10.0.0", devDependencies={"eslint": "1.0.0"}))
            _write(root, "package-lock.json", _npm_lock())
            _write(root, "packages/api/package.json", _package({"test": "node --test"}, packageManager="pnpm@9.0.0"))

            _, checks, blocked = self.profile(root, allow_network=True)

        self.assertEqual(checks, [])
        self.assertIn("conflicts with the enclosing npm workspace", blocked[0]["reason"])

    def test_mixed_node_and_python_projects_report_both_profiles_and_block_unprepared_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({"test": "node --test"}))
            _write(root, "package-lock.json", _npm_lock())
            _write(root, "api/main.py", "print('safe syntax-only fixture')\n")
            _write(root, "api/tests/test_api.py", "def test_sample():\n    assert True\n")

            manager, checks, blocked = self.profile(root, allow_network=False)

        self.assertEqual(manager, "mixed")
        self.assertEqual({check["id"] for check in checks}, {"npm:1:test", "python:compile"})
        self.assertEqual(blocked[0]["label"], "Python tests")

    def test_npm_lockfile_rejects_non_registry_and_malformed_dependency_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "package-lock.json"
            lock_path.write_text(_npm_lock("https://127.0.0.1/private.tgz"), encoding="utf-8")
            self.assertIn("outside the approved", _npm_lockfile_problem(lock_path) or "")
            lock_path.write_text(_npm_lock("https://[invalid/private.tgz"), encoding="utf-8")
            self.assertIn("outside the approved", _npm_lockfile_problem(lock_path) or "")

    def test_lockfile_symlinks_are_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "package-lock.json"
            lock_path.write_text(_npm_lock(), encoding="utf-8")
            with patch.object(Path, "is_symlink", autospec=True, return_value=True):
                self.assertEqual(_lockfiles(Path(temporary)), [])
                self.assertIn("Symbolic links", _npm_lockfile_problem(lock_path) or "")

    def test_docker_cli_without_a_daemon_reports_sandbox_unavailable(self) -> None:
        process_result = type("Completed", (), {"returncode": 1})()
        with patch("app.verification._docker_executable", return_value="C:/Docker/docker.exe"):
            with patch("app.verification.subprocess.run", return_value=process_result):
                available, reason = _docker_status()
        self.assertFalse(available)
        self.assertIn("Docker daemon is unavailable", reason or "")

    def test_dependency_free_script_can_run_without_a_lockfile_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root, "package.json", _package({"test": "node --test"}))

            manager, checks, blocked = self.profile(root, allow_network=False)

        self.assertEqual(manager, "npm")
        self.assertEqual(blocked, [])
        self.assertFalse(checks[0]["install"])


if __name__ == "__main__":
    unittest.main()
