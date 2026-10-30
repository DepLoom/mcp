"""Independent PUBLIC Chalk 6 A/B scoring. P2 Chalk5 stays unchanged.

Never distribute this evaluator, oracle scripts, lock/reference solutions, or
the code-adaptation from the Yellow demonstration to either agent workspace.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

from correctness_oracle import (
    CHALK_BASELINE, source_files, verify_chalk_candidate,
)

ORIGINAL_P2_TARGET = {"chalk": "5.3.0", "is-number": "7.0.0"}
D1_AB_CHALK6_TARGET = {"chalk": "6.0.0", "is-number": "7.0.0"}
EXPECTED_SEED_SHA256 = "4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6"
FROZEN_FILENAMES = frozenset((
    ".npmrc", "README.md", "build.cjs", "test.cjs", "src/index.cjs",
    "src/format.cjs", "package.json", "package-lock.json",
))
ALLOWED_AGENT_CHANGES = frozenset((
    "package.json", "package-lock.json", "src/format.cjs",
))


def source_sha(root: Path) -> str:
    return hashlib.sha256(json.dumps(
        source_files(root), sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def verify_chalk6_frozen(baseline: Path, candidate: Path,
                         expected_baseline_sha256: str) -> dict:
    """Fail closed before npm when frozen bytes, chosen target or scope drift."""
    if not isinstance(expected_baseline_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_baseline_sha256
    ):
        return {"status": "FAIL", "reason": "MISSING_OR_BAD_BASELINE_SHA"}
    if expected_baseline_sha256 != EXPECTED_SEED_SHA256:
        return {"status": "FAIL", "reason": "UNAUTHORIZED_BASELINE_SHA"}
    try:
        files = source_files(baseline)
        if set(files) != FROZEN_FILENAMES:
            return {"status": "FAIL", "reason": "FROZEN_FILE_SET_DRIFT",
                    "baselineVerified": False}
        digest = source_sha(baseline)
    except (OSError, ValueError, UnicodeError, TypeError):
        return {"status": "FAIL", "reason": "INVALID_FROZEN_BASELINE",
                "baselineVerified": False}
    if digest != EXPECTED_SEED_SHA256:
        return {"status": "FAIL", "reason": "FROZEN_INPUT_DIGEST_MISMATCH",
                "baselineVerified": False}
    # Reuses precisely P2's independent source/lock integrity gate, real npm
    # ci/build/test and the multi-input anti-hardcode runtime. Only explicitly
    # selected target changes; original P2 scorer still defaults to Chalk5.
    result = verify_chalk_candidate(
        baseline, candidate, target_dependencies=D1_AB_CHALK6_TARGET)
    result.update({
        "baselineVerified": True, "baselineSourceSha256": digest,
        "objective": "explicit-version-targets",
        "exactTargets": dict(D1_AB_CHALK6_TARGET),
        "approvedVersionLines": {"chalk": [6]},
        "immutablePackages": {"is-number": "7.0.0"},
        "independentOracle": "PUBLIC_CHALK6_SEPARATE_D1_AB",
        "runtimeAttribution": "INDEPENDENT_EVALUATOR_NOT_AGENT_CLAIM",
        "policyAttainment": "NOT_EVALUATED_BY_RUNTIME_SCORER",
        "agentClaimedOutcomeIgnored": True,
    })
    return result
