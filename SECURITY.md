# Security Policy

## Intended Security Model

freecad-mcp is a local development tool. By design, it grants your AI agent full access to FreeCAD's Python environment — including the filesystem, network, and OS — via `execute_python`. This is documented in the README and is not a vulnerability. The MCP bridge communicates over a Unix domain socket (TCP localhost on Windows) that is not exposed to the network.

**This tool is intended for single-user local use only.** Do not expose it to untrusted networks or users.

## Update Check (Network Access)

`check_freecad_connection` — the first call every session makes — also checks whether a newer release is available. This is the *only* network access the bridge makes on its own initiative, as opposed to `execute_python`, which can do arbitrary network I/O if the agent is asked to.

- **Pull, not push.** A plain `GET` to GitHub's public releases API (`api.github.com/repos/blwfish/freecad-mcp/releases/latest`) — not a custom telemetry endpoint. Nothing is sent beyond the HTTP request itself: no identifying data, no usage metrics.
- **Cached and throttled.** At most one live check per 24 hours. A flaky or unreachable network degrades silently — no update info is shown, and the connection check itself is never blocked or slowed by it.
- **Auditable.** Every live check (not cache hits) is appended to `~/.cache/freecad-mcp/version_check.log` — timestamp, URL, and result — so you can verify exactly what was requested and when, without having to trust this document or the source.

## Indirect Prompt Injection

When an AI agent works with FreeCAD, it reads document content — object labels, macro source code, spreadsheet values, error messages — and that content flows directly into the agent's reasoning context. This creates a structural risk: a crafted FreeCAD file could contain text designed to influence the agent's subsequent actions.

**What this means in practice:** treat FreeCAD files from untrusted sources the same way you'd treat untrusted code. A malicious `.FCStd` document or `.FCMacro` file could, in principle, contain text that causes the agent to take unintended actions — writing files to unexpected paths, executing arbitrary Python, or exfiltrating data — if the agent interprets that text as instructions.

**Why the practical risk here is lower than it might sound:** FreeCAD's user base is primarily makers, hobbyists, and engineers working with their own files or files from known collaborators. The attack requires an adversary who can get a crafted file into your workflow, and the payoff is access to a personal machine running a local CAD tool — not a corporate document management system. This is qualitatively different from CAD environments where files routinely cross organizational trust boundaries.

**Practical mitigations:**

- Open FreeCAD files only from sources you trust, as you would with any file that executes code (Office macros, Jupyter notebooks, etc.)
- Be cautious with FreeCAD macros downloaded from the internet — they execute as Python with full OS access regardless of whether an AI agent is involved
- If you're working with files from untrusted sources, stop the MCP server first, inspect the file manually, then reconnect

This is a known structural limitation of agentic tools that process rich file formats. It is not unique to this project, and there is no clean technical fix that preserves the tool's utility.

## Scope

Security reports are appropriate for issues that allow the tool to be used outside its intended local single-user context — for example:

- The socket server accepting connections from outside the local machine
- A path traversal or injection issue in file import/export that exceeds the expected access
- A dependency with a known CVE that affects this tool's operation

Reports about `execute_python` giving access to the filesystem are **out of scope** — that is the intended behaviour.

## Reporting a Vulnerability

Please use [GitHub's private vulnerability reporting](https://github.com/blwfish/freecad-mcp/security/advisories/new) rather than opening a public issue. This keeps the details private until a fix is available.

Include:
- A clear description of the issue and its impact
- Steps to reproduce
- Your OS, FreeCAD version, and agent platform

This is a one-person project maintained in spare time. I'll acknowledge the report within a week and aim to resolve confirmed issues within 30 days.
