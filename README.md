# freecad-mcp

[![Unit Tests](https://github.com/blwfish/freecad-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/blwfish/freecad-mcp/actions/workflows/tests.yml)
[![Integration Tests](https://github.com/blwfish/freecad-mcp/actions/workflows/integration-tests.yml/badge.svg)](https://github.com/blwfish/freecad-mcp/actions/workflows/integration-tests.yml)
[![License: LGPL v2.1](https://img.shields.io/badge/License-LGPL_v2.1-blue.svg)](LICENSE)

## What This Does

This is a bridge between your AI agent and [FreeCAD](https://www.freecad.org/) — a thinking partner for parametric design, CNC toolpath generation, and mesh work. You bring the design intent and the domain judgment; the agent handles the FreeCAD mechanics. Together: moving faster, co-designing things that would take hours to build by hand, untangling the cryptic error messages that FreeCAD is so good at generating, and hunting down the subtle, knotty modeling problems that are nearly impossible to find alone. Built for personal daily use — designing parts, generating toolpaths, printing and cutting them — not a demo. See [TOOLS.md](TOOLS.md) for the full tool reference.

## What You Can Work On Together

- **Work through a design** — "I need a mounting plate — 100x60mm, four M3 holes, rounded corners. What's the right approach?" You bring the requirements and constraints; the agent builds, flags underspecified decisions, and iterates with you.
- **Diagnose what went wrong** — "I just tried to pad this sketch and got a weird result — what is FreeCAD actually telling me?" The agent can read the Report View, inspect sketch constraints, and explain what's happening in terms you can act on.
- **Iterate on an existing part** — "This wall feels too thin and the fillet might not survive printing — let's look at it together"
- **Plan CNC toolpaths** — "I want to mill this pocket — let's work out the right tool and parameters for the material"
- **Build something reusable** — "I keep doing this same workflow for every bracket — let's write a macro that generates them from a spreadsheet of dimensions"
- **Check your work before committing** — "Does this model have geometry errors?", "Will these walls survive printing?", "Are any of these parts going to collide?"
- **Work with meshes** — "I have an STL from the manufacturer — let's get it into FreeCAD and add mounting features"
- **Export for manufacturing** — "Export this as STEP and generate G-code for my CNC router"

## See It In Action

These are a couple of examples of how I've used the MCP.

**Debugging a broken external reference** — a link FreeCAD can't restore, an error
message that seems to contradict what you know about your own model, and a one-line fix
found by reading the file directly.

[![Report View showing broken link error](docs/img/extref-02-error.png)](docs/scenario-external-refs.md)

[Full story →](docs/scenario-external-refs.md)

---

**Designing a shingles generator** — several sessions of back-and-forth to design a
parametric generator that tiles any roof surface from a spreadsheet of parameters.

[![Shingles generated across a multi-plane roof](docs/img/shingles-result.png)](docs/scenario-shingles.md)

[Full story →](docs/scenario-shingles.md)

---

## Getting Started

> **FreeCAD version support:** All tools except CAM are supported on FreeCAD 1.1.x (current stable). CAM toolpath generation requires FreeCAD 1.2-dev — the Path workbench API changed incompatibly between 1.1 and 1.2. This project tracks 1.2-dev.

Developed on macOS with Claude Code. The code handles macOS, Windows, and Linux — other platforms *should* work but are less tested. PRs for other agents and platforms will be considered.

Tell your AI agent:

> Go to https://github.com/blwfish/freecad-mcp and read the AGENT-INSTALL.md file. Follow the instructions to install and configure the FreeCAD MCP server on this machine.

Your agent will handle the rest — installing prerequisites, cloning the repo, setting up the FreeCAD addon, and registering itself. Once setup is complete, you can ask your agent to design parts.

### Verifying your installation

Once FreeCAD is running with the AICopilot workbench loaded, open the Report View (menu: View → Panels → Report View). You should see something like this:

![Report View on startup](docs/img/report-view-startup.png)

A few things worth knowing about this output:

- **AI Copilot Service starting** — confirms the AICopilot workbench loaded correctly. If you don't see this, the addon isn't installed or FreeCAD isn't finding it.
- **Debug/crash infrastructure loaded** — active instrumentation is running. If something goes wrong mid-operation, logs and crash reports are captured automatically.
- **Socket server started / Claude ready** — the bridge is listening. This is the line that means your AI agent can connect.
- **Font alias warning** (in orange) — harmless Qt noise that appears on most macOS systems regardless of what you're doing. Not a problem.

### For Developers

```bash
# Unit tests (593 tests, no FreeCAD required)
python3 -m pytest tests/unit/

# Integration tests (91 tests, requires running FreeCAD with AICopilot loaded)
python3 -m pytest tests/integration/

# All tests with coverage
python3 -m pytest --cov=AICopilot
```

#### Prompt Caching and Direct Claude API Calls

**For Claude users: the easiest and cheapest approach is to use Claude Code, the web interface, or the CLI tool directly.** These platforms automatically handle prompt caching and cost optimization — you don't need to think about it. If you're just using this MCP to design parts in FreeCAD, use Claude Code. Stop reading this section.

If you're building applications or integrations that make direct calls to the Claude API (using the Anthropic SDK), **you must understand prompt caching**. The Claude desktop app, web interface, and CLI tools automatically handle caching of file context and tool references — you don't see this optimization, but it reduces latency and cost for repeated queries over the same context.

When you make direct API calls, caching must be managed explicitly. The MCP server itself doesn't make API calls, but if you build integrations or extensions that do:

- **Read the Anthropic SDK documentation** on prompt caching before deploying
- Understand cache TTL (typically 5 minutes) and cost implications (20% of input tokens)
- Be aware that cache keys include model, system prompt, and exact token boundaries — small variations bust the cache
- Consider cache-busting risks if your context is frequently updated (like document state snapshots)

Similarly, if you integrate other MCP servers or agents into your workflow, they may have analogous considerations that are not documented in their README. Check their documentation or source for caching behavior, async job handling, and token limits — don't assume they work like Claude Code.



The test suite covers the handler dispatch layer, base infrastructure, and document operations via unit tests, plus end-to-end coverage of Part, PartDesign, Sketch, Draft, Boolean, Transform, Measurement, and CAM workflows via integration tests against a live FreeCAD instance. CI runs both suites on every push.

#### Built-in Diagnostics

The MCP includes operation logging, crash capture, and report view access. FreeCAD's
own error reporting is often cryptic — OCCT kernel crashes leave no trace, and the
messages that do appear require context to interpret. These tools provide that context.

Most users and developers won't know this infrastructure exists until they need it.
See [docs/diagnostics.md](docs/diagnostics.md) for a full guide with real examples,
and [AGENT-DEBUGGING.md](AGENT-DEBUGGING.md) for the step-by-step investigation
runbook agents should follow when something goes wrong.

See [AGENT-INSTALL.md](AGENT-INSTALL.md) for full technical details, architecture, contributing guidelines, and how to add new tools.

## Security

This tool grants your AI agent full access to FreeCAD's Python environment, including the filesystem and OS. This is by design. See [SECURITY.md](SECURITY.md) for the full security model and how to report vulnerabilities.

## Issues

If you hit a bug, [open an issue](https://github.com/blwfish/freecad-mcp/issues/new?template=bug_report.md) — silent failures don't help anyone. Agents won't tell you when something's wrong; they'll just fail the task. See [CONTRIBUTING.md](CONTRIBUTING.md) for what makes a useful report.

## License

LGPL-2.1-or-later

