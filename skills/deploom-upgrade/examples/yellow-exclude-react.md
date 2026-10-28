# Example: execute with an exclusion

**User:** "Update the project to Yellow, keeping React unchanged. Choose
compatible stable versions in the current major lines and make the source fixes
needed. Use this isolated checkout and run its checks."

1. Record `EXECUTE`, `advisory`, the agreed policy objective, approved current
   major lines and exclusion React. The request already authorizes ordinary
   compatibility repairs and checks. Reuse the supplied checkout.
2. Plan with full workspace context. Read blockers, unknowns and coverage before
   choosing targets; preserve the user exclusion. Record exact decisions and
   identify any manual alternatives separately.
3. Apply permitted declarations and justified source/config adaptations. Update
   the lockfile. Confirm actual installed versions in the relevant workspaces.
4. Perform a clean/frozen install on final manifests/lock/environment, then the
   accepted project and affected-runtime checks. If a later target correction
   changes the lockfile, retain the old install result and repeat the required
   clean install for the new inputs.
5. A compiler error requiring an annotation in an untouched file is within the
   authorized migration; inspect it rather than inventing a fixed file allowlist.
6. Audit the final installed policy criterion. If that audit is unavailable,
   report successful observed checks but policy NOT_VERIFIED and outcome PARTIAL.
   Do not turn matching exclusions or a successful build into Yellow PASS.
7. Record unique updated names separately from declaration changes and removals.
   Present the diff and separate install/check/runtime/policy results. Publishing
   the result is a different action unless already authorized.

This example does not assert any real package's latest version or measured
runtime result; those must come from the project's actual evidence.
