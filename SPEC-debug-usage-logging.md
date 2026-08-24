# Spec: Debug-Only Local Usage Logging for MCP Tool Calls

**Status:** Draft, written 2026-07-30. Not yet reviewed — per this project's Spec Review Rule (CLAUDE.md), needs a fresh-session cold review before implementation begins.

## Goal

Let blw see, locally, how often each MCP tool/operation actually gets used over time — pure curiosity/debugging, not a feature. Must never ship enabled, never appear in the release/Docker/registry path, and must have zero footprint (no I/O, no import cost) when not explicitly turned on.

## Background

- All 37 MCP tools dispatch through one choke point: `@server.call_tool()` → `handle_call_tool(name, arguments)` in `freecad_mcp_server.py:2185-2186`. Verified live by reading the file (not from memory) — this is a real single entry point, not one of several dispatch paths.
- Dispatcher-style tools (`part_operations`, `sketch_operations`, `partdesign_operations`, `assembly_operations`, `cam_operations`, etc.) carry an `operation` key in `arguments` naming the actual sub-action. Non-dispatcher tools (`check_freecad_connection`, `execute_python`, `test_echo`, `restart_freecad`, ...) are themselves the granular unit — no `operation` key.
- The bridge already has one out-of-band signal precedent: the installed `mcp_events` package (`event_context` / `emit_event`, `/Volumes/Files/claude/mcp-events`). It's a per-call `EventAccumulator` for surfacing warn/error events back into the tool's own JSON response — not a persistent cross-call counter, and the wrong abstraction to bend for this; it's mentioned here only to rule it out, not reuse it.
- Existing env var convention in this codebase: `FREECAD_MCP_SOCKET`, `FREECAD_MCP_FREECAD_BIN`, `FREECAD_MCP_MODULE_DIR` — all `FREECAD_MCP_*`, all override-style (unset = default behavior).
- Bridge install path: `~/.freecad-mcp/` — where `freecad_mcp_server.py` + `mcp_bridge_framing.py` get copied per the deploy step in this repo's CLAUDE.md. This is the bridge/MCP-protocol side, distinct from the `AICopilot/` handler side that deploys into FreeCAD's Mod directory.

## Design

### Activation

One env var: `FREECAD_MCP_USAGE_LOG`, whose *value is the log file path*. Unset (the default) means logging is fully off — the code path isn't entered at all, no directory created, no file touched. No separate boolean flag; the path itself is the on/off switch.

### What gets logged

One JSON line appended per tool call:

```json
{"ts": "2026-07-30T14:22:01Z", "tool": "part_operations", "operation": "fuse"}
```

- `ts` — UTC ISO-8601 timestamp of the call.
- `tool` — the MCP tool name (`handle_call_tool`'s `name` param).
- `operation` — `arguments.get("operation")` if present, else `null`.

Explicitly **not** logged: full argument payloads (sketch dimensions, `execute_python` code strings, file paths). This is a usage-frequency counter, not an audit trail — deliberately avoids ever writing model geometry or arbitrary code strings to a persistent local file.

### Hook location

A single guarded call at the very top of `handle_call_tool`, before the `if name == ...` dispatch chain. This guarantees every call is counted exactly once regardless of which branch (or early-return/exception path within a branch) ends up handling it — the log write happens on entry, not on exit.

### Failure handling

Best-effort, never blocking the actual tool call: wrap the append in `try/except`, swallow write failures (disk full, permission error, concurrent-writer race). Emit one stderr line the *first* time a write fails per process (not per-call) so a broken log is discoverable without spamming stderr on every subsequent call.

### Analysis / reporting

Out of scope here. No new MCP tool, no query interface — reading/summarizing the JSONL (`jq`, or a small standalone script that is *not* part of the shipped package) is a separate follow-up task, not part of this spec.

## Explicit non-goals

- Not enabled by default anywhere — not in the repo's own dev setup, Docker image, or MCP registry manifest.
- Not a full audit trail — no argument-payload capture beyond `operation`.
- Not handler-side (`AICopilot/handlers/`) — stays entirely in the bridge process; never touches the handler file's line-count budget.
- No log rotation or retention policy — plain append-only file; rotation is a future manual concern if it ever matters.
- No concurrency hardening for multiple simultaneous bridge processes (e.g. two Claude Code sessions) appending to the same path — small JSONL appends are effectively atomic at the OS level for this use case; not treated as a real risk worth engineering around now.

## Open questions for review

1. Default path the user actually sets `FREECAD_MCP_USAGE_LOG` to (e.g. `~/.freecad-mcp/usage_log.jsonl`) — not hardcoded into the code since unset means off; user picks the path when opting in. Confirm that's the right default to suggest in docs/README, if it gets documented at all.
2. `handle_call_tool` is `async`; the planned append is a plain synchronous `open(...).write()`, which briefly blocks the event loop. For one local interactive user this is very likely fine, but worth a second look during implementation rather than assuming.

## Review status

Needs a fresh-session cold review per this project's Spec Review Rule before implementation starts: verify the external-system assumption above (dispatch shape/line numbers, re-confirmed against the live file 2026-07-30), check for internal contradictions, and check scope gaps against the current repo state at implementation time (line numbers drift). Do not implement in the same session that reviews this spec.
