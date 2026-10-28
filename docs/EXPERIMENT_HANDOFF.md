# Agent experiment handoff

This is a preparation checklist for the agent running a separately authorized
experiment. It is not an experiment result. Product regression tests establish
specific contracts; they do not establish agent behavior, migration quality,
speedup or end-to-end determinism. Use the study owner's frozen protocol for
conditions, prompts, budgets and outcome definitions.

## Freeze the tested delivery

Record the actual MCP revision with `git rev-parse HEAD`, the pinned Core
revision with `git -C upstream/deploom rev-parse HEAD`, Python/client/model
versions, tool configuration and the applicable CI run. A branch name or package
version alone is insufficient. Capture `release-manifest.json`, the Skill
`contract.json` and their hashes. For an installed wheel, retain its SHA-256 and
compare the actual runtime provenance with the intended delivery. An override
Core must retain its observed identity; do not substitute the expected pin.

In an environment with the project's declared dependencies already available,
run these engineering gates from the repository root and retain commands, exit
codes and logs:

```sh
python -m unittest discover -s tests -p test_target_assessment_integration.py -v
python -m unittest discover -s tests -p test_decision_contract.py -v
python scripts/check_skill.py
python scripts/update_release_manifest.py --check
python scripts/smoke_stdio.py
```

Use the normal CI matrix for full tests and packaging evidence. An environment
setup failure is distinct from a product assertion failure. Do not silently
substitute another interpreter or skip a failing contract and mark this gate
passed. These checks do not run a migration or call an experimental model.

Install the complete canonical Skill, including references, with the documented
installer into the experiment's designated client location. Record the install
receipt and observe which content the new experimental session actually loads.
The main file and any changed reference need loaded-content fingerprints or
captured text. A source manifest or install receipt proves bytes on disk only;
MCP's `loadedClientContent=UNVERIFIED` remains unverified until separate client
evidence exists. If a loaded revision cannot be observed, record that limitation
and do not claim that the experiment evaluated the revised rule.

## Gate on decision correctness

The offline real-Core regression distinguishes these cases for a known-clean
current `1.0.0` and chosen same-major `1.5.0`:

| Exact target evidence | Required observable decision |
| --- | --- |
| UNKNOWN | Target uncertainty is visible; evidence is incomplete; a review reason is present without inventing a proven policy violation. |
| Known Critical finding under a policy that permits none | The target policy is blocked; a typed blocker is visible. Evidence completeness remains a separate question. |
| Known clean | Complete target assessment is visible without a target-policy blocker; runtime remains unverified. |

Also preserve scoped attribution, clipping markers and evidence identities.
Inspect `targetAssessment`, `decisionEvidence` and `decisionCompleteness`
together. Do not infer target safety from current coverage, `PROPOSED`,
`DRAFT_READY`, untruncated rows or a successful tool call. Keep
`PLANNING_ONLY`/`NOT_VERIFIED` in every case. The contract is described in
[DECISION_CONTRACT.md](DECISION_CONTRACT.md).

## Keep the experimental contrast interpretable

One study owner prepares the frozen inputs and assigns isolated slots. Each
agent reads only its slot and explicitly shared inputs; keep sibling outputs,
installed versions, reports and usage inaccessible to that agent. Record every
operator intervention, retry and permission wait with its actual evidence.
An unexplained interval remains unexplained.

Specify the same effective task, allowed source/config changes, acceptance
checks and deliverables for comparable execution arms. Send the objective,
`target_mode`, mutable packages, allowed version lines, exclusions and health
policy explicitly; compare them with the applied response. Do not rely on an
API default to establish target authority. Record scope expansion and changed
targets so incomparable work is identified before any timing comparison.

Keep common tools, starting manifests/lockfiles and external-evidence access
consistent, except for predeclared treatment differences. If comparing two
Skill procedures, give them the same MCP schema and initial data-access route;
do not preload one arm with a full answer while requiring the other to obtain
it through tools. If isolating target authority, align every relevant channel
(Core handoff, compact contract, main Skill and references) with that one
conceptual factor. Replacing a full Skill bundle changes several factors and
must not be reported as an authority-only contrast.

Retain full and compact planning artifacts, effective prompts, actually loaded
Skill content, decision records and raw usage counters. Evidence digests do not
freeze registry/OSV responses; freeze or retain those inputs separately when the
protocol requires replay. Keep cache reads separate and do not add overlapping
token counters. A quality-critical failure takes precedence over a short time.

For an execution phase, bind installation and checks to the final input state,
then report install, build/unit checks, targeted runtime behavior and policy
attainment separately. Root-only planning still requires workspace
reconciliation; this release does not implement a joint workspace solver.
Record missing evidence as unknown. One run per condition cannot establish a
statistical effect or deterministic migration behavior.
