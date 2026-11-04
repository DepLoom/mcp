# Targeted npm workspace upgrades

1. Observe the lockfile root with `observe_workspace_inventory_v1`. Reuse its
   `inventoryDigest`. Select exact `{owner, section, name}` declarations, where
   `.` identifies the root. Keep same-name declarations in different owners
   separate. Keep held declarations and their locked versions as constraints.
2. Call `get_upgrade_plan` with `workspace_request.schemaVersion: 2`,
   `goalKind: TARGETED_UPGRADE`, the inventory digest and selected targets.
   Send the approved major lines, supported finite candidate versions and any
   exact target for each selector. Use only frozen, observed candidate metadata
   and a supported provider topology. Missing metadata or topology stays unknown.
3. Omit `preflight` for a pure model. Enable isolated lock-only npm preflight
   only with the required authority and host setup. A rejected candidate
   applies to its recorded inputs. Preserve pinned versions. Stop on missing
   evidence, an exhausted budget or a blocker with no authorized alternative.
4. Read `constraintModel` and `packageManagerValidation` separately. When a
   decision needs details, pass the issued capability, request/candidate IDs
   and snapshot hash to `get_workspace_plan_details` for the selected owner and
   section. Treat redacted external endpoints and partial rule coverage as
   explicit evidence limits. Keep plan capabilities out of shared reports.
5. Review the selected lock before any authorized isolated trial. Record
   baseline, selected, applied and installed version per declaration with its
   owner and evidence. Independently verify install, project checks and
   affected runtime. Preserve unverified gates and deferred changes.

The current route targets explicit dependency scope. A request for whole
monorepo Yellow or Green retains its policy goal even if this route is unavailable.
