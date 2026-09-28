#!/usr/bin/env bash
# Run this immediately after merging a release PR (dev -> main) with
# `gh pr merge --rebase`.
#
# Why this is needed: GitHub's "Rebase and merge" replays dev's commits onto
# main as brand-new commit objects (same tree content, different hashes/
# parents) but never touches dev itself. dev's branch pointer keeps pointing
# at the pre-rebase commits, so main and dev instantly diverge in git's
# history graph even though their content is identical -- the next release
# PR then shows spurious merge conflicts (a real 3-way merge sees both sides
# as having independently changed the same lines from a stale common
# ancestor). Recurred at v6.0.0, v7.0.0, and v8.2.2 before this script
# existed; each time required manually diagnosing "is this a real conflict
# or just this?" from scratch.
#
# This script only ever fast-forwards dev to match main's content -- it
# refuses to run if their trees differ, since that would mean dev has real
# work main doesn't have yet, and blindly resetting would discard it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "Fetching origin..."
git fetch origin --quiet

main_tree=$(git rev-parse origin/main^{tree})
dev_tree=$(git rev-parse origin/dev^{tree})
dev_sha=$(git rev-parse origin/dev)
main_sha=$(git rev-parse origin/main)

if [ "$main_sha" = "$dev_sha" ]; then
    echo "origin/dev already == origin/main ($main_sha). Nothing to do."
    exit 0
fi

if [ "$main_tree" != "$dev_tree" ]; then
    echo "ERROR: origin/main and origin/dev have DIFFERENT content (tree" >&2
    echo "$main_tree vs $dev_tree) -- this is not the post-release-merge" >&2
    echo "divergence this script exists to fix. dev likely has real work" >&2
    echo "main doesn't have yet. Refusing to force-push; resolve manually." >&2
    exit 1
fi

echo "origin/main and origin/dev have identical content ($main_tree) but"
echo "different commit hashes -- the expected post-rebase-merge divergence."
echo "Fast-forwarding dev's branch pointer to main: $dev_sha -> $main_sha"

git push --force-with-lease="dev:${dev_sha}" origin main:dev

echo "Done. Run 'git fetch && git checkout dev && git reset --hard origin/dev' locally."
