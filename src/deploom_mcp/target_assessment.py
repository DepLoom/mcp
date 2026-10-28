"""Preserve Core's final-assignment health without selecting or certifying targets.

Current-version coverage and target coverage are different observations.  All
values below come from published Core health; missing values are never zero.
Policy violations are separate from incomplete evidence.  A project-level
finding does not identify an offending package.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

TARGET_PROJECTION_FIELDS = (
    "postPlanLagOk", "postPlanLagOkPct", "postPlanScopeTotal",
    "postPlanTargetLevel", "postPlanPolicyRequired", "postPlanReserveRequired",
    "postPlanPolicyShortfall", "postPlanReserveShortfall", "postPlanShortfall",
    "postPlanCritical", "postPlanHigh", "postPlanModerate", "postPlanLow",
    "postPlanSecurityKnown", "postPlanSecurityUnknown", "postPlanSecurityTotal",
    "postPlanGoal", "postPlanPolicyMaxCritical", "postPlanPolicyMaxHigh",
    "postPlanPolicyMaxModerate", "postPlanPolicyMaxLow", "postPlanTruncatedProposed",
    "postPlanLagOkYellow", "postPlanLagOkPctYellow", "postPlanLagOkGreen",
    "postPlanLagOkPctGreen", "postPlanYellowShortfall", "postPlanGreenShortfall",
)
_SECURITY_FIELDS = (
    "postPlanSecurityKnown", "postPlanSecurityUnknown", "postPlanSecurityTotal",
    "postPlanCritical", "postPlanHigh", "postPlanModerate", "postPlanLow",
)
_GOALS = {"feasible", "blocked", "unknown"}
_INTEGER_FIELDS = tuple(k for k in TARGET_PROJECTION_FIELDS
                        if not k.endswith("Pct") and "Pct" not in k
                        and k not in {"postPlanGoal", "postPlanTargetLevel"})


def projected_health(value: Any) -> Dict[str, Any]:
    """Copy published fields exactly, including null/invalid values for review."""
    if not isinstance(value, Mapping):
        return {}
    return {key: value[key] for key in TARGET_PROJECTION_FIELDS if key in value}


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _health_findings(health: Mapping[str, Any]) -> tuple[list[tuple[str, str, str, str]], bool]:
    findings = []
    missing = [k for k in (*_SECURITY_FIELDS, "postPlanGoal")
               if k not in health or health[k] is None or (k == "postPlanGoal" and health[k] == "")]
    invalid = [k for k in _INTEGER_FIELDS if k in health and health[k] is not None and not _count(health[k])]
    goal = health.get("postPlanGoal")
    if goal is not None and goal != "" and (not isinstance(goal, str) or goal not in _GOALS):
        invalid.append("postPlanGoal")
    for key in TARGET_PROJECTION_FIELDS:
        if "Pct" in key and key in health and health[key] is not None:
            value = health[key]
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or not 0 <= value <= 100):
                invalid.append(key)
    level = health.get("postPlanTargetLevel")
    if level is not None and (not isinstance(level, str) or level not in {"yellow", "green", "mixed"}):
        invalid.append("postPlanTargetLevel")
    known, unknown, total = (health.get(k) for k in _SECURITY_FIELDS[:3])
    if all(_count(v) for v in (known, unknown, total)):
        if known + unknown != total:
            invalid.append("postPlanSecurityKnown+postPlanSecurityUnknown!=postPlanSecurityTotal")
        scope = health.get("postPlanScopeTotal")
        if _count(scope) and scope != total:
            invalid.append("postPlanScopeTotal!=postPlanSecurityTotal")
    violations = []
    for severity in ("Critical", "High", "Moderate", "Low"):
        value = health.get("postPlan" + severity)
        limit = health.get("postPlanPolicyMax" + severity)
        if _count(value) and _count(limit) and value > limit:
            violations.append(f"postPlan{severity}={value} exceeds published limit {limit}")
    shortfall = health.get("postPlanPolicyShortfall")
    if _count(shortfall) and shortfall > 0:
        violations.append(f"postPlanPolicyShortfall={shortfall}")
    if goal == "feasible" and ((_count(unknown) and unknown > 0) or violations):
        invalid.append("postPlanGoal=feasible contradicts published target evidence")
    if missing:
        findings.append(("TARGET_ASSESSMENT_UNKNOWN", "REVIEW",
                         "Final target assessment is missing required fields: " + ", ".join(missing),
                         "FETCH_TARGET_ASSESSMENT"))
    if invalid:
        findings.append(("TARGET_ASSESSMENT_INVALID", "REVIEW",
                         "Final target assessment contains inconsistent or invalid fields: " + ", ".join(invalid),
                         "RECONCILE_TARGET_ASSESSMENT"))
    if _count(unknown) and unknown > 0:
        findings.append(("TARGET_SECURITY_UNKNOWN", "REVIEW",
                         f"Security evidence is unavailable for {unknown} final planned declaration(s); current-version coverage does not assess these versions.",
                         "FETCH_TARGET_SECURITY_EVIDENCE"))
    if goal == "blocked" or violations:
        message = ("Core reports that the final planned assignment is blocked by its applied policy."
                   if goal == "blocked" else "Published target evidence violates an explicit policy threshold; the Core goal verdict is retained.")
        if violations:
            message += " " + "; ".join(violations)
        findings.append(("TARGET_POLICY_BLOCKED", "BLOCKER", message, "REPLAN_TARGET_POLICY"))
    if goal == "unknown" and not (_count(unknown) and unknown > 0):
        findings.append(("TARGET_POLICY_UNKNOWN", "REVIEW",
                         "Core cannot establish the policy goal for this final assignment; inspect the published target assessment.",
                         "FETCH_TARGET_POLICY_EVIDENCE"))
    complete = (not missing and not invalid and unknown == 0 and isinstance(goal, str) and goal in {"feasible", "blocked"})
    return findings, complete


def _cross_project_findings(aggregate: Mapping[str, Any], projects: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Check only identities in Core's published aggregation, not solver policy."""
    if not projects:
        return []
    inconsistent = []
    # Core sums per-project counters, including its per-project unique-name
    # severity counts. Do not deduplicate those across projects in this adapter.
    for key in _SECURITY_FIELDS:
        values = [health.get(key) for health in projects.values()]
        if _count(aggregate.get(key)) and all(_count(value) for value in values):
            if aggregate[key] != sum(values):
                inconsistent.append(key)
    goals = [h.get("postPlanGoal") for h in projects.values()]
    if (isinstance(aggregate.get("postPlanGoal"), str) and aggregate["postPlanGoal"] in _GOALS
            and all(isinstance(goal, str) and goal in _GOALS for goal in goals)):
        expected = "blocked" if "blocked" in goals else "unknown" if "unknown" in goals else "feasible"
        if aggregate["postPlanGoal"] != expected:
            inconsistent.append("postPlanGoal")
    return inconsistent


