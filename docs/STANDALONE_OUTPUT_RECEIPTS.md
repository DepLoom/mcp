# Standalone CLI output receipts through MCP

This opt-in route joins the Core standalone generated-output observation to the
existing host-authorized exact private/final route. It is not a new MCP tool,
solver, execution grant or a claim that every npm CLI is supported.

The operator selects `DEPLOOM_SOURCE_SCOPE_MODE=standalone-js-cli-output-v1`
and binds `standaloneOutputSpec` in the hashed host policy. The recipe contains
Core's exact build/test output grammar and source-script hashes. It requires V2
explicit project execution environment, the frozen exact materialization route,
and the original ordered `npm test`, `npm run test:perf` commands. It cannot mix
with the independent TAP/NYC external-input or tool-cache profiles. Missing or
explicit-null policy, a source profile mismatch and recipe drift fail closed.

The existing verifier adapter supplies the specification to the actual Core
workspace verifier. Private and final readers additionally join output source,
lock, dependency plan, command, invocation, environment, toolchain and profile
identities. A receipt without the full pre-existing Core source/tree/command and
process-closure observations cannot establish acceptance.

The final checkpoint includes `generatedOutputObservationDigest`. Its separate
terminal owner and live observation must agree on this value. Current checks
physically enumerate and hash all declared generated outputs again, using the
same non-renewable proof budget; a late successful read cannot return current.
Deleting, replacing, adding or changing a build/TAP output invalidates current.
A later command run needs fresh output roots and a new receipt, not reuse of the
old checkpoint's test artifacts. The generated output trace gate is diagnostic.

Default profiles and their checkpoint identities remain unchanged. The default
Core source guard still includes dist/TAP paths. This policy separates authored
inputs from outputs only with the mandatory typed receipt and original tests.
The source snapshot intentionally does not contain those outputs; current proof
observes their separately bound physical identities. No atomicity against an
arbitrary hostile writer or per-file writer attestation is claimed.

Focused validation includes installed-wheel adapter tests with a real native
Node/npm fixture and a real stdio exact-plan smoke. Synthetic fixture TAP output
is not a real project's correctness evidence. The dotenv exact-update acceptance
is a separate stage; parser benchmark ratios are not MCP speedup measurements.


Private delta transport and final dependency staging pass an explicit Core
input-only materialization contract from the validated host spec/commands. This
is necessary before the fresh output verifier can run; it does not waive its
receipt requirements or admit existing dist/.tap trees. Both source-copy owners
retain the default generic path for all other profiles. The actual project child
environment uses the fixed OS/path/home/temp allowlist; MCP, loader, credential
and unknown host variables are not forwarded. Source transport itself never
produces a project-check observation or checkpoint authority.
