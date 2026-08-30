# Release contract

Releases are source distributions identified by protected, annotated `v<version>` Git tags. Consumers pin those tags; this repository does not publish the package to PyPI.

## Invariants

A release is valid only when all of these are true on the tagged commit:

1. `[project].version` in `pyproject.toml`, installed distribution metadata, and `epicoracle_feedback.__version__` are identical.
2. `uv.lock` is synchronized with `pyproject.toml`.
3. `CHANGELOG.md` contains a `## [v<version>]` section describing all changes since the previous tag.
4. The version is greater than every version previously merged or tagged. Never reuse a skipped or untagged version.
5. The protected annotated tag is exactly `v<version>`, points to the reviewed commit on `main`, and is never moved or deleted.
6. Tag and GitHub Release creation happen only after merge and fresh Captain authorization.

`tests/test_version_contract.py` enforces the first invariant in the required PR gate. The lockfile check and remaining validation commands are authoritative in [`.github/workflows/test.yml`](.github/workflows/test.yml).

## Release-preparation PR

1. Inspect both merged version history and immutable tags before selecting the next SemVer value:

   ```bash
   git log -G '^(version =|__version__ =)' -- pyproject.toml src/epicoracle_feedback/__init__.py
   git tag --sort=-version:refname
   ```

2. Update `pyproject.toml`, `epicoracle_feedback.__version__`, `uv.lock`, and `CHANGELOG.md` in the same PR.
3. Preserve existing public exports and behavior unless the changelog and SemVer bump explicitly account for a contract change.
4. Run the exact local sequence from `.github/workflows/test.yml`, including `uv lock --check` and `uv build --offline --no-build-isolation`.
5. Merge through the required human gate. A release-preparation PR does not create a tag or GitHub Release.

## `v0.2.3` repair evidence

The release history establishes `v0.2.0` as a proven coherent package release: its annotated tag, `pyproject.toml`, and runtime `__version__` all report `0.2.0` at commit `4e7004f5d9c00f841bf5584851133600921ed09a`.

The drift repaired by `v0.2.3` was reproduced before code changes:

- **Trigger:** manual release edits changed `pyproject.toml` without changing runtime `__version__`; the later merged `0.2.2` metadata bump also received no immutable tag or GitHub Release.
- **Masking condition:** the required tests did not compare installed package metadata with runtime `__version__`, and the release workflow only runs after a tag is pushed.
- **User/operator-visible consequence:** installs from `main` reported distribution version `0.2.2` but runtime version `0.2.0`, while operators and consumers could only discover or pin the newest immutable release, `v0.2.1`.

Compatibility inspection of the four current consumers found marketplace, compliance, and satellite-template pinned to `v0.2.0`, and the hub pinned to `v0.2.1`. Every package export they import remains present. Those existing pins are immutable and unaffected; adoption of `v0.2.3` remains an explicit consumer change.

## Post-merge operator step for `v0.2.3`

**Stop until the Captain gives fresh authorization to create the tag/release.** After authorization, run this from a clean substrate checkout. It validates the merged `origin/main` commit before creating the one new immutable tag:

```bash
git fetch --prune --tags origin
git switch --detach origin/main
test -z "$(git status --porcelain)"

uv lock --check
uv sync --python 3.12.13 --locked --extra dev
uv run --offline ruff check .
uv run --offline mypy src
uv run --offline pytest --disable-socket --allow-unix-socket -v
uv build --offline --no-build-isolation

RELEASE_VERSION=0.2.3
RELEASE_TAG="v${RELEASE_VERSION}"
RELEASE_SHA="$(git rev-parse 'origin/main^{commit}')"

test "$(git show "${RELEASE_SHA}:pyproject.toml" | awk -F'"' '/^version = / {print $2; exit}')" = "$RELEASE_VERSION"
test "$(git show "${RELEASE_SHA}:src/epicoracle_feedback/__init__.py" | awk -F'"' '/^__version__ = / {print $2; exit}')" = "$RELEASE_VERSION"
git show "${RELEASE_SHA}:CHANGELOG.md" | grep -F "## [${RELEASE_TAG}]"
if git ls-remote --exit-code --tags origin "refs/tags/${RELEASE_TAG}" >/dev/null 2>&1; then
  echo "Refusing to reuse existing tag ${RELEASE_TAG}" >&2
  exit 1
fi

git show --summary "$RELEASE_SHA"
git tag -a "$RELEASE_TAG" "$RELEASE_SHA" -m "$RELEASE_TAG — release contract repair"
git push origin "refs/tags/${RELEASE_TAG}"
```

The final push is also the release command: [`.github/workflows/release.yml`](.github/workflows/release.yml) creates the GitHub Release from the matching changelog section. Verify it without modifying it:

```bash
gh-axi release view v0.2.3 -R abtex/epicoracle-feedback-substrate
```

If the workflow does not create the release, stop and obtain new authorization before any manual release operation. Never move the tag to repair a failed release.
