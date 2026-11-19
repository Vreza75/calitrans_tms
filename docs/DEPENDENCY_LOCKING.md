# Backend dependency locking

## Why

`requirements.txt` and `requirements-dev.txt` declare version *ranges*
(e.g. `pandas>=2.2,<4`). Ranges are the right format for a human reading
"what this project needs," but they are not reproducible: the exact
resolved version set installed today can differ from the set installed
next week, even with no code change, purely because a dependency
published a new release inside an allowed range. CI (and any fresh
`pip install`) was silently exposed to that drift.

## Strategy

- `requirements.txt` / `requirements-dev.txt` stay exactly as they are
  today - the human-authored source of truth for *what* this project
  depends on and the version floor/ceiling each one tolerates. Nothing
  about this locking setup changes how you edit them.
- `requirements.lock.txt` / `requirements-dev.lock.txt` are **generated**
  files - every direct and transitive dependency pinned to an exact
  version, with `--generate-hashes` sha256 hashes for every distribution
  pip is allowed to install. `requirements-dev.lock.txt` is a strict
  superset of `requirements.lock.txt` (it compiles `requirements-dev.txt`,
  which itself starts with `-r requirements.txt`).
- CI (`.github/workflows/pr-validation.yml`) installs from
  `requirements-dev.lock.txt` with `pip install --require-hashes`, never
  from the plain `.txt` files. `--require-hashes` makes pip refuse to
  install **any** package that doesn't have a matching hash in the lock
  file - a lock file that's missing a transitive dependency, or that
  disagrees with what's actually resolvable, fails the install outright
  instead of silently succeeding with an unpinned/unverified package.
- Never hand-edit a `.lock.txt` file. Regenerate it (see below) and
  commit the regenerated output.

## Regenerating the lock files

The lock files are resolved **inside a Python 3.11 Linux container**,
not on a local machine, so the pinned versions and hashes always match
what the `ubuntu-latest` GitHub Actions runner will actually install
(wheel availability and resolution can differ by OS/Python combination).
Docker is the only requirement to run this - no need to have pip-tools,
or even Python, installed locally.

From the repository root:

```bash
docker run --rm -v "$(pwd):/work" -w /work python:3.11-slim bash -c '
  set -e
  pip install --quiet pip-tools==7.4.1
  pip-compile requirements.txt \
    --output-file=requirements.lock.txt \
    --generate-hashes --allow-unsafe --strip-extras \
    --resolver=backtracking --header
  pip-compile requirements-dev.txt \
    --output-file=requirements-dev.lock.txt \
    --generate-hashes --allow-unsafe --strip-extras \
    --resolver=backtracking --header
'
```

(On Windows with Git Bash, prefix the command with `MSYS_NO_PATHCONV=1`
so the `-v`/`-w` path arguments aren't mangled.)

Run this:

- Whenever `requirements.txt` or `requirements-dev.txt` changes (a
  dependency added, removed, or its range tightened/widened).
- Periodically, to pick up patch/security releases within the existing
  ranges - review the diff before committing, same as any dependency
  bump.

Then:

1. Review the diff - `git diff requirements.lock.txt
   requirements-dev.lock.txt`. Confirm the versions that changed are the
   ones you expected (or, for a periodic refresh, that nothing
   surprising moved).
2. Verify locally before committing:
   ```bash
   python -m venv /tmp/lockcheck && /tmp/lockcheck/bin/pip install --require-hashes -r requirements-dev.lock.txt
   /tmp/lockcheck/bin/pip check
   ```
3. Commit both regenerated `.lock.txt` files together with whatever
   source change prompted the update (or alone, for a periodic refresh).

## `--allow-unsafe`

pip-tools normally excludes `pip`, `setuptools`, and `wheel` from the
lock file by default (historically "unsafe" to pin). `--allow-unsafe`
includes them anyway, with pip-compile's own header documenting that
choice - the goal here is a *complete* graph with nothing resolved
outside the lock, which `--require-hashes` depends on.

## Hash verification limits

`--require-hashes` in CI protects the install step itself: every wheel
or sdist pip downloads is checked against the lock file's recorded
hash before being installed. It does not (and cannot) protect the
*resolution* step that produced the lock file in the first place - that
step trusts PyPI at regeneration time, same as any other dependency
tool. Regenerate on a network you trust, review the diff, and the hash
pin then protects every install from that point on.
