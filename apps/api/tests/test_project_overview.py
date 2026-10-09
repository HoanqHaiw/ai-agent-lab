import tempfile
import unittest
from pathlib import Path

from app.project_overview import PROJECT_OVERVIEW_VERSION, analyze_project


class ProjectOverviewTests(unittest.TestCase):
    def test_project_health_reports_static_evidence_without_a_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [
                "README.md",
                "apps/api/tests/test_api.py",
                ".github/workflows/ci.yml",
                "apps/api/pyproject.toml",
                "apps/api/uv.lock",
            ]
            for relative in files:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("", encoding="utf-8")

            overview = analyze_project(root, files)

        signals = {signal["id"]: signal for signal in overview["health"]["signals"]}
        self.assertEqual(signals["tests"]["status"], "detected")
        self.assertEqual(signals["ci"]["evidence_files"], [".github/workflows/ci.yml"])
        self.assertEqual(signals["documentation"]["evidence_files"], ["README.md"])
        self.assertEqual(signals["dependency-locks"]["evidence_files"], ["apps/api/uv.lock"])
        self.assertEqual(signals["containers"]["status"], "not_detected")
        self.assertNotIn("score", overview["health"])
        self.assertIn("does not establish", overview["health"]["scope_note"])

    def test_project_overview_schema_version_increments_for_health_signals(self) -> None:
        self.assertEqual(PROJECT_OVERVIEW_VERSION, 3)


if __name__ == "__main__":
    unittest.main()
