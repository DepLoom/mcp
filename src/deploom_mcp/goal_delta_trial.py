"""Host-bound exact private trial apply. No resolver, project verifier or MCP tool.

A candidate is data, never a grant. The existing operator-manifest startup trust
boundary issues an opaque, single-use local capability only for an explicitly
bound delta. Engineering grants remain visibly test-only. Historical S1/BR1 have
no grants and cannot be resumed by this code.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import weakref

from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError, canonical_digest, validate_manifest_change, npm_lock_format
from deploom_mcp.goal_execution_registry import load_host_registration, configured_state_root, OPERATION
from deploom_mcp.goal_lock_scope import _packages, _legacy, validate_npm_lock_scope, _scope_packages
from deploom_mcp.goal_report import _plain_under

APPLY_OPERATION = "apply-private-candidate"
_ISSUED = weakref.WeakKeyDictionary()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise GoalContractError("CANDIDATE_FILE_SIZE_OR_TYPE_INVALID")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GoalContractError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (UnicodeError, ValueError) as exc:
        raise GoalContractError("INVALID_CANDIDATE_JSON") from exc
    if not isinstance(value, dict):
        raise GoalContractError("INVALID_CANDIDATE_JSON")
    return value


def _core():
    sys.path.insert(0, str(resolve_core_dir()))
    import source_snapshot
    return source_snapshot


@dataclass(frozen=True)
class ExactDeltaProposal:
    source_key: str
    source_digest: str
    baseline_manifest_hash: str
    baseline_lock_hash: str
    manifest_bytes: bytes
    lock_bytes: bytes
    descriptor_json: bytes
    candidate_digest: str
    delta_digest: str
    source_files: tuple[tuple[str, bytes], ...] = ()

    @property
    def descriptor(self):
        return json.loads(self.descriptor_json)


def _proposal_artifacts(proposal):
    return (("package.json", proposal.manifest_bytes), ("package-lock.json", proposal.lock_bytes), *proposal.source_files)


def _candidate_digest(source_key, manifest_bytes, lock_bytes, source_files=()):
    value = {"sourceSnapshotKey": source_key, "manifestSha256": _sha(manifest_bytes), "lockSha256": _sha(lock_bytes)}
    if source_files:
        value["sourceEdits"] = [{"path": n, "afterSha256": _sha(raw)} for n, raw in source_files]
    return canonical_digest(value)


def _source_edits(goal, project, sources):
    from deploom_mcp.goal_contract import validate_goal
    goal = validate_goal(goal)
    if sources is None:
        sources = {}
    if not isinstance(sources, dict):
        raise GoalContractError("INVALID_SOURCE_REPAIR_FILES")
    allow = {e["path"]: e["beforeSha256"] for e in goal.get("sourceRepairAllowlist", [])}
    if set(sources) != set(allow):
        raise GoalContractError("SOURCE_REPAIR_SCOPE_MISMATCH")
    rows = []
    for name in sorted(allow):
        raw = sources[name]
        if not isinstance(raw, bytes) or len(raw) > 1024*1024:
            raise GoalContractError("SOURCE_REPAIR_FILE_SIZE_OR_TYPE_INVALID")
        before = _plain_under(project/name, project).read_bytes()
        if _sha(before) != allow[name]:
            raise GoalContractError("SOURCE_REPAIR_BASELINE_MISMATCH")
        if raw == before:
            raise GoalContractError("SOURCE_REPAIR_NO_CHANGE")
        rows.append({"path": name, "comparison": "EXACT_BYTES", "beforeSha256": _sha(before), "afterSha256": _sha(raw)})
    return rows


def propose_exact_delta(registration, snapshot, manifest_bytes, lock_bytes, source_files=None):
    """Validate a candidate against a real Core snapshot without modifying either."""
    core = _core()
    if not isinstance(snapshot, core.SourceSnapshot):
        raise GoalContractError("TRUSTED_SOURCE_SNAPSHOT_REQUIRED")
    core.validate_source_snapshot(snapshot, timeout_seconds=10)
    if not core.source_snapshot_live_source_continuity(snapshot, timeout_seconds=10):
        raise GoalContractError("STALE_DELTA_BASELINE")
    from deploom_mcp.goal_worker import prepare_goal_plan
    from deploom_mcp.goal_contract import assert_current_binding
    current_plan = prepare_goal_plan(Path(registration.project_path), registration.goal)
    if not current_plan.get("ok"):
        raise GoalContractError("CURRENT_GOAL_BINDING_UNAVAILABLE")
    assert_current_binding(registration.binding, registration.goal, current_plan["binding"]["identity"])
    if snapshot.original_project_path.resolve() != Path(registration.project_path).resolve():
        raise GoalContractError("SOURCE_PROJECT_BINDING_MISMATCH")
    if registration.goal["goalKind"] != "functional-maintenance":
        raise GoalContractError("BASELINE_OBSERVATION_HAS_NO_EXECUTION_AUTHORITY")
    return describe_exact_delta(registration,snapshot,manifest_bytes,lock_bytes,source_files)


def describe_exact_delta(registration,snapshot,manifest_bytes,lock_bytes,source_files=None):
    """Describe exact bytes against a sealed baseline; never grants a mutation.

    Recovery may describe the original baseline after live files became a
    permitted partial postimage. Its final transaction must separately prove
    the durable intent, final host grant and every current before/after byte.
    The private entry above still requires unchanged live source/current goal.
    """
    core=_core()
    if not isinstance(snapshot,core.SourceSnapshot):
        raise GoalContractError('TRUSTED_SOURCE_SNAPSHOT_REQUIRED')
    core.validate_source_snapshot(snapshot,timeout_seconds=10)
    before_manifest = _plain_under(snapshot.project_path/"package.json", snapshot.root).read_bytes()
    before_lock = _plain_under(snapshot.project_path/"package-lock.json", snapshot.root).read_bytes()
    source_digest = canonical_digest({"coreSourceSnapshotKey": snapshot.key})
    identity = registration.binding["identity"]
    if (identity["sourceDigest"] != source_digest or identity["manifestDigest"] != _sha(before_manifest)
            or identity["lockDigest"] != _sha(before_lock)):
        raise GoalContractError("STALE_DELTA_BASELINE")
    source_edits = _source_edits(registration.goal, snapshot.project_path, source_files)
    source_files = tuple(sorted((source_files or {}).items()))
    old_manifest, new_manifest = _json(before_manifest, 1024*1024), _json(manifest_bytes, 1024*1024)
    old_lock, new_lock = _json(before_lock, 8*1024*1024), _json(lock_bytes, 8*1024*1024)
    validate_manifest_change(old_manifest, new_manifest, registration.goal)
    declared = validate_npm_lock_scope(old_lock, new_lock, registration.goal,
        host_policy=registration.policy,check_profile=registration.profile)
    if declared["status"] != "DECLARED_LOCK_CLOSURE_ONLY":
        raise GoalContractError("DECLARED_DELTA_SCOPE_UNAVAILABLE")
    version=npm_lock_format(old_lock)
    admission=registration.profile.get('admission')
    if admission is not None and admission['scope']!='ISOLATED_NPM_V'+str(version)+'_CANDIDATE':
        raise GoalContractError('ADMISSION_LOCK_FORMAT_MISMATCH')
    instances = []
    old_packages,new_packages=_scope_packages(old_lock,new_lock)
    views=[("packages",old_packages,new_packages)]
    if version==2:views.append(("dependencies",_legacy(old_lock),_legacy(new_lock)))
    for view, old, new in views:
        for location in sorted(set(old) | set(new)):
            if location == "" or old.get(location) == new.get(location):
                continue
            instances.append({"view": view, "instance": location,
                              "before": old.get(location), "after": new.get(location)})
    if len(instances) > 1024:
        raise GoalContractError("DELTA_INSTANCE_LIMIT")
    files = [{"path": name, "comparison": "EXACT_BYTES", "beforeSha256": _sha(before), "afterSha256": _sha(after)}
             for name, before, after in (("package.json", before_manifest, manifest_bytes),
                                         ("package-lock.json", before_lock, lock_bytes)) if before != after]
    files.extend(source_edits)
    descriptor = {"schemaVersion": registration.goal["schemaVersion"], "goalDigest": registration.binding["goalDigest"],
                  "bindingDigest": registration.binding["bindingDigest"], "sourceSnapshotKey": snapshot.key,
                  "profileDigest": canonical_digest(registration.profile), "files": files,
                  "instances": instances, "sourceConfigEdits": source_edits,
                  "declaredLockClosure": declared["status"], "necessity": "NOT_CLAIMED"}
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    candidate_digest = _candidate_digest(snapshot.key, manifest_bytes, lock_bytes, source_files)
    return ExactDeltaProposal(snapshot.key, source_digest, _sha(before_manifest), _sha(before_lock),
        manifest_bytes, lock_bytes, encoded, candidate_digest, _sha(encoded), source_files)


class _DeltaGrant:
    __slots__ = ("__weakref__",)


def load_host_delta_grant(run_id, candidate_id, binding_digest, authorization, proposal, *, state_root=None):
    """Only the existing pinned operator registration can issue a capability.

    No grant-shaped JSON parameter exists. The operator must separately approve
    the full candidate; old verify-only registrations have no apply authority.
    """
    if authorization != APPLY_OPERATION:
        raise GoalContractError("EXPLICIT_DELTA_AUTHORIZATION_REQUIRED")
    if not isinstance(proposal, ExactDeltaProposal):
        raise GoalContractError("CANDIDATE_PROPOSAL_REQUIRED")
    configured = configured_state_root()
    _plain_under(configured,configured)
    root = configured.resolve()
    if state_root is not None and Path(state_root).resolve()!=root:
        raise GoalContractError("DELTA_STATE_ROOT_MISMATCH")
    registration = load_host_registration(run_id, candidate_id, binding_digest, OPERATION, state_root=root)
    descriptor = proposal.descriptor
    identity=registration.binding["identity"]
    recomputed_candidate=_candidate_digest(proposal.source_key,proposal.manifest_bytes,proposal.lock_bytes,proposal.source_files)
    if (recomputed_candidate!=proposal.candidate_digest or _sha(proposal.descriptor_json)!=proposal.delta_digest
            or proposal.source_digest!=canonical_digest({"coreSourceSnapshotKey":proposal.source_key})
            or proposal.source_digest!=identity["sourceDigest"]
            or proposal.baseline_manifest_hash!=identity["manifestDigest"]
            or proposal.baseline_lock_hash!=identity["lockDigest"]):
        raise GoalContractError("DELTA_PROPOSAL_IDENTITY_MISMATCH")
    if (descriptor.get("goalDigest") != registration.binding["goalDigest"]
            or descriptor.get("bindingDigest") != binding_digest):
        raise GoalContractError("DELTA_GOAL_BINDING_MISMATCH")
    # Recheck hard scope before issuing a grant, even for host-listed bytes.
    project = Path(registration.project_path)
    before_manifest = _plain_under(project/"package.json", project).read_bytes()
    before_lock = _plain_under(project/"package-lock.json", project).read_bytes()
    if (_sha(before_manifest) != proposal.baseline_manifest_hash or _sha(before_lock) != proposal.baseline_lock_hash):
        raise GoalContractError("STALE_DELTA_BASELINE")
    if _source_edits(registration.goal,project,dict(proposal.source_files)) != descriptor.get("sourceConfigEdits"):
        raise GoalContractError("SOURCE_REPAIR_DESCRIPTOR_MISMATCH")
    validate_manifest_change(_json(before_manifest,1024*1024), _json(proposal.manifest_bytes,1024*1024), registration.goal)
    validate_npm_lock_scope(_json(before_lock,8*1024*1024), _json(proposal.lock_bytes,8*1024*1024), registration.goal,
        host_policy=registration.policy,check_profile=registration.profile)
    approval = registration.delta_authorization
    if approval is None:
        raise GoalContractError("AUTHORIZATION_UNAVAILABLE")
    wanted = {"schemaVersion": 1, "operation": APPLY_OPERATION, "origin": approval["origin"],
              "candidateDigest": proposal.candidate_digest, "deltaDigest": proposal.delta_digest,
              "bindingDigest": binding_digest, "profileDigest": canonical_digest(registration.profile),
              "sourceSnapshotKey": proposal.source_key, "transitiveScope": approval["transitiveScope"],
              "stateRootDigest": canonical_digest({"hostStateRoot": str(root)})}
    if approval != wanted:
        raise GoalContractError("DELTA_AUTHORIZATION_BINDING_MISMATCH")
    mode = registration.policy.get("transitiveChangePolicy")
    if mode != approval["transitiveScope"]:
        raise GoalContractError("TRANSITIVE_AUTHORIZATION_POLICY_UNAVAILABLE")
    root_targets = {"node_modules/" + n for n in registration.goal["exactTargets"]}
    if mode == "ROOT_TARGETS_ONLY" and any(c["instance"] not in root_targets for c in proposal.descriptor["instances"]):
        raise GoalContractError("TRANSITIVE_DELTA_NOT_AUTHORIZED")
    grant = _DeltaGrant()
    _ISSUED[grant] = (registration, proposal.candidate_digest, proposal.delta_digest, root)
    return grant


def _remaining(registration):
    remaining=registration.deadline-time.time()
    if remaining<=0:raise GoalContractError("EXECUTION_DEADLINE_EXCEEDED")
    return max(1,min(30,int(remaining)))


def _inventory(core, root, policy, timeout):
    manifest = core.build_source_tree_manifest(root, policy=policy, timeout_seconds=timeout)
    return {str(entry["path"]): dict(entry) for entry in manifest.entries}


def _write_files(project, proposal):
    for name, raw in _proposal_artifacts(proposal):
        path = _plain_under(project/name, project)
        temporary = path.with_name(path.name+".deploom-trial.tmp")
        with temporary.open("xb") as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)


def _cleanup(core, container, work_root):
    if container.resolve().parent != work_root.resolve() or not container.name.startswith("delta-"):
        raise GoalContractError("TRIAL_CLEANUP_OWNERSHIP_MISMATCH")
    if container.is_symlink() or (hasattr(container, "is_junction") and container.is_junction()):
        raise GoalContractError("TRIAL_CLEANUP_OWNERSHIP_MISMATCH")
    core._force_rmtree(container)
    if container.exists():
        raise GoalContractError("TRIAL_CLEANUP_FAILED")


def _write_ledger(path, record):
    temporary = path.with_suffix(".next")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


def apply_authorized_private_trial(grant, proposal, snapshot):
    return _apply_authorized_private_trial(grant, proposal, snapshot)


def _apply_authorized_private_trial(grant, proposal, snapshot, *, _verification_action=None):
    """Apply only, then remove the trial; never claim npm/project verification.

    A durable attempt ID is created before materialization. A repeated call,
    restart or newly loaded grant for this run/candidate cannot spend another
    trial implicitly. No candidate bytes or raw local paths enter the receipt.
    """
    if not isinstance(grant, _DeltaGrant) or grant not in _ISSUED:
        raise GoalContractError("TRUSTED_HOST_DELTA_GRANT_REQUIRED")
    registration, candidate, delta, state_root = _ISSUED.pop(grant)
    if registration.delta_authorization["origin"]=="PREAPPROVED_HOST_POLICY" and _verification_action is None:
        raise GoalContractError("HOST_AUTHORIZED_VERIFICATION_ACTION_REQUIRED")
    if (not isinstance(proposal, ExactDeltaProposal) or proposal.candidate_digest != candidate
            or proposal.delta_digest != delta):
        raise GoalContractError("GRANT_CANDIDATE_MISMATCH")
    _remaining(registration)
    # Recompute, do not trust a dataclass/digest supplied by caller code.
    current = propose_exact_delta(registration, snapshot, proposal.manifest_bytes, proposal.lock_bytes, dict(proposal.source_files))
    if current != proposal:
        raise GoalContractError("STALE_OR_MUTATED_DELTA_PROPOSAL")
    _remaining(registration)
    core = _core()
    if (state_root.resolve().is_relative_to(snapshot.root.resolve())
            or state_root.resolve().is_relative_to(Path(registration.project_path).resolve())):
        raise GoalContractError("TRIAL_STATE_INSIDE_PROJECT")
    directory = _plain_under(state_root/"delta-trial-attempts", state_root)
    work_root = _plain_under(state_root/"delta-trial-workspaces", state_root)
    directory.mkdir(parents=True, exist_ok=True);work_root.mkdir(parents=True, exist_ok=True)
    key = canonical_digest({"runId": registration.run_id, "candidateId": registration.candidate_id})
    ledger = directory/(key+".json")
    record = {"schemaVersion": 1, "runId": registration.run_id, "candidateId": registration.candidate_id,
              "candidateDigest": candidate, "deltaDigest": delta, "goalDigest": registration.binding["goalDigest"],
              "sourceSnapshotKey": snapshot.key, "profileDigest": canonical_digest(registration.profile),
              "status": "STARTED", "authorizationOrigin": registration.delta_authorization["origin"],
              "verification": "NOT_VERIFIED", "fullAcceptance": False, "projectChecksExecuted": False}
    try:
        with ledger.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True);stream.flush();os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise GoalContractError("TRIAL_ALREADY_REGISTERED") from exc
    if registration.delta_authorization["origin"]=="PREAPPROVED_HOST_POLICY":
        record.update(privateExecutionAuthorized=True,privateExecutionAttempted=True,privateVerificationObserved=False,
            candidateProfileAccepted=False,goalChecksComplete=False,checkpointStatus="NOT_PUBLISHED",
            bindingDigest=registration.binding["bindingDigest"])
        _write_ledger(ledger,record)
    container = None
    primary_error = None
    try:
        remaining = _remaining(registration)
        container = Path(tempfile.mkdtemp(prefix="delta-", dir=work_root)).resolve()
        with core.bind_source_snapshot(snapshot):
            from deploom_mcp.goal_generated_outputs import source_materialization_arguments
            project, consumed, method = core.materialize_source_for_verification(
                Path(registration.project_path), container/"repo", timeout_seconds=remaining,
                **source_materialization_arguments(registration.policy,registration.profile,Path(registration.project_path)))
        if consumed.key != snapshot.key:
            raise GoalContractError("TRIAL_SOURCE_IDENTITY_MISMATCH")
        before = _inventory(core, container/"repo", snapshot.input_policy, _remaining(registration))
        _remaining(registration)
        _write_files(project, proposal)
        after = _inventory(core, container/"repo", snapshot.input_policy, _remaining(registration))
        changed = {n for n in set(before)|set(after) if before.get(n) != after.get(n)}
        prefix = snapshot.project_relative.as_posix().rstrip(".").strip("/")
        expected = {(prefix+"/" if prefix else "")+f["path"] for f in proposal.descriptor["files"]}
        if changed != expected:
            raise GoalContractError("TRIAL_UNAUTHORIZED_FILE_DELTA")
        for name, expected_bytes in _proposal_artifacts(proposal):
            if (project/name).read_bytes() != expected_bytes:
                raise GoalContractError("TRIAL_CANDIDATE_BYTES_MISMATCH")
        core.validate_source_snapshot(snapshot, timeout_seconds=_remaining(registration))
        _remaining(registration)
        record.update(status="APPLIED_AWAITING_CLEANUP", materialization=method,
                      appliedStateDigest=canonical_digest(after), exactFileDelta=sorted(changed))
        if _verification_action is not None:
            observation = _verification_action(project, container, registration, proposal)
            if not isinstance(observation, dict) or observation.get("fullAcceptance") is not False:
                raise GoalContractError("INVALID_CONTROLLED_VERIFIER_RESULT")
            final_inventory = _inventory(core, container/"repo", snapshot.input_policy, _remaining(registration))
            if final_inventory != after:
                raise GoalContractError("PRIVATE_CANDIDATE_CHANGED_DURING_VERIFICATION")
            if not core.source_snapshot_live_source_continuity(snapshot, timeout_seconds=_remaining(registration)):
                raise GoalContractError("STALE_DELTA_BASELINE")
            if propose_exact_delta(registration, snapshot, proposal.manifest_bytes, proposal.lock_bytes, dict(proposal.source_files)) != proposal:
                raise GoalContractError("POST_VERIFIER_BINDING_MISMATCH")
            record["verificationObservation"] = observation
    except BaseException as exc:
        primary_error = exc
        record.update(status="FAILED", errorType=type(exc).__name__)
        raise
    finally:
        try:
            if container is not None:
                _cleanup(core, container, work_root)
        except BaseException as exc:
            record.update(status="CLEANUP_FAILED", cleanupErrorType=type(exc).__name__)
            if primary_error is not None:
                primary_error.add_note("Private trial cleanup also failed: "+type(exc).__name__)
            else:
                raise
        finally:
            if record["status"] != "APPLIED_AWAITING_CLEANUP":
                _write_ledger(ledger, record)
    try:_remaining(registration)
    except GoalContractError:
        record.update(status="DEADLINE_EXCEEDED")
        _write_ledger(ledger,record)
        raise
    record.update(status=("VERIFIER_TRIAL_CLOSED_AWAITING_BASELINE_CLEANUP" if "verificationObservation" in record else "TRIAL_APPLIED"),
                  historicalPrivateTrial=True, currentWorkspaceVerified=False)
    _write_ledger(ledger, record)
    return record
