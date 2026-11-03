"""Closing full-content revalidation for a budgeted physical observation.

Detects drift across earlier gates; still not an atomic snapshot against
arbitrary adversarial writers. No watcher markers, cache or write lease.
"""
import hashlib
import shutil
from pathlib import Path

from deploom_mcp.goal_contract import GoalContractError


def revalidate_current_boundary(context,record,project,checkpoint,core,budget,trace):
    from deploom_mcp.goal_final_checkpoint import current_state_matches,observe_swap,observe_delivered_tree
    budget.remaining()
    trace.mark('source','BEGIN')
    if core.source_snapshot_fingerprint(project,remaining_budget=budget.remaining)!=checkpoint['sourceSnapshotKey']:
        raise GoalContractError('FINAL_SOURCE_NOT_CURRENT')
    budget.remaining();trace.mark('source',True)
    trace.mark('swaps','BEGIN')
    for move in record['windowsMoves'].values():
        if observe_swap(move,deadline=budget.wall_deadline(),completed_read=True)!='DELIVERED':
            raise GoalContractError('FINAL_SOURCE_NOT_CURRENT')
    budget.remaining();trace.mark('swaps',True)
    trace.mark('tree','BEGIN')
    observe_delivered_tree(record['dependencyTreeMove'],deadline=budget.wall_deadline(),_budget=budget)
    budget.remaining();trace.mark('tree',True)
    trace.mark('toolchain','BEGIN')
    node=shutil.which('node');npm=Path(node).parent/'node_modules/npm/bin/npm-cli.js' if node else None
    tools=record['liveObservation']['materializedSourceOrigin']['toolchain']
    if not node or not npm.is_file() or tools!={
            'nodeSha256':hashlib.sha256(Path(node).read_bytes()).hexdigest(),
            'npmCliSha256':hashlib.sha256(npm.read_bytes()).hexdigest()}:
        raise GoalContractError('FINAL_TOOLCHAIN_NOT_CURRENT')
    budget.remaining();trace.mark('toolchain',True)
    if not current_state_matches(context,record,project,_trace=trace,_budget=budget):return False
    budget.remaining()
    if core.source_snapshot_fingerprint(project,remaining_budget=budget.remaining)!=checkpoint['sourceSnapshotKey']:
        raise GoalContractError('FINAL_SOURCE_NOT_CURRENT')
    budget.remaining()
    return True
