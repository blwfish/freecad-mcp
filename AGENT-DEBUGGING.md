# FreeCAD MCP — Agent Debugging Guide

This file is for you, the AI agent. When something goes wrong with a FreeCAD MCP
operation, follow the relevant section below. Don't guess — the diagnostic tools
exist precisely so you don't have to.

## Your Debugging Toolkit

This MCP exposes diagnostic infrastructure that most FreeCAD users don't know exists
and that most other MCPs don't provide. Use these tools first, before trying to reason
about what might have gone wrong.

**`get_debug_logs()`**
Reads the structured operation log at `~/.freecad-mcp/logs/`. Every tool call is
recorded — tool name, arguments, duration, success/failure, and a snapshot of the
result. When something goes wrong, this tells you the exact sequence of operations
that led there, not just the one that failed.

**`view_control(operation="get_report_view", tail=N, filter="...")`**
Reads FreeCAD's Report View — the panel where FreeCAD logs recompute warnings, OCCT
messages, link restore failures, and workbench output. This is often where the real
error is, even when the tool call error message is vague or missing. Use
`filter="error"` to show only error lines, or `filter="warning"` for warnings.
Use `clear=True` before retrying an operation so you see only fresh output.

**`manage_connection(action="status")`**
Runs on the bridge side — works even when FreeCAD is down. Reports connection state,
socket health, whether the crash watcher left a last-op file, and whether corrupt
`.FCStd` recovery files are present. This is the first tool to call when FreeCAD
crashes or won't connect.

**`manage_connection(action="clear_recovery")`**
Removes corrupt FreeCAD recovery files. FreeCAD writes recovery files continuously
during a session; if it crashes mid-write, the recovery file is corrupt. On next
launch, FreeCAD tries to restore it, crashes again, and you're in a crash loop.
This clears the loop.

**`manage_connection(action="validate_fcstd", filename="...")`**
Checks a `.FCStd` file's ZIP integrity before opening it. If a save was interrupted
mid-write, the file may be structurally intact but contain bad data. This tells you
before FreeCAD tries to open it.

**Crash watcher — `/tmp/freecad_mcp_last_op.json`**
Written by the AICopilot workbench *inside* FreeCAD before every operation, cleared
on success. If FreeCAD crashes, this file survives and records exactly what was in
flight — tool name, arguments, timestamp, PID. `manage_connection(action="status")`
reads and reports it automatically.

---

**Always check connection first:**
```
check_freecad_connection()
```
If this fails, FreeCAD is not running or the AICopilot workbench isn't loaded. Tell
the user to launch FreeCAD and wait for "AI Copilot ready" in the Report View before
retrying.

---

## 1. Operation Returned an Error

**Symptom:** A tool call returned an error message or unexpected result.

**Step 1 — Read the Report View:**
```
view_control(operation="get_report_view", tail=30, filter="error")
```
FreeCAD often logs the real reason for a failure here even when the tool call error
message is vague. Look for OCCT messages, recompute failures, or constraint conflicts.

**Step 2 — Check operation logs:**
```
get_debug_logs()
```
This shows the recent operation sequence. Check whether a previous operation set up
bad state that caused this one to fail.

**Step 3 — Inspect the document state:**
```
view_control(operation="list_objects")
```
Verify the document is in the state you expect. Objects may be missing, hidden, or
in an error state from a prior failed operation.

**Step 4 — If it's a geometry failure:**

Boolean operations (fuse, cut, common) and PartDesign features (pad, pocket, fillet)
can fail silently or with OCCT errors. Try:
```python
execute_python("FreeCAD.ActiveDocument.recompute(); 
    [(o.Name, o.State) for o in FreeCAD.ActiveDocument.Objects]")
```
Objects in state `['Invalid']` or `['Error']` need attention. Inspect them with
`measurement_operations(operation="check_solid", object_name="...")` to find geometry
problems.

---

## 2. FreeCAD Crashed / Disconnected

**Symptom:** A tool call returned a crash/disconnect error. The connection is lost.

**Step 1 — Find out what was running:**
```
manage_connection(action="status")
```
This reads `/tmp/freecad_mcp_last_op.json` (written by the crash watcher inside
FreeCAD before every operation) and reports what tool was in flight when the crash
occurred. Report this to the user.

**Step 2 — Tell the user to restart FreeCAD.** Wait for them to confirm it's running
before proceeding.

**Step 3 — Check for crash loops:**

If FreeCAD crashes again immediately on startup, there are corrupt recovery files.
Run:
```
manage_connection(action="clear_recovery")
```
Then ask the user to restart FreeCAD again.

**Step 4 — After reconnection, check document state:**
```
check_freecad_connection()
view_control(operation="list_objects")
```
The document may or may not have been restored from a recovery file. Verify what's
open before continuing.

