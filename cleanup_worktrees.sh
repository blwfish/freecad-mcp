#!/bin/bash
#
# FreeCAD MCP Worktree Cleanup Script
#
# This script safely removes obsolete worktrees and branches after
# merging valuable changes into main.
#
# Generated: 2025-12-11
# Context: Cleanup after cherry-picking fixes from old branches
#

set -e  # Exit on error

REPO_DIR="/Volumes/Additional Files/development/freecad-mcp"
WORKTREE_BASE="$HOME/.claude-worktrees/freecad-mcp"

echo "========================================="
echo "FreeCAD MCP Worktree Cleanup"
echo "========================================="
echo ""
echo "This will remove the following worktrees and branches:"
echo ""
echo "SAFE TO DELETE (already merged or cherry-picked):"
echo "  - crazy-matsumoto      (merged to main)"
echo "  - lucid-zhukovsky      (merged via ac5a225)"
echo "  - elastic-zhukovsky    (behind main, outdated)"
echo "  - amazing-chaplygin    (cherry-picked cam_ops fix)"
echo ""
echo "DANGEROUS/OBSOLETE (v2.0/v3.3.3 era, superseded):"
echo "  - fix-execute-python-return-value (v2.0 era, cherry-picked)"
echo "  - fix-gui-thread-safety           (v3.4.2, already merged)"
echo "  - eager-boyd                      (v3.0 era, deletes everything)"
echo "  - sharp-herschel                  (v3.3.3, superseded by v4.0)"
echo ""
echo "========================================="
echo ""

read -p "Continue with cleanup? (yes/no): " confirm

if [[ "$confirm" != "yes" ]]; then
    echo "Cleanup cancelled."
    exit 0
fi

cd "$REPO_DIR"

echo ""
echo "Step 1: Removing worktrees..."
echo ""

# Remove worktrees (if they exist)
for worktree in crazy-matsumoto lucid-zhukovsky elastic-zhukovsky amazing-chaplygin eager-boyd sharp-herschel; do
    worktree_path="$WORKTREE_BASE/$worktree"
    if [ -d "$worktree_path" ]; then
        echo "  Removing worktree: $worktree"
        git worktree remove "$worktree_path" --force || echo "    (worktree already removed or locked)"
    else
        echo "  Worktree not found: $worktree (already removed)"
    fi
done

echo ""
echo "Step 2: Pruning worktree metadata..."
git worktree prune

echo ""
echo "Step 3: Deleting local branches..."
echo ""

# Delete local branches
for branch in crazy-matsumoto lucid-zhukovsky elastic-zhukovsky amazing-chaplygin eager-boyd sharp-herschel fix-execute-python-return-value fix-gui-thread-safety; do
    if git show-ref --verify --quiet "refs/heads/$branch"; then
        echo "  Deleting branch: $branch"
        git branch -D "$branch" || echo "    (failed to delete, may have unmerged commits)"
    else
        echo "  Branch not found: $branch (already deleted)"
    fi
done

echo ""
echo "Step 4: Cleaning up remote tracking branches (optional)..."
echo ""
echo "The following remote branches still exist:"
git branch -r | grep -E "(crazy-matsumoto|lucid-zhukovsky|elastic-zhukovsky|amazing-chaplygin|eager-boyd|sharp-herschel|fix-execute-python-return-value|fix-gui-thread-safety)" || echo "  (none found)"
echo ""
read -p "Delete these remote branches? (yes/no): " delete_remote

if [[ "$delete_remote" == "yes" ]]; then
    for branch in crazy-matsumoto lucid-zhukovsky elastic-zhukovsky amazing-chaplygin eager-boyd sharp-herschel fix-execute-python-return-value fix-gui-thread-safety; do
        if git ls-remote --heads origin "$branch" | grep -q "$branch"; then
            echo "  Deleting remote branch: origin/$branch"
            git push origin --delete "$branch" 2>/dev/null || echo "    (branch doesn't exist on remote or already deleted)"
        fi
    done
fi

echo ""
echo "========================================="
echo "Cleanup Summary"
echo "========================================="
echo ""
git worktree list
echo ""
echo "Remaining local branches:"
git branch
echo ""
echo "✅ Cleanup complete!"
echo ""
echo "Next steps:"
echo "  1. Verify main branch has all your work: git log main --oneline -10"
echo "  2. Push main to remote: git push origin main"
echo "  3. Check for any remaining worktrees: git worktree list"
echo ""
