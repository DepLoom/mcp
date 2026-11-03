# Windows NTFS path spelling and object identity

This boundary supports ordinary absolute local drive paths on NTFS. It does
not add UNC/SMB support, permissions, filesystem settings, or migration authority.

Read-only artifact containment inspects both original path spellings and every
raw ancestor before resolving containment. Reparse points and unreadable paths
are rejected; not-yet-created artifact tails may remain absent. Short/long name
aliases do not require lexical ancestor equality. This stat-based containment
check is not an atomic mutation lease or verifier proof.

Native file delivery retains its existing handles and parent leases. Ordinary
long-name opens keep their prior path check. If only the spelling differs, the
alias branch requires all of:

- no reparse point in any raw component;
- native long-name expansion matching the opened handle's normalized name;
- a second read-only handle with the same volume and file ID;
- the existing file type and single-hardlink requirements.

Conversion alone grants nothing. An unreadable mapping, differing identity,
reparse point, conflicting writer or expired deadline still fails closed. The
second read-only observation does not relax the first handle's deny-write/delete
sharing mode. Existing object-ID and parent-identity checks remain authoritative
for apply/recovery; equal content on a foreign object is insufficient.

A real 8.3 alias may be unavailable on a volume. Tests must report that absence
rather than manufacture alias coverage. Canonical Temp admission in the test
runner provides repeatability, and is separate from these runtime alias checks.
