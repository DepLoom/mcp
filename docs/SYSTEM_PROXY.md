# System HTTP/HTTPS proxy for DepLoom MCP

The proxy is **opt-in**. Core already supports \`--use-system-proxy\` and
\`requests.Session.trust_env\`. MCP forwards that existing switch and
does not implement a second network client.

## Precedence and defaults

\`get_upgrade_plan\` and \`audit_dependencies\` accept an optional
\`use_system_proxy\` boolean. Priority is:

1. Explicit tool \`true\` or \`false\` (highest, including false over env true).
2. \`DEPLOOM_MCP_USE_SYSTEM_PROXY=true|false\` from the server's environment.
3. No configuration: **false**, preserving the previous planning behavior.

The env parser also accepts \`1/0\`, \`yes/no\`, \`on/off\` (case-insensitive).
Invalid env or tool values fail closed with \`invalid-system-proxy\`, rather than
silently activating a proxy. An explicit boolean wins even when the env is bad.

### MCP server configuration

For a Python-installed MCP server, configure the environment of the **server
process**, not just the user's terminal. For Codex, for example:

\`\`\`toml
[mcp_servers.deploom]
command = "python"
args = ["-m", "deploom_mcp"]
env = { DEPLOOM_MCP_USE_SYSTEM_PROXY = "true" }
\`\`\`

In addition, make \`HTTPS_PROXY\`/\`HTTP_PROXY\` and \`NO_PROXY\` visible to
that process if the host's system proxy configuration does not already
provide the correct routes. Keep credentials out of source-controlled configs.

### Explicit tool call

\`\`\`json
{
  "project_path": "/absolute/path/to/project",
  "target_level": "yellow",
  "use_system_proxy": true
}
\`\`\`

When effective=true, MCP appends exactly one \`--use-system-proxy\` to the
Core generator command. When false, MCP omits the flag; Core's existing
default remains false. No \`--no-use-system-proxy\` or Core API change is
needed for an explicit false override.

The audit-enabled server \`python -m deploom_mcp.audit_server\` also accepts:

\`\`\`json
{
  "project_path": "/absolute/path/to/project",
  "package_manager": "auto",
  "include_lag": true,
  "use_system_proxy": true
}
\`\`\`

Audit uses native npm and Yarn CLIs, not requests. Effective=true preserves
system-proxy environment variables for these processes and Core's \`npm view\`
lag checks. Effective=false removes inherited HTTP_PROXY, HTTPS_PROXY,
ALL_PROXY and FTP_PROXY (case-insensitively) from **isolated audit
subprocesses**, but preserves per-project registry/proxy configuration.
The audit does not silently fall back from a corporate registry to npmjs.org.

\`evaluate_assignment\` and \`explain_conflict\` only read local Blueprint
evidence, so they have no proxy parameter.

## UNKNOWN_SECURITY / OSV troubleshooting

1. Inspect the full Blueprint / planner evidence and exact OSV error.
   \`UNKNOWN_SECURITY\` means that security evidence is missing, not zero
   findings or a safe target.
2. Confirm the MCP server process actually sees the proxy and certificate
   configuration. A proxy configured only for the agent's shell may not
   be inherited by the stdio server.
3. Call \`get_upgrade_plan\` with \`use_system_proxy=true\` or set the env.
   Use a new run and inspect current data; previous UNKNOWN is not magically
   repaired. Check the registry metadata cache TTL and candidate evidence cap.
4. If enabling proxy breaks a corporate npm registry, pass explicit
   \`use_system_proxy=false\` and investigate NO_PROXY, proxy auth and CA.
5. Differentiate DNS failures, HTTP 401/403/407, TLS/cert issues, OSV
   service outages, unavailable advisories, peer conflicts and candidate
   search limits. None should be converted into security PASS.

## What the tests prove

Unit tests verify default=false, boolean and environment priority, invalid
settings, native CLI proxy environment, and OSV-UNKNOWN fail-closed semantics.
Real stdio MCP tests use a **fake Core** that records whether
\`--use-system-proxy\` reached the subprocess; this is a transport check,
not a live OSV connectivity test.

A live OSV check must use an actual, nonempty project through the **real**
Core/MCP and observe genuine api.osv.dev responses. If its external
network is blocked, record \`LIVE_OSV_UNVERIFIED\`; do not substitute mock
evidence or call a clean security verdict.
