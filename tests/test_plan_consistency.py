"""Stage 3.5 P0 guard tests: the Upgrade Blueprint boundary must fail closed
when a published Draft plan contradicts its own resolution diagnostics.

P0 evidence (planning-run-484b5a710144.json): react-dom was exported
PROPOSED @19.0.4 with needsReview=false while the plan's own conflict note
proved PEER_RESOLUTION_DEFERRED to current 18.3.1. The Draft solver resolves
only the "default" variant; the stale chosen-level target must never be
published as an actionable proposal.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploom_mcp.blueprint import (  # noqa: E402
    _plan_consistency_error,
    _resolved_version_from_note,
)

DEFERRED_NOTE = (
    "PEER_RESOLUTION_DEFERRED: desired=19.0.4; resolved=current 18.3.1; "
    "react-dom@19.0.4 requires react@^19.0.4, but compatible assignment uses 18.3.1"
)
FALLBACK_NOTE = "PEER_RESOLUTION_FALLBACK: desired=3.0.0; resolved=2.0.0"


def _deferred_plan(target):
    return {
        "proposals": [{
            "project": "Demo",
            "rows": [{
                "package": "react-dom",
                "target": target,
                "status": "proposed" if target else "no-target",
                "reason": "policy desired" if target else DEFERRED_NOTE,
            }],
        }],
        "conflicts": [{
            "package": "react-dom",
            "project": "Demo",
            "note": DEFERRED_NOTE,
            "cohort": "peer-react-dom-8f3a…",
        }],
    }


class ResolvedVersionFromNoteTests(unittest.TestCase):
    def test_fallback_note_yields_the_resolved_version(self):
        self.assertEqual("2.0.0", _resolved_version_from_note(FALLBACK_NOTE))

    def test_deferred_note_yields_current_version_without_prefix(self):
        self.assertEqual("18.3.1", _resolved_version_from_note(DEFERRED_NOTE))

    def test_note_without_marker_yields_none(self):
        self.assertIsNone(_resolved_version_from_note("compatibility cohort only"))


class PlanConsistencyErrorTests(unittest.TestCase):
    def test_deferred_with_concrete_target_fails_closed(self):
        error = _plan_consistency_error(_deferred_plan("19.0.4"))
        self.assertIsNotNone(error)
        pkg, target, note = error
        self.assertEqual("react-dom", pkg)
        self.assertEqual("19.0.4", target)
        self.assertIn("PEER_RESOLUTION_DEFERRED", note)

    def test_deferred_without_target_stays_consistent(self):
        self.assertIsNone(_plan_consistency_error(_deferred_plan(None)))

    def test_fallback_target_matching_resolved_version_stays_consistent(self):
        plan = {
            "proposals": [{
                "project": "Demo",
                "rows": [{
                    "package": "plugin",
                    "target": "2.0.0",
                    "status": "proposed",
                    "reason": "policy desired",
                }],
            }],
            "conflicts": [{
                "package": "plugin",
                "project": "Demo",
                "note": FALLBACK_NOTE,
                "cohort": "peer-plugin-…",
            }],
        }
        self.assertIsNone(_plan_consistency_error(plan))

    def test_fallback_target_differing_from_resolved_version_fails_closed(self):
        plan = {
            "proposals": [{
                "project": "Demo",
                "rows": [{
                    "package": "plugin",
                    "target": "3.0.0",
                    "status": "proposed",
                    "reason": "policy desired",
                }],
            }],
            "conflicts": [{
                "package": "plugin",
                "project": "Demo",
                "note": FALLBACK_NOTE,
                "cohort": "peer-plugin-…",
            }],
        }
        error = _plan_consistency_error(plan)
        self.assertIsNotNone(error)
        self.assertEqual(("plugin", "3.0.0"), (error[0], error[1]))

    def test_entry_level_conflict_is_checked_without_conflicts_section(self):
        plan = {
            "proposals": [{
                "project": "Demo",
                "rows": [{
                    "package": "react-dom",
                    "target": "19.0.4",
                    "status": "proposed",
                    "conflict": DEFERRED_NOTE,
                }],
            }],
            "conflicts": [],
        }
        self.assertIsNotNone(_plan_consistency_error(plan))

    def test_unrelated_conflicts_do_not_poison_other_rows(self):
        plan = {
            "proposals": [{
                "project": "Demo",
                "rows": [
                    {"package": "react-dom", "target": "19.0.4", "status": "proposed"},
                    {"package": "tool", "target": "3.0.0", "status": "proposed"},
                ],
            }],
            "conflicts": [
                {"package": "react-dom", "project": "Demo", "note": DEFERRED_NOTE, "cohort": ""},
                {"package": "tool", "project": "Demo", "note": "PEER_COMPANION: …", "cohort": ""},
            ],
        }
        error = _plan_consistency_error(plan)
        self.assertIsNotNone(error)
        self.assertEqual("react-dom", error[0])

    def test_empty_plan_stays_consistent(self):
        self.assertIsNone(_plan_consistency_error({"proposals": [], "conflicts": []}))

    def test_guard_is_wired_into_finalize_after_the_plan_read(self):
        source = Path(ROOT / "src" / "deploom_mcp" / "blueprint.py").read_text(encoding="utf-8")
        lines = source.splitlines()
        read_line = next(i for i, line in enumerate(lines) if "_read_plan(prep.plan_path)" in line)
        guard_line = next(i for i, line in enumerate(lines) if "_plan_consistency_error(plan)" in line)
        self.assertGreater(guard_line, read_line)


if __name__ == "__main__":
    unittest.main()
