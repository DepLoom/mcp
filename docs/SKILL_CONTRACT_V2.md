# Skill contract 2.0.0: delivery and verification

The canonical workflow lives in `skills/deploom-upgrade/SKILL.md`, with details
loaded from references only when needed. This revision does not claim a new
agent benchmark or a real project migration. The Stage 3 pilot remains evidence
for its original revision.

## Behavioral changes

- Establish the user's action, objective, target authority and allowed scope.
  `advisory` allows evidence-backed alternatives within already delegated
  authority; `pinned` preserves agreed targets. Neither overrides hard
  constraints, exclusions or explicit user requirements. A review flag calls
  for a decision, not an automatic repeat permission question.
- Separate full decision evidence from displayed row count. Missing reasons,
  coverage or blockers require the full artifact even if rows are untruncated.
  Sufficient compact evidence needs no mechanical full-artifact read. A root
  plan does not certify workspace-wide compatibility.
- A bounded manual proposal preserves the planner's unresolved verdict and its
  own origin. Candidate caps and deadlines need different recovery. Repeating
  an unchanged request does not strengthen the evidence.
- Source repairs follow the actual authorized scope, including necessary
  untouched files. Runtime checks target impacted behavior: route hierarchy,
  redirect history, parameter guards and shared Storybook decorators where
  relevant. Warning attribution needs a source or retained baseline.
- Installation, project checks, runtime, policy and session completion are
  separate. A later lock resync cannot inherit an earlier clean install. An
  incremental install cannot stand in for a required clean/frozen install.

## Canonical identity and portable install

`contract.json` is generated from the version marker in SKILL.md and the complete
canonical tree. `files` maps relative POSIX paths to file SHA-256 values;
`bundleSha256` hashes the UTF-8 bytes of sorted, compact JSON for that map. The
manifest itself, installation receipt and Python bytecode caches are excluded
from self-hashing. Changes to a reference or bundled validator change bundle
identity even when the main Skill file stays identical.

```sh
python scripts/check_skill.py
# After an intentional canonical content/version change:
python scripts/check_skill.py --write
```

The installer verifies the source manifest, compares every canonical target file
and writes `.deploom-skill-install.json`. The receipt records version, bundle and
file hashes, source path/Git revision when available, and installation target.
It says `loadedVerification=NOT_OBSERVED`: neither a source hash nor an installed
receipt proves that a client reloaded the Skill. Existing sessions retain their
old loaded identity until actual client evidence says otherwise.
When testing a rule in a reference, record that reference's loaded content/hash
as well; an identical main file cannot attest consumption of a changed reference.

An existing differing or incomplete tree requires `--force`; this repairs
references even if SKILL.md is identical. Foreign extra files are preserved and
reported as a nonidentical tree. The installer refuses symlink destinations and
source/target overlap. `--dry-run --verify` checks source integrity and prints
the plan without claiming an installation. Client configuration remains a
separate action. The packaged installer uses the same helpers and canonical
tree; it does not require Desktop or a Git executable.

## Minimal acceptance and evidence validation

For `PLAN_ONLY`, a concise decision summary is enough unless a structured record
was requested; Core execution instructions do not authorize migration or project
delivery files for a planning task. For `EXECUTE` or evaluation, use one
decision/acceptance JSON index linking existing logs and applicable user/Core
execution deliverables. Required developer guides and migration reports remain
required; reference them without duplicating their text in the evidence record.
Index paths, requirement references and observed delivery status separately.
Define checks required by the task and affected behavior. Do not turn the
example check list into a universal mandate.

From the installed Skill directory:

```sh
python scripts/validate_evidence.py /absolute/path/to/deploom-evidence.json
```

The helper checks versioned record structure, decision authority references,
manual/planner attribution, unique-name/declaration counts, actual exit/log/time
fields and final input hash consistency. It rejects a final PASS based on an
earlier lock state, a later failed check, an absent accepted runtime criterion
or incomplete policy scope. An install check records clean/frozen/incremental
mode, and only the first two can satisfy final-install acceptance.

This is a consistency validator, not a command runner or independent verifier.
Supplying invented logs or hashes would still be a false claim. Preserve real
command records and the covered inputs. Historical passes stay in the record
without being promoted to final-state success. Waived/not-run checks stay honest.

The local synthetic tests cover those claim boundaries and source/target drift:

```sh
python -m unittest discover -s tests -p 'test_skill_*.py' -v
```

These tests require no registry, model, project migration or global client Skill
installation. They do not verify Router or Storybook behavior in an application;
the Skill instructs the agent to perform the minimal relevant real checks when
execution is authorized.

This contract does not eliminate the cost of required guide/report preparation.
An experiment should measure that delivery work separately and compare arms
with the same acceptance/delivery requirements before attributing overhead to MCP.

## Blinded evaluation protocol

Freeze task, acceptable targets/lines, source baseline, registry evidence,
environment, model settings and required checks before assigning an arm. Distinct
objectives are distinct comparisons: equal fixed targets can isolate execution
cost; autonomous target choice also measures a different search/decision task.

Provide each arm only its own slot plus shared frozen inputs. Do not let an agent
inspect another arm's manifests, resolved versions, reports, logs or usage while
deciding or repairing its run. An evaluator can join results after the original
cutoffs. Record a boundary breach as a protocol limitation; do not silently
replace the original result with a later contaminated repair.

Record actual loaded Skill text/hash, MCP/Core identities and original session
cutoff before comparing rules. Keep operator waits, command intervals and
unknown time separate; union overlapping intervals. Count unique message usage
once and show cache read separately. Do not compute execution speedup for
different target sets or acceptance criteria. A small diagnostic fixture can
decide whether to proceed to a more expensive trial; it cannot establish a
statistical speed effect or agent determinism.
