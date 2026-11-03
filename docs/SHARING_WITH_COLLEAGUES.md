# Sharing DepLoom MCP with colleagues

[Русский](SHARING_WITH_COLLEAGUES.ru.md) · [User guide](../USERGUIDE.md)

## Self-hosted MCP (stdio)

The simplest trial: share the release link and user guide. Every colleague installs locally and registers their own Python path and Skill. The wheel includes the pinned Core. Your computer does not need to remain on.

You can instead prepare a ZIP with the public wheel, checksums, Python dependency wheels and instructions. Do not copy your venv, agent configuration, credentials, MCP state or project evidence. A copied Windows venv is not a portable package.

### Prepare an offline kit on Windows

Use Python matching the recipient's version, OS and CPU architecture. Dependency wheels can be platform-specific. Run from a dedicated empty working directory:

```powershell
$bundle = Join-Path $PWD 'deploom-trial'
New-Item -ItemType Directory -Force "$bundle\wheelhouse" | Out-Null
$wheel = Join-Path $bundle 'deploom_mcp-0.2.2-py3-none-any.whl'
Invoke-WebRequest 'https://github.com/DepLoom/mcp/releases/download/v0.2.2/deploom_mcp-0.2.2-py3-none-any.whl' -OutFile $wheel
Invoke-WebRequest 'https://github.com/DepLoom/mcp/releases/download/v0.2.2/SHA256SUMS.txt' -OutFile "$bundle\SHA256SUMS.txt"
$line = Get-Content "$bundle\SHA256SUMS.txt" | Where-Object { $_ -match 'deploom_mcp-0\.2\.2-py3-none-any\.whl$' }
$expected = ($line -split '\s+')[0]
$actual = (Get-FileHash $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $expected -or $actual -ne $expected.ToLowerInvariant()) { throw 'Release checksum mismatch' }
python -m pip download --only-binary=:all: --dest "$bundle\wheelhouse" $wheel
if ($LASTEXITCODE -ne 0) { throw 'Incomplete wheelhouse; do not distribute' }
Get-ChildItem "$bundle\wheelhouse" -File | Get-FileHash -Algorithm SHA256 |
  Select-Object @{n='File';e={Split-Path $_.Path -Leaf}}, Hash |
  Export-Csv "$bundle\wheelhouse-sha256.csv" -NoTypeInformation -Encoding UTF8
```

Use the approved Python index/proxy, without copying credentials into the kit. Add a copy of the user guide, then ZIP only this bundle folder. The CSV inventories dependency downloads; it is not an independent publisher signature. Verify delivery through your normal trusted channel.

### Install on the recipient's computer

Unpack, open PowerShell in the bundle directory:

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse deploom-mcp==0.2.2
if ($LASTEXITCODE -ne 0) { throw 'Install failed' }
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\deploom-install-skill.exe --client opencode --scope user --verify
```

Register that machine's absolute `.venv/Scripts/python.exe` path in `mcp.servers`, following the [user guide](../USERGUIDE.md#3-connect-opencode-v2). Keep the venv at that path after installation.

Offline Python installation does not make registry/advisory planning offline. Those services must be available or the missing evidence remains UNKNOWN.

### Distribute the files from your computer

An approved shared folder/internal file-transfer service is normally enough. If HTTP file sharing is permitted in your environment, serve only the dedicated bundle directory (replace `YOUR_LAN_ADDRESS`):

```powershell
python -m http.server 8765 --bind YOUR_LAN_ADDRESS --directory .\deploom-trial
```

Colleagues download the files using that address and port. Stop with Ctrl+C when finished. Apply your normal firewall/network access rules. This simple file server has no authentication/TLS; use an existing authenticated internal service when required. Never serve your home or project directory.

**This distributes files. It is not a Networked MCP endpoint.**

## Networked MCP (remote HTTP)

One shared server accepting multiple MCP clients would be a different deployment. The 0.2.2 packaged entrypoint is **stdio only**: no supported HTTP startup flag, authentication configuration or multi-user/project-isolation profile ships here.

A future/shared SDK or gateway deployment needs authenticated transport, explicit project-path boundaries, least-privilege host identity, state/evidence isolation and concurrency/process limits. A colleague's local project is not automatically available on that host.

Do not treat a file server as MCP or an unvalidated transport adapter as production support. For today's trials, use self-hosted stdio and share the installation kit.
