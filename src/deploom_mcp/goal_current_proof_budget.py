"""Host resource policy and a single physical-read deadline; grants no authority.

Missing currentProof is deliberately legacy, not an implicit policy upgrade.
A budget is process-local; only an original UTC ceiling may cross processes.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from threading import Event
from typing import Callable

from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_final_resource_policy import parse_final_resource_policy


@dataclass(frozen=True)
class CurrentProofSpec:
    max_seconds: int = 20
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in (1, 2)
                or type(self.max_seconds) is not int
                or not 1 <= self.max_seconds <= (120 if self.schema_version == 1 else 300)):
            raise GoalContractError('INVALID_CURRENT_PROOF_SPEC')

    def as_policy(self):
        return {'schemaVersion': self.schema_version, 'scope': 'CURRENT_PHYSICAL_READ',
                'maxSeconds': self.max_seconds}


def parse_current_proof_spec(policy):
    """Validate operator policy; None means the unchanged legacy reader."""
    if not isinstance(policy, dict):
        raise GoalContractError('INVALID_EXECUTION_POLICY')
    resources = parse_final_resource_policy(policy)
    if 'currentProof' not in policy:
        return None
    value = policy['currentProof']
    if (not isinstance(value, dict)
            or set(value) != {'schemaVersion', 'scope', 'maxSeconds'}
            or type(value.get('schemaVersion')) is not int
            or value['schemaVersion'] not in (1, 2)
            or (value['schemaVersion'] == 2 and resources is None)
            or value.get('scope') != 'CURRENT_PHYSICAL_READ'):
        raise GoalContractError('INVALID_CURRENT_PROOF_SPEC')
    return CurrentProofSpec(value['maxSeconds'], value['schemaVersion'])


def registered_current_proof_spec(registration):
    """Resource policy is pinned through the existing host policy digest chain."""
    policy = getattr(registration, 'policy', {})
    spec = parse_current_proof_spec(policy)
    if spec is None:
        return None
    if (getattr(registration, 'delta_authorization', None) or {}).get('origin') != 'PREAPPROVED_HOST_POLICY':
        raise GoalContractError('CURRENT_PROOF_HOST_POLICY_REQUIRED')
    digest = canonical_digest(policy)
    if (registration.goal.get('policyDigest') != digest
            or registration.binding.get('identity', {}).get('policyDigest') != digest):
        raise GoalContractError('CURRENT_PROOF_POLICY_BINDING_MISMATCH')
    return spec


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


class CurrentProofBudget:
    """One non-renewable monotonic budget, additionally clamped by UTC.

    Cooperative checks alone cannot interrupt blocked filesystem I/O. The caller
    must also supervise the read-only process with a killable outer watchdog.
    """
    def __init__(self, spec: CurrentProofSpec, *, original_deadline_utc=None,
                 cancelled: Event | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time):
        if not isinstance(spec, CurrentProofSpec):
            raise GoalContractError('INVALID_CURRENT_PROOF_SPEC')
        if original_deadline_utc is not None and not _finite_number(original_deadline_utc):
            raise GoalContractError('INVALID_CURRENT_PROOF_DEADLINE')
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._cancelled = cancelled if cancelled is not None else Event()
        duration = spec.max_seconds
        if original_deadline_utc is not None:
            duration = min(duration, original_deadline_utc - wall_clock())
        # Clamp at construction as well as on every check: rolling wall time
        # back later must not revive time from the original UTC deadline.
        self._initial_duration = duration
        self._cutoff = monotonic() + duration
        self._utc_ceiling = original_deadline_utc
        self.remaining()

    def remaining(self):
        if self._cancelled.is_set():
            raise GoalContractError('CURRENT_PROOF_CANCELLED')
        # Float addition/subtraction may otherwise exceed the original allowance
        # by one ULP (for example 300.1 - 0.1). Never enlarge a host limit.
        remaining = min(self._initial_duration, self._cutoff - self._monotonic())
        if self._utc_ceiling is not None:
            remaining = min(remaining, self._utc_ceiling - self._wall_clock())
        if not _finite_number(remaining) or remaining <= 0:
            raise GoalContractError('CURRENT_PROOF_DEADLINE_EXCEEDED')
        return remaining

    def wall_deadline(self):
        """Adapter for existing owners; never promotes a fractional remainder."""
        remaining = self.remaining()
        deadline = self._wall_clock() + remaining
        return min(deadline, self._utc_ceiling) if self._utc_ceiling is not None else deadline

    def cancel(self):
        self._cancelled.set()


def reserved_worker_timeout(spec, *, now, original_deadlines, return_headroom_seconds=60, worker_ceiling=600):
    """Reserve independent parent proof before any mutating worker is started.

    All supplied deadlines must be the original host/transaction/operation UTC
    ceilings, not newly issued grants. Does not create or persist a new deadline.
    """
    if (not isinstance(spec, CurrentProofSpec) or not _finite_number(now)
            or not _finite_number(return_headroom_seconds) or return_headroom_seconds < 0
            or type(worker_ceiling) is not int or not 5 <= worker_ceiling <= 1200
            or not isinstance(original_deadlines, (tuple, list)) or not original_deadlines
            or any(not _finite_number(value) for value in original_deadlines)):
        raise GoalContractError('INVALID_CURRENT_PROOF_RESERVATION')
    remaining = min(original_deadlines) - now - spec.max_seconds - return_headroom_seconds
    if remaining <= 0:
        raise GoalContractError('FINAL_CURRENT_PROOF_BUDGET_INSUFFICIENT')
    return min(worker_ceiling, remaining)
