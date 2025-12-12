# Git Worktree Quick Reference

**Essential commands for managing Claude Code worktrees**

---

## Quick Status Check

```bash
# See all worktrees
git worktree list

# See all branches with last commit
git branch -vv

# See commit graph
git log --oneline --graph --all -20
```

---

## After a Claude Code Session

### Option 1: Keep the Work (Recommended)

```bash
# 1. Go to main repo
cd /path/to/main/repo

# 2. Switch to main
git checkout main

# 3. Merge the worktree branch
git merge <branch-name>

# 4. Push to remote
git push origin main

# 5. Remove the worktree
git worktree remove ~/.claude-worktrees/repo/<branch-name>

# 6. Delete the local branch
git branch -d <branch-name>

# 7. Clean up worktree metadata
git worktree prune
```

### Option 2: Discard the Work

```bash
# 1. Just delete the worktree directory
rm -rf ~/.claude-worktrees/repo/<branch-name>

# 2. Prune metadata
git worktree prune

# 3. Force-delete the branch
git branch -D <branch-name>
```

---

## Cherry-Picking from Old Branches

```bash
# 1. See what changed in the branch
git diff main..<branch-name> -- <file-path>

# 2. Show the specific commit
git show <commit-hash>

# 3. Cherry-pick the commit
git cherry-pick <commit-hash>

# OR manually apply specific changes
git show <commit-hash> | patch -p1
```

---

## Verifying Merges

```bash
# Check if a branch is merged into main
git merge-base --is-ancestor <branch-name> main && echo "MERGED" || echo "NOT merged"

# See commits in branch not in main
git log main..<branch-name> --oneline

# See commits in main not in branch
git log <branch-name>..main --oneline
```

---

## Comparing Branches

```bash
# Files changed between branches
git diff --name-status main..<branch-name>

# Detailed diff
git diff main..<branch-name>

# Diff specific file
git diff main..<branch-name> -- <file-path>
```

---

## Dry-Run Merge

```bash
# Merge without committing (can abort)
git merge --no-commit --no-ff <branch-name>

# Review the staged changes
git status
git diff --cached

# If you like it:
git commit -m "Merge <branch-name>"

# If you don't:
git merge --abort
```

---

## Remote Branch Management

```bash
# List remote branches
git branch -r

# Delete remote branch
git push origin --delete <branch-name>

# Prune deleted remote branches locally
git fetch --prune
```

---

## Finding Lost Work

```bash
# Show all recent commits (even deleted branches)
git reflog

# Recover a deleted branch
git checkout -b <new-branch-name> <commit-hash-from-reflog>

# Show all unreachable commits
git fsck --lost-found
```

---

## Worktree Troubleshooting

```bash
# Remove a locked worktree
git worktree remove <path> --force

# Repair a corrupted worktree
git worktree repair

# List all worktree locations
git worktree list --porcelain

# Move a worktree to a new location
# (No direct command - must remove and re-add)
git worktree remove <old-path>
git worktree add <new-path> <branch-name>
```

---

## Bulk Cleanup

```bash
# Delete all local branches except main
git branch | grep -v "main" | xargs git branch -D

# Delete all remote-tracking branches that no longer exist
git fetch --prune

# Remove all worktrees (CAREFUL!)
git worktree list --porcelain | grep "worktree" | awk '{print $2}' | xargs -n1 git worktree remove --force
```

---

## Best Practices

### ✅ DO

- Merge or cherry-pick valuable work immediately
- Delete worktrees after merging
- Use descriptive commit messages when merging
- Keep `main` branch clean and up-to-date
- Run `git worktree prune` regularly

### ❌ DON'T

- Leave worktrees around indefinitely
- Merge without reviewing changes
- Delete branches without checking if they're merged
- Force-push to shared branches
- Ignore worktree errors (they indicate real problems)

---

## Claude Code Workflow

```
User starts new Claude Code conversation
    ↓
Claude Code creates worktree: ~/.claude-worktrees/repo/random-name
    ↓
Claude Code creates branch: random-name
    ↓
Work happens in that worktree
    ↓
User says "commit and push"
    ↓
Claude commits to random-name branch
    ↓
Claude pushes to origin/random-name
    ↓
**USER MANUALLY MERGES TO MAIN AND CLEANS UP**
```

---

## Emergency: "I Checked Out a Bad Branch!"

```bash
# If you accidentally checked out a branch that deletes everything:

# 1. STOP - Don't make any commits
# 2. Switch back to main immediately
git checkout main

# 3. Verify main is intact
git status
git log --oneline -5

# 4. Delete the bad branch
git branch -D <bad-branch-name>

# 5. If files were deleted, restore them
git checkout HEAD -- .
```

---

## Pro Tips

1. **Use Aliases:**
   ```bash
   git config --global alias.wt 'worktree'
   git config --global alias.wtl 'worktree list'
   git config --global alias.wtr 'worktree remove'
   ```

2. **Check Before Deleting:**
   ```bash
   # Always check what's in a branch before deleting
   git log <branch-name> --oneline -10
   git diff main..<branch-name> --stat
   ```

3. **Backup Before Cleanup:**
   ```bash
   # Create a backup branch before deleting
   git branch backup/<branch-name> <branch-name>
   git branch -D <branch-name>
   # Keep backup for a week, then delete
   ```

---

**Last Updated:** 2025-12-11
**For:** FreeCAD MCP Project
