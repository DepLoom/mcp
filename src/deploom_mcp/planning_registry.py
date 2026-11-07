"""Select a single approved registry for flat project planning."""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from deploom_mcp.dependency_audit import AuditError, checked_registry
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import _read_bytes

_PUBLIC = {"https://registry.npmjs.org", "https://registry.yarnpkg.com"}
_REGISTRY_ROUTE = re.compile(r"^\s*[\"']?(?:registry|@[^\s=:]+:registry)[\"']?(?:\s|=|$)", re.I)


def _url(value: str | None) -> str | None:
    result = checked_registry(value)
    if result and any(c in result for c in "`$;{}"):
        raise AuditError("invalid-registry", "registry contains an unsupported character")
    return result


def _configured_routes(raw: bytes, *, yarn: bool) -> set[str]:
    routes = {}
    has_endpoint_auth = False
    for line in raw.decode("utf-8-sig").splitlines():
        if line.lstrip().startswith("//"):
            has_endpoint_auth = True
        if not _REGISTRY_ROUTE.match(line):
            continue
        match = (re.fullmatch(r"\s*([^\s=]+)\s+(.+?)\s*", line) if yarn else
                 re.fullmatch(r"\s*([^\s=]+)\s*=\s*(.+?)\s*", line))
        if not match:
            raise AuditError("project-registry-unsupported", "Use an explicit registry for this project configuration")
        key, value = match.groups()
        key = key.strip("\"'").lower()
        if value[:1] in {"\"", "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        if key in routes:
            raise AuditError("project-registry-ambiguous", "Use an explicit registry when a project route is repeated")
        routes[key] = _url(value)
    if (routes or has_endpoint_auth) and "registry" not in routes:
        raise AuditError("project-registry-ambiguous", "Use an explicit registry for a scope-only or authentication-only registry configuration")
    return set(routes.values())


def _yarn_yaml_mapping(node: yaml.Node) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        raise AuditError("project-registry-unsupported", "Expected a mapping in Yarn registry configuration")
    fields = {}
    for key, value in node.value:
        if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
            raise AuditError("project-registry-unsupported", "Expected string keys in Yarn registry configuration")
        if key.value in fields:
            raise AuditError("project-registry-ambiguous", "Yarn registry configuration has a repeated key")
        fields[key.value] = value
    return fields


def _configured_yarn_yaml_routes(raw: bytes) -> set[str]:
    try:
        if any(isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for token in yaml.scan(raw)):
            raise AuditError("project-registry-unsupported", "Yarn registry configuration cannot use YAML aliases")
        document = yaml.compose(raw, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError):
        raise AuditError("project-registry-unsupported", "Could not parse Yarn registry configuration") from None
    if document is None:
        return set()

    config = _yarn_yaml_mapping(document)
    default = config.get("npmRegistryServer")
    scopes = _yarn_yaml_mapping(config["npmScopes"]) if "npmScopes" in config else {}
    scoped = [settings["npmRegistryServer"] for scope in scopes.values()
              if "npmRegistryServer" in (settings := _yarn_yaml_mapping(scope))]
    if scoped and default is None:
        raise AuditError("project-registry-ambiguous", "Use an explicit registry for Yarn scope-only routing")

    routes = set()
    for node in ([default] if default is not None else []) + scoped:
        if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
            raise AuditError("invalid-registry", "Yarn registry must be a literal HTTP(S) URL")
        routes.add(_url(node.value))
    return routes


def planning_registry(project: Path, explicit: str | None) -> str | None:
    approved = _url(os.environ.get("DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY"))
    restricted = approved is not None and os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"
    if explicit is not None:
        selected = _url(explicit.strip() if isinstance(explicit, str) else explicit)
        if restricted and selected != approved:
            raise AuditError("project-registry-not-authorized", "Planning registry must match the MCP host's approved registry")
        return selected

    if not project.is_dir():
        return None
    routes = set()
    try:
        for name in (".npmrc", ".yarnrc", ".yarnrc.yml"):
            path = project / name
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raw = _read_bytes(path, 16 * 1024, root=project)
            if name == ".yarnrc.yml":
                routes.update(_configured_yarn_yaml_routes(raw))
            else:
                routes.update(_configured_routes(raw, yarn=name == ".yarnrc"))
    except (GoalContractError, OSError, UnicodeError):
        raise AuditError("project-registry-unavailable", "Could not safely read the project registry configuration") from None
    if len(routes) > 1:
        raise AuditError("project-registry-ambiguous", "Use an explicit registry for a project with multiple registry routes")
    if not routes:
        return approved if restricted else None
    selected = next(iter(routes))
    if not selected.startswith("https://"):
        raise AuditError("project-registry-unsupported", "Automatic project registry selection requires HTTPS")
    if selected != approved and (restricted or selected not in _PUBLIC):
        raise AuditError("project-registry-not-authorized",
                         "Pass the registry explicitly or configure the MCP host's approved project registry")
    return selected
