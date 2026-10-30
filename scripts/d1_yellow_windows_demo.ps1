#Requires -Version 5.1
<#
  Exact public checkout -> isolated Python venv and wheel -> P2 oracle ->
  installed-wheel stdio -> Yellow policy -> independent Chalk6 runtime.
  No Codex/LLM, no global pip, no overwriting or deleting user directories.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$ExpectedMcpSha,
  [Parameter(Mandatory=$true)][string]$OutputDir,
  [string]$BootstrapPython = 'python'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Check-Exit([string]$code) {
  if ($LASTEXITCODE -ne 0) { throw ("{0}: EXIT_{1}" -f $code,$LASTEXITCODE) }
}
function Run-Native([string]$exe,[string[]]$commandArgs,[string]$code) {
  & $exe @commandArgs
  Check-Exit $code
}
function Read-Json([string]$path) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'MISSING_RECEIPT' }
  return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}
function One-Scenario($rows,[string]$id) {
  $matches = @($rows | Where-Object { $_.scenarioId -eq $id })
  if ($matches.Count -ne 1) { throw 'SCENARIO_COUNT_MISMATCH' }
  return $matches[0]
}

try {
  if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSVersion.Major -ne 5) {
    throw 'REQUIRES_WINDOWS_POWERSHELL_5_1'
  }
  $gitHead = @(& git rev-parse --verify HEAD)
  Check-Exit 'GIT_HEAD'
  if ($gitHead.Count -ne 1 -or $gitHead[0].Trim() -cne $ExpectedMcpSha) {
    throw 'EXACT_MCP_HEAD_MISMATCH'
  }
  $dirty = @(& git status --porcelain --untracked-files=no)
  Check-Exit 'GIT_STATUS'
  if ($dirty.Count -gt 0) { throw 'TRACKED_CHECKOUT_DIRTY' }
  $coreSha = 'd1594c36eb5b35447e941cf0f528b8cbd15705b5'
  $actualCore = @(& git -C upstream/deploom rev-parse --verify HEAD)
  Check-Exit 'CORE_SUBMODULE'
  if ($actualCore.Count -ne 1 -or $actualCore[0].Trim() -cne $coreSha) {
    throw 'CORE_SHA_MISMATCH'
  }

  $root = (Get-Location).ProviderPath.TrimEnd('\')
  $out = [System.IO.Path]::GetFullPath($OutputDir)
  if ($out -ieq $root -or $out.StartsWith(($root+'\'),
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OUTPUT_MUST_BE_OUTSIDE_REPO'
  }
  if (Test-Path -LiteralPath $out) { throw 'OUTPUT_ALREADY_EXISTS_DO_NOT_DELETE' }
  if (-not (Test-Path -LiteralPath ([System.IO.Path]::GetDirectoryName($out)) -PathType Container)) {
    throw 'OUTPUT_PARENT_MISSING'
  }
  New-Item -Path $out -ItemType Directory -ErrorAction Stop | Out-Null

  # Environment adjustments are process-scoped; public PyPI only.
  $env:PIP_INDEX_URL='https://pypi.org/simple'
  $env:PIP_EXTRA_INDEX_URL=''
  $env:PIP_CONFIG_FILE='NUL'
  $env:PIP_NO_INPUT='1'
  $env:PIP_DISABLE_PIP_VERSION_CHECK='1'
  $env:PYTHONNOUSERSITE='1'
  $env:PYTHONPATH=''
  $env:PYTHONHOME=''
  $env:NPM_TOKEN=''
  $env:NODE_AUTH_TOKEN=''
  $env:CI='true'

  $venv = Join-Path $out 'isolated-python'
  Run-Native $BootstrapPython @('-m','venv',$venv) 'VENV_CREATE_FAILED'
  $py = Join-Path $venv 'Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $py -PathType Leaf)) { throw 'VENV_PYTHON_MISSING' }
  # PowerShell 5.1/Win32 strips embedded double quotes in python -c.
  # Use a quote-free expression so the observed interpreter version is stable.
  $ver = @(& $py -c 'import sys;print(sys.version_info.major,sys.version_info.minor,sep=chr(46))')
  Check-Exit 'PYTHON_VERSION'
  if ($ver.Count -ne 1 -or $ver[0].Trim() -cne '3.12') { throw 'REQUIRES_PYTHON_3_12' }
  $node = @(& node --version)
  Check-Exit 'NODE_VERSION'
  if ($node.Count -ne 1 -or -not $node[0].StartsWith('v22.')) { throw 'REQUIRES_NODE_22' }
  Run-Native 'npm.cmd' @('--version') 'NPM_MISSING'

  Run-Native $py @('scripts/update_release_manifest.py','--check') 'RELEASE_MANIFEST_MISMATCH'
  Run-Native $py @('scripts/check_skill.py') 'SKILL_BUNDLE_MISMATCH'
  Run-Native $py @('-m','pip','install','--no-input','--disable-pip-version-check',
    '-e','.','build','setuptools','wheel') 'ISOLATED_PIP_FAILED'
  $dist = Join-Path $out 'dist'
  New-Item -Path $dist -ItemType Directory | Out-Null
  Run-Native $py @('-m','build','--no-isolation','--outdir',$dist) 'WHEEL_BUILD_FAILED'
  $wheel = Join-Path $dist 'deploom_mcp-0.2.0-py3-none-any.whl'
  if (-not (Test-Path -LiteralPath $wheel -PathType Leaf)) { throw 'EXPECTED_WHEEL_MISSING' }
  $wheelSha = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()

  $seed = Join-Path $out 'd1-frozen-chalk'
  $p2Path = Join-Path $out 'd1-p2-control.json'
  $d1Path = Join-Path $out 'd1-mcp-value.json'
  $yellowPath = Join-Path $out 'd1-yellow-policy.json'
  Run-Native $py @('scripts/correctness_preflight.py','breaking-demo',
    '--wheel',$wheel,'--snapshot-dir',$seed,'--report',$p2Path) 'PUBLIC_P2_FAILED'
  Run-Native $py @('scripts/d1_mcp_value_preflight.py',
    '--frozen',$seed,'--wheel',$wheel,'--report',$d1Path,'--require-go') 'WHEEL_STDIO_D1_FAILED'
  Run-Native $py @('scripts/d1_yellow_policy_demo.py',
    '--frozen',$seed,'--wheel',$wheel,'--receipt',$d1Path,
    '--report',$yellowPath) 'INDEPENDENT_YELLOW_RUNTIME_FAILED'

  $p2 = Read-Json $p2Path
  $d1 = Read-Json $d1Path
  $yellow = Read-Json $yellowPath
  $sourceSha = '4adfa455d02e9f354e5b47e7b4d983f6676b620b13dc1454bda9248272d97cb6'
  if ($p2.status -ne 'PASS' -or $p2.mcp.status -ne 'PASS' -or
      $p2.provenance.coreRevision -cne $coreSha -or
      $p2.provenance.mcpWheelSha256 -cne $wheelSha -or
      $p2.provenance.baselineSourceSha256 -cne $sourceSha) {
    throw 'P2_OR_SOURCE_PROVENANCE_MISMATCH'
  }
  foreach ($key in @('baselinePASS','versionOnlyInstallPASS','versionOnlyRuntimeFAIL',
      'referenceCodePatchPASS','hardcodedSuccessRejected','unsafeControlsFAIL',
      'realPackagedStdioPASS')) {
    if ($p2.checks.PSObject.Properties[$key].Value -ne $true) {
      throw 'P2_CONTROL_MISMATCH'
    }
  }
  if ($d1.gate -ne 'GO' -or $d1.gateScope -ne 'EXACT_VERSION_PLANNING_FEASIBILITY_ONLY' -or
      $d1.modelCalls -ne 0 -or $d1.coreRevision -cne $coreSha -or
      $d1.wheelSha256 -cne $wheelSha -or
      $d1.frozenObservedSha256 -cne $sourceSha) {
    throw 'D1_INSTALLED_WHEEL_PROVENANCE_MISMATCH'
  }
  $o = One-Scenario $d1.requests 'ORIGINAL_EXACT_5_3_0'
  $c = One-Scenario $d1.requests 'SEPARATE_CURRENT_POLICY_MIN_6_0_0'
  if ($o.chalkTarget -ne '5.3.0' -or
      $o.ordinaryPlanPolicy.finalHealthPolicy.postPlanGoal -ne 'blocked' -or
      $o.ordinaryPlanPolicy.finalHealthPolicy.postPlanLagOk -ne 1 -or
      $o.ordinaryPlanPolicy.finalHealthPolicy.postPlanPolicyRequired -ne 2 -or
      $o.ordinaryPlanPolicy.finalHealthPolicy.postPlanPolicyShortfall -ne 1 -or
      $o.explicitVersionAssessment.status -ne 'BLOCKED_BY_KNOWN_CONSTRAINTS') {
    throw 'ORIGINAL_5_3_0_NEGATIVE_MISSING'
  }
  if ($c.chalkTarget -ne '6.0.0' -or
      $c.ordinaryPlanPolicy.finalHealthPolicy.postPlanGoal -ne 'feasible' -or
      $c.ordinaryPlanPolicy.finalHealthPolicy.postPlanLagOk -ne 2 -or
      $c.ordinaryPlanPolicy.finalHealthPolicy.postPlanPolicyRequired -ne 2 -or
      $c.ordinaryPlanPolicy.finalHealthPolicy.postPlanPolicyShortfall -ne 0 -or
      $c.explicitVersionAssessment.status -ne 'MATCHES_PUBLISHED_PLAN') {
    throw 'SEPARATE_6_0_0_POSITIVE_MISSING'
  }
  if ($yellow.status -ne 'PASS' -or
      $yellow.compliantAlternative -ne 'POLICY_FEASIBLE_AND_RUNTIME_PASS' -or
      $yellow.wheelSha256 -cne $wheelSha -or
      $yellow.modelCalls -ne 0 -or $yellow.codexSessions -ne 0 -or
      $yellow.independentRuntime.status -ne 'PASS' -or
      $yellow.independentRuntime.attribution -ne 'EVALUATOR_ONLY_NOT_AGENT') {
    throw 'RUNTIME_OR_ATTRIBUTION_MISMATCH'
  }
  foreach ($key in @('lockGeneration','cleanInstall','build','test','runtimeMultiInput')) {
    if ($yellow.independentRuntime.checks.PSObject.Properties[$key].Value -ne $true) {
      throw 'INDEPENDENT_REAL_RUNTIME_CHECK_FAILED'
    }
  }
  $separate = @($yellow.scenarios | Where-Object { $_.id -eq 'SEPARATE_CURRENT_POLICY_MIN_6_0_0' })
  if ($separate.Count -ne 1 -or $separate[0].requiresAuthorization -ne $true) {
    throw 'UNAUTHORIZED_ALTERNATIVE'
  }

  $summary = [ordered]@{
    kind='D1_WINDOWS_PS51_FULL_NO_AGENT'
    platform='windows'
    powershell=$PSVersionTable.PSVersion.ToString()
    mcpSha=$ExpectedMcpSha
    coreSha=$coreSha
    wheelSha256=$wheelSha
    frozenSourceSha256=$sourceSha
    skillBundleSha256=$p2.provenance.skillBundleSha256
    manifest='MATCHED'
    skillIntegrity='MATCHED'
    installedWheelStdio='PASS'
    original=[ordered]@{
      exactTarget='chalk@5.3.0'
      planning='PROPOSED_PLANNING_ONLY_NOT_VERIFIED'
      policy='BLOCKED_50_PERCENT_1_OF_2'
      runtime='NOT_VERIFIED'
    }
    separate=[ordered]@{
      exactTarget='chalk@6.0.0'
      requiresAuthorization=$true
      planning='PROPOSED_PLANNING_ONLY_NOT_VERIFIED'
      policy='FEASIBLE_100_PERCENT_2_OF_2'
      runtime='PASS'
      attribution='EVALUATOR_ONLY_NOT_AGENT'
    }
    p2Controls='PASS'
    paidAgentSessions=0
  }
  $summary | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath (Join-Path $out 'd1-windows-demo-summary.json') -Encoding UTF8
  Write-Host 'D1_WINDOWS_FULL_NO_AGENT: PASS'
  Write-Host 'ORIGINAL: chalk@5.3.0 PROPOSED/PLANNING_ONLY/NOT_VERIFIED; Yellow BLOCKED 50% (1/2); agent runtime NOT_VERIFIED'
  Write-Host 'SEPARATE: chalk@6.0.0 PROPOSED/PLANNING_ONLY/NOT_VERIFIED; Yellow FEASIBLE 100% (2/2); evaluator runtime PASS; requiresAuthorization=true'
  Write-Host ('MCP_HEAD_SHA={0}' -f $ExpectedMcpSha)
  Write-Host ('CORE_SHA={0}' -f $coreSha)
  Write-Host ('WHEEL_SHA256={0}' -f $wheelSha)
  Write-Host ('FROZEN_SOURCE_SHA256={0}' -f $sourceSha)
  Write-Host ('EVIDENCE_DIR={0}' -f $out)
  exit 0
}
catch {
  # Fail closed on registry drift without changing the frozen project.
  Write-Host ('D1_WINDOWS_FULL_NO_AGENT: FAIL ({0})' -f $_.Exception.Message)
  exit 1
}
