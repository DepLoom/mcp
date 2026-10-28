#!/usr/bin/env python3
"""Check (or deliberately regenerate) the canonical Skill content manifest."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Works both as a checkout script and when invoked from bundled resources.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_integrity import verify_contract, write_contract  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1] / "skills" / "deploom-upgrade")
    parser.add_argument("--write", action="store_true", help="regenerate after an intentional version/content change")
    args = parser.parse_args(argv)
    if args.write:
        try:
            contract = write_contract(args.skill_dir)
        except (OSError, ValueError) as exc:
            print(f"skill: {exc}", file=sys.stderr)
            return 1
        print(f"skill: wrote {contract['contractVersion']} {contract['bundleSha256']}")
    problems = verify_contract(args.skill_dir)
    for problem in problems:
        print(f"skill: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("skill: canonical content matches contract.json (client-loaded content is not attested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
