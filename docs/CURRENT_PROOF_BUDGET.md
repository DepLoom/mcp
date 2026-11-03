# Host-owned current physical proof budget

This contract applies only to host registrations that explicitly pin `policy.currentProof` before goal and binding issuance. It is a resource allowance, not an acceptance result or additional write permission.

```json
{"schemaVersion":1,"scope":"CURRENT_PHYSICAL_READ","maxSeconds":20}
```

`maxSeconds` must be an integer from 1 through 120. The default explicit specification is 20 seconds; a host may select a larger allowance for a new registration. Unknown keys, null, booleans and nonfinite values are rejected. The existing policy digest in the goal, binding and final authorization binds the entire specification. Only normal preapproved host policy can select it. Public MCP calls accept no timeout or budget parameter.

A new host may explicitly select the fixed resource class below before goal and binding issuance:

```json
{"finalResources":{"schemaVersion":2,"resourceClass":"LARGE_PHYSICAL_TREE_CURRENT","finalMaxSeconds":1800,"workerMaxSeconds":1200,"returnHeadroomSeconds":60},"currentProof":{"schemaVersion":2,"scope":"CURRENT_PHYSICAL_READ","maxSeconds":300}}
```

This version permits a final operation of at most 1800 seconds, a mutating worker of at most 1200 seconds, and each independent physical read of at most 300 seconds. The parent reserves the full proof allowance and 60-second cleanup/return headroom before worker launch. The earliest original host, operation or durable transaction deadline still wins; resume cannot renew it. All fields are strict and digest-bound under preapproved host policy. Version 2 current reads require this exact resource class. The version 1 proof ceiling stays 120 seconds, the legacy final ceiling stays 600 seconds, and public MCP calls gain no numeric budget argument. Increased time grants no new writes and removes no verification checks. Separate fresh reads and runtime acceptance need their own room within the preregistered stage STOP.

An absent specification uses the legacy reader. Existing registries, grants, original deadlines, checkpoints and historical results are not rewritten or upgraded. To select another allowance, issue a genuinely new host registration and matching authorization.

One process-local monotonic budget covers sealed evidence lookup and physical source, swap, full target and preserved-backup tree, toolchain, environment and TAP checks. It is initially clamped by any original operation UTC deadline and checks that deadline again during the read. Clock rollback cannot revive the initial allowance. Timing state is not persisted across restarts. Positive fractional remainders pass through the opt-in Core manifest and I/O-admission path without rounding up to one second. Full-byte manifests and all original path/object/membership/stability checks remain required.

The final apply parent reserves a full independent proof allowance plus 60 seconds for two bounded cleanup paths and return overhead before launching the mutating worker. Any existing durable transaction deadline further clamps this reservation. Insufficient time rejects before launching the worker; looking up an attempt does not renew it. The worker's post-transaction current read also uses the registered specification, bounded by its original transaction deadline.

A new current query or terminal replay runs a fixed read-only child which independently reloads the pinned registry and sealed checkpoint. It acquires no writer lease, performs no install or project command, publishes no receipt and creates no attempt. Expired execution grants cannot resume writes; completed read-only inspection uses its own bounded read allowance while preserving all original metadata freshness gates. Worker and independent parent-reader canonical digests must still agree on apply.

Cooperative time checks cannot interrupt blocked filesystem I/O. The fixed read process is supervised with the existing process-tree runner. After timeout or cancellation, kill is requested, termination is awaited for at most ten seconds, output readers have their existing bounded shutdown, and an unconfirmed stop raises an error. A timed-out or late process cannot supply accepted current proof. The 60-second reserve is conservative cleanup headroom, not permission to keep doing physical checks after their deadline. Windows OS scheduling is not a hard real-time guarantee.

Final source-snapshot publication defers best-effort orphan reclamation through a context-local Core scope. This skips discovery and deletion of older runs' abandoned containers only; it preserves full source capture, durable validation, subsequent opening, both delivered-tree observations and cleanup of the capture's own temporary container. Ordinary calls retain their existing maintenance behavior and the next first-reap opportunity is not consumed by a deferred scope. This is not a resource-grant extension or evidence that orphan cleanup caused a particular timeout.

Diagnostics carry no authority. Authoritative budgeted reads disable synchronous diagnostic-file persistence; optional legacy passive diagnostics remain separate. A late response cannot become current success. Canonical checkpoint fields stay unchanged; historical verification and current physical continuity remain distinct.

Budgeted reads revalidate source, swaps, both full trees, toolchain and environment/TAP at the closing boundary, and confirm source content again afterward. This detects drift across earlier gates. Sequential full-tree reads provide the existing host-declared boundary observations, not an atomic snapshot against arbitrary hostile external writers. Raising the resource allowance does not expand this guarantee or waive the preserved-backup read.
