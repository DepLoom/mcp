"""Bounded fresh Core byte checks; no memoized integrity or execution authority."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from deploom_mcp.goal_contract import GoalContractError


def verify_core_files(core, expected_files):
    """Keep every original path/byte predicate and deterministic error ordering.

    The caller supplies its resolved Core root and freshly read release mapping.
    Only a bounded mapping uses concurrent read-only checks. All workers finish
    before a result or exception returns; module origins remain caller checks.
    """
    def verify(item):
        relative, expected = item
        path = core / relative
        if (path.is_symlink() or not path.resolve().is_relative_to(core)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise GoalContractError('CORE_INTEGRITY_MISMATCH')

    items = expected_files.items()
    if not 2 <= len(expected_files) <= 256:
        for item in items:
            verify(item)
        return
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='deploom-core-integrity') as pool:
        for _ in pool.map(verify, items):
            pass