def target_assessment(blueprint: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize raw published health; never trust a cached completeness flag."""
    aggregate = projected_health(blueprint.get("projections"))
    raw_projects = blueprint.get("perProject")
    raw_projects = raw_projects if isinstance(raw_projects, Mapping) else {}
    names = {str(name) for name in raw_projects}
    for row in blueprint.get("dependencies") or []:
        if isinstance(row, Mapping) and isinstance(row.get("project"), str) and row["project"]:
            names.add(row["project"])
    projects = {name: projected_health(raw_projects.get(name)) for name in sorted(names)}
    aggregate_findings, aggregate_complete = _health_findings(aggregate)
    project_checks = [_health_findings(value) for value in projects.values()]
    inconsistencies = _cross_project_findings(aggregate, projects)
    return {
        "scope": "FINAL_PLANNED_ASSIGNMENT",
        "aggregate": aggregate,
        "projects": projects,
        "evidenceComplete": aggregate_complete and all(complete for _, complete in project_checks) and not inconsistencies,
        "needsReview": bool(aggregate_findings or any(findings for findings, _ in project_checks) or inconsistencies),
    }


def target_decision_evidence(assessment: Mapping[str, Any]) -> list[Dict[str, Any]]:
    """Typed scope-level reasons, with pointers into the full blueprint."""
    aggregate = assessment.get("aggregate") or {}
    projects = assessment.get("projects") or {}
    result = []
    project_codes: set[str] = set()

    def append_findings(health, *, scope, project, pointer):
        findings, _ = _health_findings(health)
        for code, severity, message, action in findings:
            result.append({"code": code, "severity": severity, "scope": scope,
                           "project": project, "package": None, "message": message,
                           "nextAction": action, "lifecycle": "ACTIVE_FINAL",
                           "evidenceRef": pointer})
        return {code for code, _, _, _ in findings}

    for name, health in sorted(projects.items()):
        pointer_name = name.replace("~", "~0").replace("/", "~1")
        project_codes.update(append_findings(health, scope="project", project=name,
                                            pointer=f"#/targetAssessment/projects/{pointer_name}"))
    before_aggregate = len(result)
    append_findings(aggregate, scope="aggregate", project=None, pointer="#/targetAssessment/aggregate")
    inconsistencies = _cross_project_findings(aggregate, projects)
    # Suppress a repeated aggregate reason only when a project carries it and
    # the published aggregate agrees with the project assessments. Missing or
    # invalid aggregate data is never hidden by a more specific project reason.
    if not inconsistencies:
        duplicated = {"TARGET_SECURITY_UNKNOWN", "TARGET_POLICY_BLOCKED", "TARGET_POLICY_UNKNOWN"}
        result = result[:before_aggregate] + [item for item in result[before_aggregate:]
                 if not (item["code"] in duplicated and item["code"] in project_codes)]
    if inconsistencies:
        result.append({"code": "TARGET_ASSESSMENT_INVALID", "severity": "REVIEW", "scope": "aggregate",
                       "project": None, "package": None, "lifecycle": "ACTIVE_FINAL",
                       "message": "Aggregate target assessment disagrees with published projects: " + ", ".join(inconsistencies),
                       "nextAction": "RECONCILE_TARGET_ASSESSMENT", "evidenceRef": "#/targetAssessment"})
    return sorted(result, key=lambda item: (0 if item["severity"] == "BLOCKER" else 1,
                                          str(item["project"] or ""), item["code"]))
