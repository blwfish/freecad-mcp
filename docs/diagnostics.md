# Diagnostics and Crash Recovery

FreeCAD's error reporting is often cryptic — OCCT kernel failures produce no useful
message, and the Report View output that does exist is easy to miss. This MCP includes
several layers of instrumentation designed to answer "what went wrong and why" rather
than leaving you with a silent failure.

## What's Running When You Use the MCP

Every time you make a tool call, three things happen in parallel:

1. **The bridge** (`freecad_mcp_server.py`) logs the tool name, arguments, duration,
   and result to a structured operation log.
2. **The crash watcher** (inside FreeCAD) writes the current tool call to disk
   *before* it executes — so if FreeCAD crashes mid-operation, the file survives.
3. **FreeCAD's Report View** receives diagnostic output from both FreeCAD itself and
   the AICopilot workbench.

If something goes wrong, you have at least three places to look.

---

## Operation Logs

**Location:** `~/.freecad-mcp/logs/`

**Files:** `freecad_mcp.log` (human-readable) and `operations_YYYYMMDD.json`
(structured, one JSON object per line).

**What they contain:** Every tool call — start, end, duration, success/failure, and a
truncated copy of the result. Useful for understanding what the agent was doing in the
sequence leading up to a failure.

**Sample entry:**
```json
{"timestamp": "2026-04-18T15:23:42.341985", "operation": "COMMAND_START",
 "parameters": {"tool": "execute_python_async",
   "args": {"code": "import FreeCAD\ndocs = []\nfor name, doc in ..."}},
 "duration_seconds": null, "success": true}

{"timestamp": "2026-04-18T15:23:44.117103", "operation": "COMMAND_SUCCESS",
 "parameters": {"tool": "poll_job"},
 "duration_seconds": 0.074, "success": true,
 "result": "{\"status\": \"done\", \"result\": \"[{'name': 'MyPart', ..."}"}
```

Each tool call produces a COMMAND_START and either COMMAND_SUCCESS or COMMAND_FAILURE.
The sequence tells you exactly what the agent tried and in what order.

**How to access from an agent:**

Ask your agent to call `get_debug_logs` — it reads the most recent operation log and
returns it formatted. You can also read the files directly.

---

## Crash Watcher

**Location:** `/tmp/freecad_mcp_last_op.json`

**What it does:** Written by the AICopilot workbench *inside* FreeCAD immediately
before each tool call executes. If FreeCAD crashes during an operation, this file
survives and tells you what was running at the moment of the crash. Cleared
automatically on successful completion.

**Sample content after a crash:**
```json
{
  "tool": "execute_python",
  "args": {"code": "import Part\nresult = shape1.fuse(shape2)\n…"},
  "started_at": 1747123456.789,
  "pid": 12345
}
```

If FreeCAD is not running and this file exists, the last operation did not complete
cleanly. The agent can read it to report exactly what was in flight when the crash
occurred.

---

## Report View

FreeCAD's Report View is the closest thing it has to a console. It shows:

- Workbench load status and errors
- Recompute warnings and failures
- OCCT kernel messages (the ones that aren't completely cryptic)
- AICopilot startup and operation status
- Link restore failures (like the broken external reference in the scenario)

**How to access from an agent:** Call
`view_control(operation="get_report_view", tail=50)` to get the last 50 lines.
Add `filter="error"` to show only error lines. Add `clear=True` to clear it after
reading (useful before retrying an operation so you see only fresh output).

**What normal startup looks like:**

```
10:08:28  Starting FreeCAD AI Copilot Service...
10:08:28  MCP Debug infrastructure loaded
10:08:28    Logs: /Users/you/.freecad-mcp/logs/
10:08:28    Crashes: /Users/you/.freecad-mcp/crashes/
10:08:28  Crash watcher loaded — op tracking active
10:08:28  freecad_mcp_handler v5.5.0 validated
10:08:28  Modular handlers loaded successfully
10:08:28  Socket server started on /tmp/freecad_mcp.sock
10:08:28  AI Socket Server started - Claude ready
10:08:28  FreeCAD AI Copilot ready.
```

If you don't see "AI Socket Server started - Claude ready", the workbench didn't load.
Check that the AICopilot addon is installed in FreeCAD's Mod directory.

---

## Connection Diagnostics (`manage_connection`)

The `manage_connection` tool runs on the bridge side — it works even when FreeCAD is
down or unreachable. Use it for:

- **`action="status"`** — reports connection state, socket health, and whether a
  crash recovery file exists from a previous session.
- **`action="clear_recovery"`** — removes corrupt `.FCStd` recovery files that can
  cause FreeCAD to crash immediately on startup (crash loop). If FreeCAD crashes
  every time you try to open it, this is the first thing to try.
- **`action="validate_fcstd"`** — checks a `.FCStd` file's ZIP integrity before
  opening it. Useful if you suspect a save was interrupted mid-write.

---

## Crash Loops

A crash loop happens when FreeCAD crashes during startup because it tries to restore
a recovery file that was itself written during a crash — so it crashes again
immediately, before you can do anything.

**Symptoms:** FreeCAD opens and closes instantly. The socket file exists but
connections are immediately refused.

**Cause:** MCP operations are fast and programmatic. When FreeCAD crashes
mid-operation, any open documents may have partially-written recovery files. On next
launch, FreeCAD tries to restore them and crashes again.

**Fix:**
1. Ask your agent to call `manage_connection(action="clear_recovery")` — this finds
   and removes corrupt recovery files.
2. Restart FreeCAD.
3. If it still crashes, call `manage_connection(action="status")` and check what the
   bridge reports about the crash history.

---

## Summary: Which Tool for Which Problem

| Symptom | Start here |
|---|---|
| Operation returned an error | `get_report_view(filter="error")` |
| FreeCAD crashed | Read `/tmp/freecad_mcp_last_op.json` via `manage_connection(action="status")` |
| FreeCAD crashes on startup | `manage_connection(action="clear_recovery")` |
| Weird result, no error | `get_debug_logs` — check the operation sequence |
| Can't connect at all | `manage_connection(action="status")` |
| Suspect corrupt .FCStd | `manage_connection(action="validate_fcstd", filename="...")` |
