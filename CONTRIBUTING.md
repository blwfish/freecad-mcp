# Contributing

Thanks for taking the time to read this. This is a personal-use project — I built it for my own work and rely on it daily. That shapes how I handle contributions.

## Found a bug?

Please [open an issue](https://github.com/blwfish/freecad-mcp/issues/new?template=bug_report.md). GitHub Discussions are intentionally off — issues are the single feedback channel for this repo. The bug report template will prompt you for the right information:

- **What you tried to do.** The agent prompt or call sequence, if applicable.
- **What happened instead.** The error message or unexpected output, verbatim.
- **What FreeCAD says.** Often the FreeCAD Report View has more detail than the MCP response — `view_control(operation="get_report_view")` will dump it.
- **Versions:** FreeCAD version (Help → About), OS, agent (Claude Code, Cursor, etc.).

Don't worry about being exhaustive. *"Tried to pad a sketch and got a wire diagnosis instead of a pad"* is enough to start.

## Platform and version coverage

**macOS** is the primary development platform. Every change is developed and manually tested here.

**Linux** is covered by CI — the integration test suite runs against both FreeCAD 1.1-stable and 1.2-dev on Ubuntu on every push. Bugs that only reproduce on Linux are real bugs.

**Windows** has no coverage. The code has conditional handling for Windows paths and sockets, but it is untested. Windows PRs will be considered, but held to a higher bar than macOS/Linux ones: the fix must be clearly scoped and must not break anything on tested platforms. I can't reproduce Windows issues myself or maintain Windows-specific behaviour over time, so a PR that introduces Windows complexity without strong justification is likely to be declined.

**FreeCAD 1.1.x** (current stable) is supported for all tools except CAM. **FreeCAD 1.2-dev** is required for CAM toolpath generation — the Path workbench API changed incompatibly between 1.1 and 1.2. This is a hard line, not a gap waiting to be filled. Please don't send PRs that backport CAM support to 1.1.

CAM improvements for 1.2-dev are welcome, as long as they track FreeCAD upstream — meaning they work with the current Path API as it evolves, not around it.

## Sending a PR

Welcome, but a couple of preflight things. The [PR template](/.github/PULL_REQUEST_TEMPLATE.md) has a checklist — here's the reasoning behind it:

1. **Open an issue first** if it's a non-trivial change. Saves both of us from wasted effort if I disagree on direction.
2. **Run the tests** before pushing:
   ```bash
   python3 -m pytest tests/unit/        # no FreeCAD required
   python3 -m pytest tests/integration/ # requires FreeCAD with AICopilot loaded
   ```
3. **Match the commit style.** `git log --oneline` shows it. Conventional commits: lowercase prefix (`fix:`, `feat:`, `test:`, `docs:`), short imperative summary, body explains the *why*.
4. **One concern per PR.** Bug fix and unrelated cleanup go in separate PRs.

PRs should include tests. For changes to platforms or FreeCAD versions covered by CI, tests are expected. For Windows, tests aren't just good practice — they're the minimum bar for the PR to be reviewable at all, since I have no other way to verify the fix holds or doesn't regress. If you're using this project, you already have a capable test-writer at hand.

PRs most likely to be accepted:

- Bug fixes reproducible on macOS or Linux
- Compatibility fixes for FreeCAD 1.1.x (non-CAM tools)
- CAM improvements for FreeCAD 1.2-dev that track upstream
- New tools that fit the stated workflow scope (see below)
- Windows fixes that are clearly scoped, don't affect tested platforms, and include tests

PRs that will be declined:

- CAM support for FreeCAD 1.1.x — won't happen by design
- Features outside the project scope — consider a fork instead

## Will feature requests be accepted?

Maybe. The project is scoped to what I personally need. If your request overlaps with my workflow (parametric part design, CAM for hobby CNC, mesh-to-solid, model railroad fabrication), it's likely. If it's deeply outside that (FEM, advanced TechDraw, assembly constraints beyond what I use), I'll probably point you at a fork instead.

## Maintainer

One person, in their spare time. Response times are not guaranteed. A polite ping after two silent weeks is fine.
