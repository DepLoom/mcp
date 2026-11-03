"""Versioned exact-goal contract and pure scope guards; grants no verification authority."""
from __future__ import annotations
import copy
import hashlib
import json
from typing import Any, Mapping
from jsonschema import Draft202012Validator

_SHA = r"^[0-9a-f]{64}$"
_NAME = r"^(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+$"
_VERSION = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
_STRING_LIST = {"type": "array", "maxItems": 256, "uniqueItems": True, "items": {"type": "string", "pattern": _NAME}}
GOAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schemaVersion", "goalKind", "healthPolicy", "goalId", "exactTargets", "mutablePackages", "immutablePackages", "excludedPackages", "requiredChecks", "acceptPartial", "allowSourceRepair", "policyDigest"],
    "properties": {
        "schemaVersion": {"const": 1},
        "goalKind": {"enum": ["functional-maintenance", "baseline-observation"]},
        "healthPolicy": {"const": "REPORT_ONLY"},
        "goalId": {"type": "string", "minLength": 1, "maxLength": 128},
        "exactTargets": {"type": "object", "minProperties": 1, "maxProperties": 256, "propertyNames": {"pattern": _NAME}, "additionalProperties": {"type": "string", "pattern": _VERSION}},
        "mutablePackages": _STRING_LIST, "immutablePackages": _STRING_LIST,
        "excludedPackages": _STRING_LIST,
        "requiredChecks": {"type": "array", "minItems": 1, "maxItems": 128, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 128}},
        "acceptPartial": {"type": "boolean"}, "allowSourceRepair": {"type": "boolean"},
        "policyDigest": {"type": "string", "pattern": _SHA},
        "verificationContext": {"type":"object","additionalProperties":False,
            "required":["profileDigest","checkBindingsDigest","baselineLineageDigest"],
            "properties":{key:{"type":"string","pattern":_SHA} for key in
                ["profileDigest","checkBindingsDigest","baselineLineageDigest"]}},
    },
}
# v2 adds exact source repair scope only. It grants no execution, profile or
# durable-checkpoint authority; those owners must still admit every operation.
GOAL_SCHEMA_V2 = copy.deepcopy(GOAL_SCHEMA)
GOAL_SCHEMA_V2["properties"]["schemaVersion"] = {"const": 2}
GOAL_SCHEMA_V2["required"].append("sourceRepairAllowlist")
GOAL_SCHEMA_V2["properties"]["sourceRepairAllowlist"] = {
    "type": "array", "minItems": 1, "maxItems": 32,
    "items": {"type": "object", "additionalProperties": False,
              "required": ["path", "beforeSha256"], "properties": {
                  "path": {"type": "string", "minLength": 1, "maxLength": 256},
                  "beforeSha256": {"type": "string", "pattern": _SHA}}}}
IDENTITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["sourceDigest", "manifestDigest", "lockDigest", "toolchainDigest", "policyDigest", "implementationDigest"],
    "properties": {key: {"type": "string", "pattern": _SHA} for key in ["sourceDigest", "manifestDigest", "lockDigest", "toolchainDigest", "policyDigest", "implementationDigest"]},
}

class GoalContractError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code + (": " + detail if detail else ""))

