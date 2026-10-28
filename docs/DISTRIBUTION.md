# Headless distribution

The source checkout pins the Python Core at `upstream/deploom`. A built wheel
contains that Core, the Blueprint schema, the canonical Skill bundle and its
installer. No Desktop installation, Electron runtime, sibling checkout or Git
executable is required to run the installed wheel.

`release-manifest.json` binds the MCP version, staged Core revision and content
inventory, schema bytes and Skill contract. It records the expected release;
runtime provenance also hashes the actual generator and available Core files.
Neither an installed Skill receipt nor an expected release manifest proves
which instructions a client has loaded. Capture the actual loaded content when
evaluating an agent session.

## Build from the pinned source checkout

Use Python 3.12–3.14 and initialize the submodule. Install the package runtime
dependencies and the `build`, `setuptools` and `wheel` build tools in a dedicated
environment. After changing the Core pin or canonical resources:

```bash
python scripts/check_skill.py --write
git add upstream/deploom
python scripts/update_release_manifest.py
python scripts/check_skill.py
python scripts/update_release_manifest.py --check
python -m build --no-isolation
```

The release updater requires the submodule HEAD to match its staged gitlink and
refuses tracked Core edits. Change and validate Core in its owning repository,
then update the pin. It does not bump versions, create tags, publish packages or
change source history.

The build validates the explicit Core inventory and canonical Skill files
before copying them. It excludes Desktop, Git history, tests, caches and
unlisted local files. A source distribution includes the same explicit Core
inventory, so building a wheel from it does not fetch a submodule. Schema or
Skill drift fails the build until the manifest is refreshed deliberately.

## Install and run a locally built wheel

```bash
python -m pip install dist/deploom_mcp-0.2.0-py3-none-any.whl
deploom-mcp
deploom-install-skill --client codex --scope user --verify
```

For an isolated install test, use `--target /absolute/test/skill-dir` instead of
the user scope. The installer does not edit MCP configuration. Planning remains
read-only on the target project and returns `PLANNING_ONLY`, even when the
package and its resources have passed distribution checks.

## Distribution checks

```bash
python -m unittest discover -s tests -v
python scripts/smoke_stdio.py
python -m build --no-isolation
python scripts/smoke_wheel.py dist/deploom_mcp-0.2.0-py3-none-any.whl
```

`smoke_wheel.py` creates a temporary environment outside the checkout. It reuses
already installed runtime dependencies, installs only the supplied wheel, and
checks real bundled Core startup, the shipped schema, read-only planning on an
empty synthetic project and byte-verified Skill installation. It does not
contact a dependency registry or run a migrated application. The regular
production-boundary tests cover nonempty planning separately.

Run the smoke on the wheel rebuilt from the source distribution as well when
changing packaging. Record the exact wheel hash and interpreter/dependency
versions for release evidence. Local build success is not a public package
release and does not establish migration quality or performance.
