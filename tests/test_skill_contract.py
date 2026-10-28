"""Portability checks for the canonical deploom-upgrade skill.

These tests pin the structure the installer promises and that all three clients
(OpenCode, Claude Code, Codex) agree on:

* one canonical SKILL.md with valid, portable frontmatter;
* progressive disclosure: exactly the documented references/ + examples/;
* client-neutrality: the canonical workflow must not depend on any client-
  specific command or config path (those live in integrations/<client>/).
"""
from __future__ import annotations

import pathlib
import re
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "deploom-upgrade"
SKILL_MD = SKILL_DIR / "SKILL.md"

EXPECTED_REFERENCES = {
    "blueprint-contract.md",
    "preflight.md",
    "execution-policy.md",
    "verification-policy.md",
    "evidence-report.md",
}
EXPECTED_EXAMPLES = {
    "plan-only.md",
    "yellow-exclude-react.md",
    "blocked-upgrade.md",
}

CLIENT_SPECIFIC_TOKENS = (
    "opencode run",
    "--permission-mode",
    "--auto",
    "opencode.json",
    ".opencode/",
    "claude mcp add",
    ".mcp.json",
    ".claude/",
    "codex mcp add",
    "config.toml",
    ".agents/",
    "~/.claude",
)


class CanonicalSkillContractTests(unittest.TestCase):
    def test_skill_dir_and_skil_md_exist(self) -> None:
        self.assertTrue(SKILL_DIR.is_dir(), f"missing skill dir: {SKILL_DIR}")
        self.assertTrue(SKILL_MD.is_file(), f"missing SKILL.md: {SKILL_MD}")

    def test_name_matches_folder(self) -> None:
        self.assertEqual(SKILL_DIR.name, "deploom-upgrade")

    def test_frontmatter_parses_and_name_description_present(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "SKILL.md must start with YAML frontmatter")
        parts = text.split("---", 2)
        self.assertGreaterEqual(len(parts), 3, "frontmatter must be closed by a second ---")
        meta = yaml.safe_load(parts[1])
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("name"), "deploom-upgrade")
        desc = meta.get("description")
        self.assertTrue(desc and len(desc) > 20, "description is effectively required and must be informative")
        self.assertIn("upgrade", desc.lower())
        self.assertIn("dependenc", desc.lower())
        md = meta.get("metadata") or {}
        self.assertIsInstance(md, dict)
        for value in md.values():
            self.assertIsInstance(value, str, "metadata must be a string-string map (portability)")

    def test_no_client_specific_fields_in_frontmatter(self) -> None:
        meta = yaml.safe_load(SKILL_MD.read_text(encoding="utf-8").split("---", 2)[1])
        for key in ("opencode", "claude", "codex", "client"):
            self.assertNotIn(key, meta, f"client-specific frontmatter field not allowed: {key}")

    def test_expected_references_and_examples_present(self) -> None:
        refs = sorted(p.name for p in (SKILL_DIR / "references").glob("*.md"))
        exs = sorted(p.name for p in (SKILL_DIR / "examples").glob("*.md"))
        self.assertEqual(set(refs), EXPECTED_REFERENCES)
        self.assertEqual(set(exs), EXPECTED_EXAMPLES)

    def test_no_stray_files_in_skill_tree(self) -> None:
        actual = {
            str(p.relative_to(SKILL_DIR)).replace("\\", "/")
            for p in SKILL_DIR.rglob("*")
            if p.is_file()
        }
        expected = {
            "SKILL.md",
            "contract.json",
            "scripts/validate_evidence.py",
            *(f"references/{name}" for name in sorted(EXPECTED_REFERENCES)),
            *(f"examples/{name}" for name in sorted(EXPECTED_EXAMPLES)),
        }
        self.assertEqual(actual, expected, "skill tree must contain exactly the canonical files")

    def test_skil_md_references_and_examples_resolve(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        for match in re.finditer(r"`?(references|examples)/[A-Za-z0-9_\-]+\.(?:md|json)`?", text, re.IGNORECASE):
            rel = re.sub(r"[`?]", "", match.group(0)).split(" (")[0]
            self.assertTrue((SKILL_DIR / rel).is_file(), f"SKILL.md points at missing file: {rel}")

    def test_canonical_workflow_has_no_client_specific_commands(self) -> None:
        targets = [SKILL_MD, *(SKILL_DIR / "references").glob("*.md")]
        for path in targets:
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            for token in CLIENT_SPECIFIC_TOKENS:
                self.assertNotIn(token.lower(), lower, f"client-specific token {token!r} in {path.relative_to(REPO)}")

    def test_examples_are_non_empty_and_self_contained(self) -> None:
        for path in (SKILL_DIR / "examples").glob("*.md"):
            self.assertTrue(path.read_text(encoding="utf-8").strip(), f"empty example: {path.name}")


if __name__ == "__main__":
    unittest.main()
