import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipInfo

from fastapi import HTTPException

from app.auth import get_current_user_id
from app.code_intelligence import _validate_review
from app.implementation_engine import _safe_target
from app.main import ConfirmationRequest, apply_workspace_implementation, rollback_workspace_implementation, workspace_list
from app.task_analysis import _validate_citations
from app.workspaces import WorkspaceError, _archive_path, _normalize_github_url, ensure_workspace_owner, list_workspaces


class WorkspaceSafetyTests(unittest.TestCase):
    def test_workspace_owner_is_exact_and_guest_workspaces_are_not_user_owned(self) -> None:
        ensure_workspace_owner({"owner_id": "user_123"}, "user_123")
        ensure_workspace_owner({"owner_id": None}, None)
        with self.assertRaises(WorkspaceError) as mismatch:
            ensure_workspace_owner({"owner_id": "user_123"}, "user_456")
        self.assertEqual(mismatch.exception.status_code, 404)
        with self.assertRaises(WorkspaceError) as guest_access:
            ensure_workspace_owner({"owner_id": None}, "user_123")
        self.assertEqual(guest_access.exception.status_code, 404)

    def test_workspace_listing_is_owner_scoped_and_excludes_expired_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = datetime.now(timezone.utc)
            records = (
                ("11111111-1111-4111-8111-111111111111", "user_123", current),
                ("22222222-2222-4222-8222-222222222222", "user_456", current),
                ("33333333-3333-4333-8333-333333333333", "user_123", current - timedelta(days=8)),
            )
            for workspace_id, owner_id, created_at in records:
                directory = root / workspace_id
                directory.mkdir()
                (directory / "workspace.json").write_text(
                    json.dumps({
                        "id": workspace_id,
                        "owner_id": owner_id,
                        "name": "workspace",
                        "source": "ZIP upload",
                        "created_at": created_at.isoformat(),
                        "expires_at": (created_at + timedelta(days=7)).isoformat(),
                    }),
                    encoding="utf-8",
                )

            with patch("app.workspaces.WORKSPACE_ROOT", root), patch.dict(os.environ, {"MONGODB_URI": ""}):
                listed = asyncio.run(list_workspaces("user_123"))

        self.assertEqual([item["id"] for item in listed], [records[0][0]])
        self.assertNotIn("owner_id", listed[0])

    def test_guest_workspace_listing_does_not_disclose_other_guest_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "11111111-1111-4111-8111-111111111111"
            directory.mkdir()
            (directory / "workspace.json").write_text(
                json.dumps({
                    "id": directory.name,
                    "owner_id": None,
                    "name": "private guest workspace",
                    "source": "ZIP upload",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                }),
                encoding="utf-8",
            )
            with patch("app.workspaces.WORKSPACE_ROOT", root), patch.dict(os.environ, {"MONGODB_URI": ""}):
                listed = asyncio.run(list_workspaces(None))

        self.assertEqual(listed, [])

    def test_workspace_list_route_returns_the_current_users_records(self) -> None:
        with patch("app.main.list_workspaces", return_value=[]) as list_mock:
            self.assertEqual(asyncio.run(workspace_list("user_123")), [])
        list_mock.assert_awaited_once_with("user_123")

    def test_production_auth_rejects_missing_bearer_token(self) -> None:
        with patch.dict(os.environ, {"AUTH_REQUIRED": "true", "ENVIRONMENT": "production"}):
            with self.assertRaises(HTTPException) as rejected:
                asyncio.run(get_current_user_id(None))
        self.assertEqual(rejected.exception.status_code, 401)

    def test_apply_and_rollback_routes_reject_missing_explicit_confirmation(self) -> None:
        for route in (apply_workspace_implementation, rollback_workspace_implementation):
            with self.subTest(route=route.__name__), self.assertRaises(HTTPException) as rejected:
                asyncio.run(route("workspace-id", "draft-id", ConfirmationRequest(), None))
            self.assertEqual(rejected.exception.status_code, 409)

    def test_token_must_have_an_allowed_authorized_party(self) -> None:
        class SigningKey:
            key = object()

        class JwkClient:
            def get_signing_key_from_jwt(self, token: str) -> SigningKey:
                return SigningKey()

        settings = {
            "AUTH_REQUIRED": "true",
            "CLERK_ISSUER": "https://issuer.example",
            "CLERK_JWKS_URL": "https://issuer.example/.well-known/jwks.json",
            "WEB_ORIGIN": "https://web.example",
            "AUTHORIZED_PARTIES": "",
        }
        with patch.dict(os.environ, settings):
            with patch("app.auth._jwk_client", None):
                with patch("app.auth.PyJWKClient", return_value=JwkClient()):
                    with patch("app.auth.jwt.decode", return_value={"sub": "user_123", "azp": "https://web.example/"}):
                        self.assertEqual(asyncio.run(get_current_user_id("Bearer signed-token")), "user_123")
                    with patch("app.auth.jwt.decode", return_value={"sub": "user_123"}):
                        with self.assertRaises(HTTPException) as missing_party:
                            asyncio.run(get_current_user_id("Bearer signed-token"))
                        self.assertEqual(missing_party.exception.status_code, 401)
                    with patch("app.auth.jwt.decode", return_value={"sub": "user_123", "azp": "https://other.example"}):
                        with self.assertRaises(HTTPException) as wrong_party:
                            asyncio.run(get_current_user_id("Bearer signed-token"))
                        self.assertEqual(wrong_party.exception.status_code, 401)

    def test_github_import_accepts_only_public_github_repository_urls(self) -> None:
        self.assertEqual(
            _normalize_github_url("https://github.com/example/project.git"),
            ("https://github.com/example/project.git", "project"),
        )
        for value in (
            "https://github.com@127.0.0.1/example/project",
            "https://github.com:8443/example/project",
            "https://github.com.example.invalid/example/project",
            "https://github.com/example/project?redirect=http://127.0.0.1",
        ):
            with self.subTest(value=value), self.assertRaises(WorkspaceError):
                _normalize_github_url(value)

    def test_zip_import_rejects_traversal_absolute_paths_and_symlinks(self) -> None:
        for filename in ("../outside.txt", "/absolute.txt", "C:/outside.txt"):
            with self.subTest(filename=filename), self.assertRaises(WorkspaceError):
                _archive_path(ZipInfo(filename))

        link = ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(WorkspaceError):
            _archive_path(link)

    def test_citations_must_match_a_sampled_file_and_its_visible_lines(self) -> None:
        result = _validate_citations(
            {
                "relevant_files": [
                    {"path": "src/main.py", "reason": "evidence", "start_line": 4, "end_line": 500},
                    {"path": "src/other.py", "reason": "not supplied", "start_line": 1, "end_line": 2},
                ],
            },
            {"src/main.py", "src/other.py"},
            [{"path": "src/main.py", "line_count": 5, "content": "1: one\n5: five"}],
        )
        self.assertEqual(
            result["relevant_files"],
            [{"path": "src/main.py", "reason": "evidence", "start_line": 4, "end_line": 5}],
        )

    def test_code_review_findings_are_clamped_to_lines_supplied_to_the_model(self) -> None:
        result = _validate_review(
            {
                "review_summary": "Static review",
                "dependency_notes": [],
                "findings": [
                    {
                        "category": "bug",
                        "severity": "high",
                        "title": "Potential issue",
                        "description": "Evidence",
                        "file": "src/main.py",
                        "start_line": 3,
                        "end_line": 900,
                        "recommendation": "Verify",
                    },
                    {
                        "category": "security",
                        "severity": "critical",
                        "title": "Unsourced",
                        "description": "Not in supplied files",
                        "file": "src/hidden.py",
                        "start_line": 1,
                        "end_line": 2,
                        "recommendation": "Ignore",
                    },
                ],
            },
            [{"path": "src/main.py", "line_count": 4, "content": "1: a\n4: d"}],
        )
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["start_line"], 3)
        self.assertEqual(result["findings"][0]["end_line"], 4)

    def test_implementation_targets_reject_traversal_and_secret_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(WorkspaceError):
                _safe_target(root, "../outside.py", creating=True)
            with self.assertRaises(WorkspaceError):
                _safe_target(root, ".env.local", creating=True)


if __name__ == "__main__":
    unittest.main()
