# Example: resolve the reason before retrying

These are synthetic situations. They describe decisions, not successful runs.

## Root proposal conflicts with a child

A root plan proposes renderer 19; a child package requires renderer 18. The compact
projection has a truncated-search row and the full artifact has the peer blocker.
Read that evidence before asking whether renderer 19 is acceptable. Reconcile the
whole graph; keep 18 or propose a coordinated remedy. Advisory mode cannot erase
the peer constraint. Pinned mode cannot require a known-incompatible install.
Independent updates may continue if their graph and authority are established.

## Candidate cap, not timeout

A targetless package has candidate coverage 3/29 with `draft_max_candidates=3`.
Another call with the same inputs/cap and a 600-second deadline does not expand
that coverage. Follow the targeted recovery: change the candidate budget within
supported bounds or obtain the missing evidence. A true deadline interruption
can justify one larger-time-budget retry; these are different causes.

## Authorized manual fallback

The user delegated stable same-major alternatives. A row remains
CANDIDATE_SEARCH_TRUNCATED, but registry/peer/API evidence supports a concrete
same-line proposal. In advisory mode, record it as `targetOrigin=manual` with the
existing authority reference and remaining uncertainty. Apply only after the
relevant graph gates pass; verify the final state. Do not re-ask for permission
already granted or invent a solver/security verdict. If the user pinned targets,
prepare the proposed revision and obtain the missing revised-target decision.

## Changed source or policy

A policy discrepancy, hash mismatch, unaccounted manifest change or exclusion
violation blocks the affected execution. Correct the input/contract and re-plan;
manual fallback does not bypass these conditions. Do not fabricate a full-project
PASS if an independent subset completes while other work remains blocked.
