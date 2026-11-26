from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT / "src" / "cfd_bot"


def _extract_import_targets(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    return targets


class TestCleanArchitectureBoundaries(unittest.TestCase):
    def test_architecture_document_and_layers_exist(self) -> None:
        self.assertTrue((ROOT / "ARCHITECTURE.md").exists())
        expected = [
            PKG_ROOT / "domain" / "models.py",
            PKG_ROOT / "domain" / "ports.py",
            PKG_ROOT / "domain" / "risk_engine.py",
            PKG_ROOT / "domain" / "preventive_guard.py",
            PKG_ROOT / "domain" / "post_news_analyzer.py",
            PKG_ROOT / "domain" / "strategies" / "base.py",
            PKG_ROOT / "domain" / "strategies" / "session_breakout.py",
            PKG_ROOT / "application" / "config.py",
            PKG_ROOT / "application" / "bot_runner.py",
            PKG_ROOT / "application" / "operation_service.py",
            PKG_ROOT / "infrastructure" / "mt5_gateway.py",
            PKG_ROOT / "infrastructure" / "order_executor.py",
            PKG_ROOT / "infrastructure" / "calendar_service.py",
            PKG_ROOT / "infrastructure" / "web" / "api_server.py",
        ]
        for path in expected:
            self.assertTrue(path.exists(), f"Missing Clean Architecture module: {path}")

        # Ensure legacy flat modules were cleaned out
        legacy = ["api_server.py", "bot_runner.py", "calendar_service.py", "config.py", "executor.py", "models.py", "mt5_gateway.py"]
        for name in legacy:
            self.assertFalse((PKG_ROOT / name).exists(), f"Legacy flat module still present: {name}")

        # Verify Frontend Clean Architecture layers exist and legacy UI files are removed
        ui_src = ROOT / "ui" / "src"
        frontend_expected = [
            ui_src / "domain" / "types.ts",
            ui_src / "data" / "api.ts",
            ui_src / "presentation" / "components" / "layout" / "Sidebar.tsx",
            ui_src / "presentation" / "components" / "layout" / "Header.tsx",
            ui_src / "presentation" / "components" / "ui" / "PageHeader.tsx",
            ui_src / "presentation" / "components" / "ui" / "SectionCard.tsx",
            ui_src / "presentation" / "components" / "ui" / "StatCard.tsx",
            ui_src / "presentation" / "components" / "ui" / "StatusBadge.tsx",
            ui_src / "presentation" / "pages" / "OverviewPage.tsx",
            ui_src / "presentation" / "pages" / "SimulatorPage.tsx",
            ui_src / "presentation" / "pages" / "NewsIntelPage.tsx",
            ui_src / "presentation" / "pages" / "CalendarPage.tsx",
            ui_src / "presentation" / "pages" / "SettingsPage.tsx",
            ui_src / "presentation" / "pages" / "LogsPage.tsx",
        ]
        for path in frontend_expected:
            self.assertTrue(path.exists(), f"Missing Frontend Clean Architecture file: {path}")
        self.assertFalse((ui_src / "components").exists())
        self.assertFalse((ui_src / "api.ts").exists())
        self.assertFalse((ui_src / "types.ts").exists())

    def test_frontend_domain_and_data_respect_dependency_direction(self) -> None:
        ui_src = ROOT / "ui" / "src"
        domain_text = (ui_src / "domain" / "types.ts").read_text(encoding="utf-8")
        self.assertNotIn("../data", domain_text)
        self.assertNotIn("../presentation", domain_text)
        data_text = (ui_src / "data" / "api.ts").read_text(encoding="utf-8")
        self.assertNotIn("../presentation", data_text)

    def test_frontend_uses_local_anuphan_font_everywhere(self) -> None:
        ui_root = ROOT / "ui" / "src"
        main_ts = (ui_root / "main.tsx").read_text(encoding="utf-8")
        styles_css = (ui_root / "styles.css").read_text(encoding="utf-8")
        for weight in ("300.css", "400.css", "500.css", "600.css", "700.css"):
            self.assertIn(f"@fontsource/anuphan/{weight}", main_ts)
        self.assertIn("Anuphan", styles_css)
        self.assertNotIn("ui-monospace", styles_css)
        self.assertIn('[data-theme="dark"]', styles_css)

    def test_domain_layer_has_zero_outer_or_framework_dependencies(self) -> None:
        forbidden_prefixes = (
            "cfd_bot.application",
            "cfd_bot.infrastructure",
            "MetaTrader5",
            "starlette",
            "uvicorn",
            "pydantic",
            "urllib",
            "dotenv",
        )
        for py_file in (PKG_ROOT / "domain").rglob("*.py"):
            imports = _extract_import_targets(py_file)
            for imp in imports:
                for bad in forbidden_prefixes:
                    self.assertFalse(
                        imp == bad or imp.startswith(f"{bad}."),
                        f"Domain boundary violation in {py_file.relative_to(ROOT)}: imports '{imp}'",
                    )

    def test_application_layer_never_imports_infrastructure_or_sdks(self) -> None:
        forbidden_prefixes = (
            "cfd_bot.infrastructure",
            "MetaTrader5",
            "starlette",
            "uvicorn",
            "urllib",
        )
        for py_file in (PKG_ROOT / "application").rglob("*.py"):
            imports = _extract_import_targets(py_file)
            for imp in imports:
                for bad in forbidden_prefixes:
                    self.assertFalse(
                        imp == bad or imp.startswith(f"{bad}."),
                        f"Application boundary violation in {py_file.relative_to(ROOT)}: imports '{imp}'",
                    )


if __name__ == "__main__":
    unittest.main()
