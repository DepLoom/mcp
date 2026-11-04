#!/usr/bin/env python3
"""One end-to-end workspace Yellow witness using public npm and OSV."""
import asyncio
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

import jsonschema
from mcp import ClientSession, StdioServerParameters, stdio_client
import requests

from deploom_mcp.distribution import blueprint_schema_path, release_manifest_path
from smoke_live_proxy_osv import Proxy
from workspace_yellow_witness_runtime import accept_workspace, prepare_runtime

PACKAGE = "is-number"
CURRENT = "1.0.0"
LATEST = "7.0.0"
REGISTRY = "https://registry.npmjs.org"

def make_workspace(project):
    app = project / "packages/app"
    app.mkdir(parents=True)
    root = {"name": "deploom-live-yellow", "version": "1.0.0",
            "workspaces": ["packages/*"], "dependencies": {PACKAGE: CURRENT}}
    member = {"name": "deploom-live-yellow-app", "version": "1.0.0"}
    for path, content in ((project / "package.json", root), (app / "package.json", member)):
        path.write_text(json.dumps(content), encoding="utf-8")

def hashes(project):
    return {path.relative_to(project).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in project.rglob("*") if path.is_file()}

async def plan_workspace(project, environment):
    params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "deploom_mcp"],
                                   cwd=str(project.parent), env=environment)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            inventory = await client.call_tool("observe_workspace_inventory_v1", {"project_path": str(project)})
            assert not inventory.is_error, inventory.structured_content
            result = await client.call_tool("get_upgrade_plan", {
                "project_path": str(project),
                "workspace_request": {
                    "schemaVersion": 2, "goalKind": "MONOREPO_TO_POLICY",
                    "inventoryDigest": inventory.structured_content["inventory"]["inventoryDigest"],
                    "targets": [{"selector": {"owner": ".", "section": "dependencies", "name": PACKAGE},
                                 "approvedLines": [1, 7], "versions": [CURRENT, LATEST], "exactTarget": None}],
                    "policy": {"targetLevel": "yellow"},
                    "preflight": {"reauthor": None, "candidateLimit": 1},
                    "environment": {"node": "24.18.0", "npm": "11.16.0"}, "topology": None,
                },
            })
            assert not result.is_error, result.structured_content
            return result.structured_content

def main():
    release = json.loads(release_manifest_path().read_bytes())
    assert release["mcpVersion"] == "0.2.6"
    assert release["core"]["revision"] == "c67c2544297ae60677c7a1c08a63bcf733db6f46"
    with tempfile.TemporaryDirectory(prefix="deploom-live-workspace-yellow-") as directory, Proxy() as proxy:
        root = Path(directory)
        project = root / "project"
        make_workspace(project)
        cache = prepare_runtime(root, project, REGISTRY, LATEST)
        before = hashes(project)
        endpoint = f"http://127.0.0.1:{proxy.server_address[1]}"
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            response = requests.get(f"{REGISTRY}/{PACKAGE}", proxies={"http": endpoint, "https": endpoint}, timeout=20)
            response.raise_for_status()
            packument = response.json()
            assert packument["dist-tags"]["latest"] == LATEST
            current_date, latest_date = (date.fromisoformat(packument["time"][v][:10]) for v in (CURRENT, LATEST))
            assert current_date < latest_date.replace(year=latest_date.year - 1)
            proxy.clear()
            environment = {**os.environ, "HTTPS_PROXY": endpoint, "HTTP_PROXY": endpoint,
                           "https_proxy": endpoint, "http_proxy": endpoint, "ALL_PROXY": "", "all_proxy": "",
                           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                           "DEPLOOM_MCP_STATE_DIR": str(root / "state"),
                           "DEPLOOM_WORKSPACE_POLICY_REGISTRY": REGISTRY,
                           "DEPLOOM_WORKSPACE_POLICY_OSV_PACKAGES": PACKAGE,
                           "DEPLOOM_WORKSPACE_POLICY_SYSTEM_PROXY": "1",
                           "DEPLOOM_WORKSPACE_NPM_ARTIFACTS": str(root / "native"),
                           "DEPLOOM_WORKSPACE_NPM_CACHE": str(cache),
                           "DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY}
            plan = asyncio.run(asyncio.wait_for(plan_workspace(project, environment), 85))
            jsonschema.validate(plan, json.loads(blueprint_schema_path(workspace=True).read_bytes()))
            policy = plan["policy"]
            facts = policy["evidence"]
            targets = plan["jointAssignment"]["targets"]
            hosts = proxy.snapshot()
            print(json.dumps({"mcpVersion": release["mcpVersion"], "coreRevision": release["core"]["revision"],
                              "mode": "LIVE_WORKSPACE_PLANNING", "from": CURRENT, "currentDate": str(current_date),
                              "latestDate": str(latest_date), "targets": targets, "policy": policy,
                              "proxyConnects": hosts, "sourceUnchanged": hashes(project) == before,
                              "executionReady": plan["executionReady"]}, sort_keys=True), flush=True)
            assert hashes(project) == before and not (project / "node_modules").exists()
            assert (plan["authority"], plan["executionReady"]) == ("PLANNING_ONLY", False)
            assert (policy["targetLevel"], policy["status"]) == ("yellow", "FEASIBLE_IN_MODELED_DOMAIN")
            assert (policy["lagDenominator"], policy["lagConfirmed"], policy["lagRequired"]) == (1, 1, 1)
            assert facts["exactVersionCount"] == 2 and facts["unknownSecurityVersionCount"] == 0
            assert facts["unknownLagVersionCount"] == 0 and facts["source"] == "CORE_REGISTRY_AND_EXACT_OSV"
            assert len(targets) == 1 and targets[0]["selectedVersion"] == LATEST
            assert policy["finalAudit"] == plan["installation"]["status"] == plan["runtime"]["status"] == "NOT_RUN"
            assert all(hosts.get(host, 0) > 0 for host in ("api.osv.dev", "registry.npmjs.org"))
            print("LIVE_WORKSPACE_YELLOW_PLAN_CONFIRMED", flush=True)
            accepted = accept_workspace(root, project, plan, cache, REGISTRY, targets[0]["selectedVersion"])
            assert hashes(project) == before and not (project / "node_modules").exists()
            print(json.dumps({**accepted, "coreRevision": release["core"]["revision"],
                              "sourceUnchanged": True}, sort_keys=True), flush=True)
            print("LIVE_WORKSPACE_YELLOW_ACCEPTANCE_CONFIRMED", flush=True)
        finally:
            proxy.shutdown()
            thread.join(5)

if __name__ == "__main__":
    main()
