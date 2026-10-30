# D1 public Yellow demo on Windows PowerShell 5.1

The same pinned P2/installed-wheel stdio/Yellow control and evaluator-only
real Chalk 6 npm ci/build/test/runtime used by Linux CI. No model, Codex,
new Core product contract, new credentials, broad audits or package publication.

Requirements: Windows PowerShell 5.1, Git, Python 3.12, Node.js 22/npm and
public npm/PyPI/OSV network access. From an exact Git checkout, one PowerShell
script creates a **new** directory under TEMP, creates a dedicated Python
venv, builds a wheel there, verifies the pinned release inventory and Skill,
then performs the full no-agent demo. It does not modify global pip,
execution policy, user directories or existing project files.

## Exact checkout and one demo entrypoint

Copy the current 40-character MCP PR #13 HEAD into $McpSha. These commands
are for **Windows PowerShell 5.1**, not Bash or CMD:

~~~powershell
$ErrorActionPreference = 'Stop'
$McpSha = '<exact 40-character SHA from MCP PR #13>'
git clone https://github.com/DepLoom/mcp.git
if ($LASTEXITCODE -ne 0) { throw 'CLONE_FAILED' }
Set-Location .\mcp
git fetch origin refs/pull/13/head
if ($LASTEXITCODE -ne 0) { throw 'PR_FETCH_FAILED' }
git checkout --detach $McpSha
if ($LASTEXITCODE -ne 0) { throw 'EXACT_CHECKOUT_FAILED' }
git submodule update --init --recursive
if ($LASTEXITCODE -ne 0) { throw 'SUBMODULE_CHECKOUT_FAILED' }
$Output = Join-Path $env:TEMP ('deploom-d1-win-' + [guid]::NewGuid().ToString('N'))
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\d1_yellow_windows_demo.ps1 -ExpectedMcpSha $McpSha -OutputDir $Output
if ($LASTEXITCODE -ne 0) { throw 'WINDOWS_D1_FAILED' }
Get-Content -LiteralPath (Join-Path $Output 'd1-windows-demo-summary.json')
~~~

The script uses LASTEXITCODE after **every native command**, with
PowerShell 5.1-compatible syntax (no Bash && or PowerShell 7 operators).
ExecutionPolicy Bypass applies only to its **child process**. It refuses
existing output directories instead of deleting, reusing or overwriting them.
Create a fresh unique directory for each run.

## Evidence and acceptance boundaries

Output directory: dist/ with locally built wheel,
d1-frozen-chalk/ (eight public files),
d1-p2-control.json, d1-mcp-value.json, d1-yellow-policy.json,
d1-windows-demo-summary.json. The GitHub Actions workflow uploads a
sanitized artifact for Windows and preserves the existing Ubuntu D1 artifact.

MCP exact HEAD and Core SHA are checked before build, alongside release
manifest and 11-file Skill identity. Frozen P2 source SHA-256 must match
4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6.
Record the locally built wheel SHA-256: Windows and Linux **need not match**.
On registry timestamp/version drift the demo **fails** rather than altering
the original frozen fixture, accepted lag threshold or evidence expectations.

Expected three independent classifications:

| Case | Exact planning | Yellow health | Runtime |
| --- | --- | --- | --- |
| Original Chalk 5.3.0 | PROPOSED / PLANNING_ONLY / NOT_VERIFIED | BLOCKED 50% (1/2), TARGET_POLICY_BLOCKED | NOT_VERIFIED (agent) |
| Separate Chalk 5.6.2 (negative) | PROPOSED / NOT_VERIFIED | BLOCKED 50% (1/2) | No agent work |
| Separate Chalk 6.0.0 | PROPOSED / NOT_VERIFIED | feasible 100% (2/2) | PASS, EVALUATOR_ONLY_NOT_AGENT |

The last row is evaluator-authored **in a disposable copy** and has
requiresAuthorization=true for any new real user target or approved major.
This is not an agent migration, not an implicit authorization and not a
verified result of MCP execution. Frozen Chalk P2, is-number@7.0.0,
all original controls, exact scopes and Yellow 80% remain unchanged.
Paid agent sessions: zero. The CI Windows full demo does not substitute
Windows unit tests for a real runtime replay.