def canonical_digest(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()

def validate_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    if goal.get("schemaVersion") == 3:
        from deploom_mcp.goal_choice_contract import validate_choice_goal
        return validate_choice_goal(goal)
    schema = GOAL_SCHEMA_V2 if goal.get("schemaVersion") == 2 else GOAL_SCHEMA
    errors = sorted(Draft202012Validator(schema).iter_errors(goal), key=lambda e: str(list(e.path)))
    if errors:
        raise GoalContractError("INVALID_GOAL", errors[0].message)
    value = copy.deepcopy(dict(goal))
    targets, mutable = set(value["exactTargets"]), set(value["mutablePackages"])
    if value["goalKind"] == "baseline-observation":
        if mutable or not targets.issubset(set(value["immutablePackages"])):
            raise GoalContractError("BASELINE_OBSERVATION_MUST_BE_IMMUTABLE")
    else:
        if targets != mutable:
            raise GoalContractError("EXACT_SCOPE_MISMATCH")
        if mutable & (set(value["immutablePackages"]) | set(value["excludedPackages"])):
            raise GoalContractError("TARGET_NOT_AUTHORIZED")
    if value["schemaVersion"] == 1:
        if value["allowSourceRepair"] or value["acceptPartial"]:
            raise GoalContractError("UNSUPPORTED_GOAL_MODE", "v1 exact-maintenance guard does not authorize repair or partial acceptance")
    else:
        if value["acceptPartial"] or not value["allowSourceRepair"] or value["goalKind"] != "functional-maintenance":
            raise GoalContractError("UNSUPPORTED_GOAL_MODE", "v2 requires explicit full-goal source repair")
        paths = [entry["path"] for entry in value["sourceRepairAllowlist"]]
        for path in paths:
            parts = path.split("/")
            reserved = {"con", "prn", "aux", "nul", *["com"+str(i) for i in range(1,10)], *["lpt"+str(i) for i in range(1,10)]}
            if (any(not part or part.casefold() in {".", "..", ".git", "node_modules"}
                    or part.endswith(".") or part.split(".")[0].casefold() in reserved for part in parts)
                    or any(not all(c.isascii() and (c.isalnum() or c in "_.-") for c in part) for part in parts)
                    or path.casefold() in {"package.json", "package-lock.json"}):
                raise GoalContractError("INVALID_SOURCE_REPAIR_PATH")
        if len({p.casefold() for p in paths}) != len(paths):
            raise GoalContractError("DUPLICATE_SOURCE_REPAIR_PATH")
        value["sourceRepairAllowlist"] = sorted(value["sourceRepairAllowlist"], key=lambda e: e["path"])
    for key in ("mutablePackages", "immutablePackages", "excludedPackages", "requiredChecks"):
        value[key] = sorted(value[key])
    return value

def bind_goal(goal: Mapping[str, Any], identity: Mapping[str, str]) -> dict[str, Any]:
    value = validate_goal(goal)
    if list(Draft202012Validator(IDENTITY_SCHEMA).iter_errors(identity)):
        raise GoalContractError("INVALID_IDENTITY")
    if value["policyDigest"] != identity["policyDigest"]:
        raise GoalContractError("POLICY_BINDING_MISMATCH")
    binding = {"schemaVersion": value["schemaVersion"], "goalDigest": canonical_digest(value), "identity": dict(identity)}
    return {**binding, "bindingDigest": canonical_digest(binding), "goal": value,
            "authority": "PLANNING_ONLY", "verification": "NOT_VERIFIED",
            "remainingChecks": value["requiredChecks"]}

def assert_current_binding(binding: Mapping[str, Any], goal: Mapping[str, Any], identity: Mapping[str, str]) -> None:
    current = bind_goal(goal, identity)
    # Recompute all material fields. A caller's passed:true or changed digest is not proof.
    if dict(binding) != current:
        raise GoalContractError("STALE_OR_INVALID_GOAL_BINDING")

def _roots(manifest: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    roots: dict[str, tuple[str, str]] = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        declarations = manifest.get(section, {})
        if not isinstance(declarations, dict):
            raise GoalContractError("INVALID_MANIFEST_SECTION", section)
        for name, spec in declarations.items():
            if name in roots:
                raise GoalContractError("AMBIGUOUS_ROOT_DECLARATION", name)
            roots[name] = (section, spec)
    return roots

def validate_manifest_change(before: Mapping[str, Any], after: Mapping[str, Any], goal: Mapping[str, Any]) -> None:
    value = validate_goal(goal)
    roots = _roots(before)
    if value["goalKind"] == "baseline-observation":
        if dict(before) != dict(after):
            raise GoalContractError("BASELINE_OBSERVATION_CHANGED_MANIFEST")
        return
    expected = copy.deepcopy(dict(before))
    for name, version in value["exactTargets"].items():
        if name not in roots:
            raise GoalContractError("TARGET_NOT_DECLARED", name)
        section, _ = roots[name]
        expected[section][name] = version
    _roots(after)
    if dict(after) != expected:
        raise GoalContractError("MANIFEST_SCOPE_VIOLATION")

def npm_lock_format(lock):
    """Supported root locks; v3 has no legacy compatibility tree."""
    if not isinstance(lock,dict) or type(lock.get('lockfileVersion')) is not int or lock['lockfileVersion'] not in (2,3):
        raise GoalContractError('UNSUPPORTED_LOCK_FORMAT')
    if lock['lockfileVersion']==3 and 'dependencies' in lock:
        raise GoalContractError('UNSUPPORTED_LOCK_LEGACY_VIEW')
    return lock['lockfileVersion']


def validate_root_lock_versions(before: Mapping[str, Any], after: Mapping[str, Any], goal: Mapping[str, Any]) -> None:
    """Checks ONLY root assignments. Transitive instance/edge scope requires a separate gate."""
    value = validate_goal(goal)
    if npm_lock_format(before)!=npm_lock_format(after):
        raise GoalContractError("UNSUPPORTED_LOCK_FORMAT")
    old, new = before.get("packages"), after.get("packages")
    if (not isinstance(old, dict) or not isinstance(new, dict) or "" not in old or "" not in new
            or not isinstance(old[""],dict) or not isinstance(new[""],dict)):
        raise GoalContractError("INVALID_LOCK")
    declarations = _roots(old[""])
    for name in value["exactTargets"]:
        if name not in declarations:
            raise GoalContractError("TARGET_NOT_DECLARED", name)
        if value["goalKind"] == "baseline-observation" and old.get("node_modules/"+name,{}).get("version") != value["exactTargets"][name]:
            raise GoalContractError("BASELINE_OBJECTIVE_MISMATCH", name)
    for name in declarations:
        location = "node_modules/" + name
        expected = value["exactTargets"].get(name, old.get(location, {}).get("version"))
        if not expected or new.get(location, {}).get("version") != expected:
            raise GoalContractError("ROOT_LOCK_SCOPE_VIOLATION", name)
