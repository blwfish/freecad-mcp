# Contributing

Thanks for taking the time to read this. This is a personal-use project — I built it for my own work and rely on it daily. That shapes how I handle contributions.

## Found a bug?

Please [open an issue](https://github.com/blwfish/freecad-mcp/issues/new). GitHub Discussions are intentionally off — issues are the single feedback channel for this repo.

A useful bug report includes:

- **What you tried to do.** The agent prompt or call sequence, if applicable.
- **What happened instead.** The error message or unexpected output, verbatim.
- **What FreeCAD says.** Often the FreeCAD Report View has more detail than the MCP response — `view_control(operation="get_report_view")` will dump it.
- **Versions:** FreeCAD version (Help → About), OS, agent (Claude Code, Cursor, etc.).

Don't worry about being exhaustive. *"Tried to pad a sketch and got a wire diagnosis instead of a pad"* is enough to start.

## Sending a PR

Welcome, but a couple of preflight things:

1. **Open an issue first** if it's a non-trivial change. Saves both of us from wasted effort if I disagree on direction.
2. **Run the tests** before pushing:
   ```bash
   python3 -m pytest tests/unit/   # 850+ tests, no FreeCAD required
   python3 -m pytest tests/integration/   # requires FreeCAD with AICopilot loaded
   ```
3. **Match the commit style.** `git log --oneline` shows it. Lowercase prefix (`fix:`, `feat:`, `test:`, `docs:`), short imperative summary, body explains the *why*.
4. **One concern per PR.** Bug fix and unrelated cleanup go in separate PRs.

## Will feature requests be accepted?

Maybe. The project is scoped to what I personally need. If your request overlaps with my workflow (parametric part design, CAM for hobby CNC, mesh-to-solid, model railroad fabrication), it's likely. If it's deeply outside (FEM, advanced TechDraw, assembly constraints beyond what I use), I'll probably point you at a fork instead.

## Maintainer

One person, in their spare time. Response times are not guaranteed. A polite ping after two silent weeks is fine.
