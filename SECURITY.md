# Security Policy

## Intended Security Model

freecad-mcp is a local development tool. By design, it grants your AI agent full access to FreeCAD's Python environment — including the filesystem, network, and OS — via `execute_python`. This is documented in the README and is not a vulnerability. The MCP bridge communicates over a Unix domain socket (TCP localhost on Windows) that is not exposed to the network.

**This tool is intended for single-user local use only.** Do not expose it to untrusted networks or users.

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