**Step 5 — Avoid repeating the crash:**

If the crash happened during an `execute_python` call, the code likely triggered an
OCCT kernel error (boolean operations on invalid geometry are a common cause). Break
the operation into smaller steps and validate geometry between them.

---

## 3. FreeCAD Won't Connect at All

**Symptom:** `check_freecad_connection()` fails. FreeCAD appears to be running but
the socket isn't answering.

**Step 1:**
```
manage_connection(action="status")
```
This diagnoses the socket state without needing FreeCAD to be responsive.

**Step 2 — Check the socket file:**

The socket lives at `/tmp/freecad_mcp.sock`. If the file exists but connections are
refused, FreeCAD crashed and left the socket file behind. The user needs to quit and
restart FreeCAD.

**Step 3 — Check the Report View after restart:**

Ask the user to open View → Panels → Report View after restarting FreeCAD and confirm
they see "AI Socket Server started - Claude ready". If they don't, the AICopilot
workbench isn't loading — check that it's installed in FreeCAD's Mod directory
(see AGENT-INSTALL.md).

---

## 4. Operation Succeeded But Result Looks Wrong

**Symptom:** No error, but the model doesn't look right — geometry is in the wrong
place, features are missing, dimensions seem off.

**Step 1 — Take a screenshot:**
```
view_control(operation="screenshot")
```
Look at the actual state. Don't assume the model is in the state you expect.

**Step 2 — List objects and check visibility:**
```
view_control(operation="list_objects")
```
Check that the objects you expect exist and are visible.

**Step 3 — Measure what's there:**
```
measurement_operations(operation="get_bounding_box", object_name="...")
measurement_operations(operation="list_faces", object_name="...")
```
Verify dimensions against what was intended.

**Step 4 — Undo and retry if needed:**
```
view_control(operation="undo")
```
FreeCAD's undo is available. Use it rather than trying to patch a bad state forward.

---

## 5. Sketch or Constraint Failure

**Symptom:** A sketch operation fails or produces unexpected geometry.

**Step 1 — Check sketch validity:**
```
sketch_operations(operation="verify_sketch", sketch_name="...")
```

**Step 2 — List constraints:**
```
sketch_operations(operation="list_constraints", sketch_name="...")
```
Look for redundant or conflicting constraints. A fully-constrained sketch shows zero
degrees of freedom; over-constrained sketches fail to solve.

**Step 3 — Check the Report View:**
```
view_control(operation="get_report_view", tail=20, filter="sketch")
```
FreeCAD logs sketch solver details here when solving fails.

---

## 6. External Link / Reference Broken

**Symptom:** On document open, the Report View shows "Link not restored" or
"Link broken!" for an external reference.

**Step 1 — Identify the broken link:**
```python
execute_python("""
doc = FreeCAD.ActiveDocument
links = [o for o in doc.Objects if o.TypeId == 'App::Link']
[(l.Name, l.Label, l.LinkedObject) for l in links]
""")
```
Any link where `LinkedObject` is `None` is broken.

**Step 2 — Check what's in the external file:**
```python
execute_python("""
import zipfile, re
# Replace with actual path from link's source document
with zipfile.ZipFile('/path/to/external.FCStd') as z:
    xml = z.read('Document.xml').decode()
names = re.findall(r'<Object name="([^"]+)"', xml)
names
""")
```
This lists what objects actually exist in the file, without needing to load it.
Compare against what the link expects.

**Step 3 — Repair the link:**

If the target object was renamed, redirect the link:
```python
execute_python("""
lnk = FreeCAD.ActiveDocument.getObject('Link002')
ext_doc = FreeCAD.getDocument('external_doc_name')
if not ext_doc.Objects:
    ext_doc.restore()   # force full load of partial document
lnk.LinkedObject = ext_doc.getObject('NewObjectName')
FreeCAD.ActiveDocument.recompute()
""")
```

---

## General Principles

- **Read before guessing.** Use `get_report_view`, `get_debug_logs`, and
  `list_objects` before forming a hypothesis about what went wrong.
- **Don't patch bad state forward.** Use `undo` and retry rather than trying to
  fix a malformed object in place.
- **Validate geometry after boolean operations.** Use
  `measurement_operations(operation="check_solid")` after fuse/cut/common to confirm
  the result is valid before building further features on it.
- **Break large `execute_python` calls into smaller ones.** Long operations that
  crash take the whole document with them. Checkpoint with
  `view_control(operation="checkpoint")` before risky operations.
- **Report crash details to the user.** If FreeCAD crashed, tell the user what
  `manage_connection(action="status")` shows — specifically what operation was in
  flight. That's the information needed to file a useful bug report.
