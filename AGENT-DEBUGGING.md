# FreeCAD MCP — Agent Debugging Guide

This file is for you, the AI agent. When something goes wrong with a FreeCAD MCP
operation, follow the relevant section below. Don't guess — the diagnostic tools
exist precisely so you don't have to. Read the output carefully; the tools are
designed to give you specific, actionable information rather than generic status.

---

## Your Debugging Toolkit

This MCP exposes diagnostic infrastructure that most FreeCAD users don't know exists
and that most other MCPs don't provide. These tools are your primary resource when
something goes wrong. Use them before forming hypotheses.

### `get_debug_logs()`

Reads the structured operation log at `~/.freecad-mcp/logs/`. Every tool call is
recorded as a pair of JSONL entries — COMMAND_START (before execution) and either
COMMAND_SUCCESS or COMMAND_FAILURE (after). Each entry includes the tool name,
arguments, duration, and a snapshot of the result or error.

**What to look for:**
- The sequence of operations leading to the failure, not just the failing call itself.
  Often the problem was set up by a previous operation that succeeded but left bad state.
- Duration anomalies — an operation that normally takes 0.1s taking 30s usually means
  FreeCAD was hung waiting on something (GIL contention, OCCT blocked).
- COMMAND_FAILURE entries earlier in the sequence that may have been silently ignored.

**Sample output:**
```
{"timestamp": "2026-04-18T15:23:42.3", "operation": "COMMAND_START",
 "parameters": {"tool": "execute_python_async", "args": {"code": "..."}}}
{"timestamp": "2026-04-18T15:23:44.1", "operation": "COMMAND_SUCCESS",
 "parameters": {"tool": "poll_job"}, "duration_seconds": 0.074,
 "result": "{\"status\": \"done\", \"result\": \"...\"}"}
```

### `view_control(operation="get_report_view", tail=N, filter="...", clear=False)`

Reads FreeCAD's Report View — the panel where FreeCAD logs recompute warnings, OCCT
messages, link restore failures, sketch solver output, and AICopilot workbench status.
This is frequently where the real error is, even when the tool call error message is
vague or generic.

**Parameters:**
- `tail=50` — return the last N lines (default 50; increase if the failure happened
  a while ago and the log has scrolled)
- `filter="error"` — return only lines containing "error" (case-insensitive). Also
  useful: `filter="warning"`, `filter="link"`, `filter="sketch"`, `filter="recompute"`
- `clear=True` — clear the Report View after reading. Do this before retrying a
  failed operation so the next read shows only fresh output.

**What to look for:**
- `Link not restored` / `Link broken!` — an external reference couldn't be resolved.
  The message includes the object name and file path. See section 6.
- `BRep_API: command not done` — an OCCT boolean operation failed. The geometry fed
  into it was probably invalid. Check `check_solid` on the input objects.
- `Recomputation required` — a document needs recompute after a parameter change.
  Call `execute_python("FreeCAD.ActiveDocument.recompute()")`.
- `Sketcher: sketch has ... redundant constraint(s)` — over-constrained sketch.
  See section 5.
- `hasher mismatch` — topology naming bookkeeping issue when shapes cross document
  boundaries. Usually harmless unless downstream features reference specific faces
  of the affected geometry. See section 7.
- `PropertyTopoShape: Recomputation required ... geo element version change` — normal
  when opening a document saved with a different FreeCAD version. Trigger a recompute.

### `manage_connection(action="status")`

Runs entirely on the bridge side — does not require FreeCAD to be running or
responsive. Reports:
- Whether the socket file exists and whether connections succeed
- The contents of `/tmp/freecad_mcp_last_op.json` (what the crash watcher recorded
  as the in-flight operation when FreeCAD last crashed, if anything)
- Whether `.FCStd` recovery files are present and whether they pass basic integrity
  checks

This is the first tool to call when FreeCAD crashes or won't connect.

### `manage_connection(action="clear_recovery")`

Finds and removes corrupt FreeCAD recovery files from FreeCAD's recovery directory.

**When to use:** When FreeCAD crashes immediately on startup (crash loop). This happens
because FreeCAD writes recovery files continuously during a session. If it crashes
mid-write, the recovery file is partially written and structurally invalid. On the
next launch, FreeCAD tries to restore it, encounters the corruption, crashes again,
and the cycle repeats. The socket file may exist briefly before the second crash,
which means `check_freecad_connection()` may appear to succeed even though FreeCAD
is not actually usable.

After clearing recovery files, ask the user to restart FreeCAD.

### `manage_connection(action="validate_fcstd", filename="...")`

Checks a `.FCStd` file's ZIP archive integrity before FreeCAD tries to open it. A
`.FCStd` file is a ZIP containing `Document.xml` plus shape binary files. If a save
was interrupted mid-write (crash during `doc.save()`, power loss, disk full), the
ZIP's central directory may be intact while the file data is truncated, or vice versa.

Call this when a user reports that a file "won't open" or "crashes FreeCAD on open"
before attempting to open it.

### Crash watcher — `/tmp/freecad_mcp_last_op.json`

Written atomically by the AICopilot workbench *inside* FreeCAD immediately before
each tool call executes. Cleared on successful completion. If FreeCAD crashes during
an operation, this file survives and contains exactly what was in flight.

**Contents:**
```json
{
  "tool": "execute_python",
  "args": {"code": "import Part\nresult = shape1.fuse(shape2)\n…"},
  "started_at": 1747123456.789,
  "pid": 12345
}
```

`manage_connection(action="status")` reads and reports this automatically. You can
also read it directly with `execute_python` if FreeCAD is still running:
```python
execute_python("import json; json.load(open('/tmp/freecad_mcp_last_op.json'))")
```

---

**Always check connection before anything else:**
```
check_freecad_connection()
```
If this fails, FreeCAD is not running or the AICopilot workbench isn't loaded. Tell
the user to launch FreeCAD and wait for "AI Copilot ready" in the Report View. Do
not attempt any other tool calls until connection succeeds — they will all fail and
the errors will be misleading.

---

## 1. Operation Returned an Error

**Symptom:** A tool call returned an error message or an unexpected result. The
error message from the tool itself may be vague ("operation failed", "Python error")
or may be a raw OCCT or FreeCAD exception.

**Step 1 — Read the Report View immediately:**
```
view_control(operation="get_report_view", tail=50, filter="error")
```
Do this first, before anything else, because some operations write to the Report View
and then continue — a subsequent operation may clear or overwrite the relevant lines.
The Report View often contains the real error even when the tool call error is generic.

Common patterns and what they mean:
- `BRep_API: command not done` → boolean operation (fuse/cut/common) failed; the
  input geometry has a problem. Check solid validity on inputs.
- `BRep_Builder_MakeWire: wire not done` → a wire construction failed, usually
  because edges don't share endpoints within tolerance.
- `Standard_ConstructionError` → OCCT couldn't build the requested shape; geometry
  inputs are inconsistent.
- `Sketcher ... redundant constraint` → sketch is over-constrained; the solver
  rejected it.
- `Part.OCCError` → generic OCCT failure; check the full message for specifics.

**Step 2 — Read the operation logs:**
```
get_debug_logs()
```
Look at the sequence of calls leading up to the failure. Check whether an earlier
operation produced a result that was silently bad — for example, a sketch that closed
but was under-constrained, which then produced degenerate geometry when padded.

**Step 3 — Inspect document state:**
```
view_control(operation="list_objects")
```
Verify every object you expect exists. Check visibility (hidden objects don't
participate in booleans). Look for objects in error state.

To check object states explicitly:
```python
execute_python("""
[(o.Name, o.Label, o.TypeId, getattr(o, 'State', 'n/a'))
 for o in FreeCAD.ActiveDocument.Objects]
""")
```
Objects with `State` containing `'Invalid'` or `'Error'` are broken and need
attention before anything downstream of them will work.

**Step 4 — If it's a geometry validity failure:**

Boolean operations and PartDesign features require valid, non-self-intersecting
solid input. Check each input:
```
measurement_operations(operation="check_solid", object_name="Body")
```
A valid solid returns `is_valid: true`. If it returns false, the problem is in how
that object was constructed, not in the operation you're trying to run. Fix the input
first.

**Step 5 — Clear state and retry:**
```
view_control(operation="clear")   # clear Report View
view_control(operation="undo")    # undo the failed operation if it partially executed
```
Then retry with smaller steps, checking geometry validity between each one.

---

## 2. FreeCAD Crashed / Disconnected

**Symptom:** A tool call returned a crash/disconnect error. The MCP bridge detected
that the socket connection was lost mid-operation.

**Step 1 — Diagnose immediately:**
```
manage_connection(action="status")
```
This reads the crash watcher file and bridge logs. It will tell you:
- What operation was in flight when the crash occurred (from the crash watcher)
- Whether FreeCAD's process is still running
- Whether the socket file exists
- Whether recovery files are present

Report this information to the user verbatim. It is the most useful thing you can
provide at this point.

**Step 2 — Tell the user what crashed and why.**

Common causes of crashes:
- `execute_python` with a boolean operation on invalid geometry → OCCT kernel crash,
  typically during `fuse()`, `cut()`, or `common()` on shapes with self-intersections
- Very large geometry operations (thousands of faces, complex shells) → memory
  exhaustion or OCCT timeout
- `doc.recompute()` on a document with circular dependencies → stack overflow
- Setting `LinkedObject = None` on a link that has active clones → reference count
  crash (this is a known FreeCAD issue)

**Step 3 — Ask the user to restart FreeCAD.**

Do not attempt any tool calls while FreeCAD is down — they will all fail and the
errors will be confusing. Wait for explicit user confirmation that FreeCAD is running
again and shows "AI Copilot ready" in the Report View.

**Step 4 — Check for a crash loop before the user restarts:**

If `manage_connection(action="status")` reports recovery files present, clear them
first or FreeCAD may crash again immediately:
```
manage_connection(action="clear_recovery")
```
Then ask the user to restart.

**Step 5 — After reconnection, verify document state:**
```
check_freecad_connection()
view_control(operation="list_objects")
view_control(operation="get_report_view", tail=30)
```
Determine whether the document was restored from a recovery file (it may be in a
partially-modified state), restored from the last explicit save (changes since then
are lost), or not open at all. Communicate this clearly to the user before continuing.

**Step 6 — Avoid repeating the crash:**

If the crash was during a boolean operation, validate geometry before retrying:
```
measurement_operations(operation="check_solid", object_name="...")
```
If the crash was during a large `execute_python` call, break it into smaller steps
and use `view_control(operation="checkpoint", name="before_risky_op")` before each
risky operation so you can roll back without losing everything.

---

## 3. FreeCAD Won't Connect At All

**Symptom:** `check_freecad_connection()` fails. FreeCAD may appear to be running
(the window is visible) but the socket is not answering.

**Step 1 — Diagnose the socket state:**
```
manage_connection(action="status")
```
This will report whether the socket file exists, whether connection is refused, and
whether the crash watcher has a last-op file. The combination tells you what happened:

- Socket file missing → FreeCAD is not running, or the AICopilot workbench never
  started (workbench load failure). User needs to launch FreeCAD.
- Socket file present, connection refused → FreeCAD crashed and left the socket file
  behind. User needs to quit FreeCAD (force-quit if necessary) and restart.
- Socket file present, connection hangs → FreeCAD is running but the socket thread
  is blocked. This can happen if a GUI operation is blocking the Qt event loop.
  User needs to force-quit and restart.
- Last-op file present → FreeCAD crashed mid-operation. Note what operation it was
  and tell the user.

**Step 2 — Check for crash loop:**

If FreeCAD crashes immediately on startup, clear recovery files first:
```
manage_connection(action="clear_recovery")
```

**Step 3 — Verify workbench load after restart:**

Ask the user to check View → Panels → Report View immediately after FreeCAD starts.
The correct startup sequence in the Report View is:
```
Starting FreeCAD AI Copilot Service...
MCP Debug infrastructure loaded
Crash watcher loaded — op tracking active
freecad_mcp_handler vX.X.X validated
Modular handlers loaded successfully
Socket server started on /tmp/freecad_mcp.sock
AI Socket Server started - Claude ready
FreeCAD AI Copilot ready.
```

If the Report View is empty or shows only font/Qt warnings with no AICopilot lines,
the workbench didn't load. Common causes:
- AICopilot not installed in the correct Mod directory (see AGENT-INSTALL.md)
- Python dependency missing
- Syntax error in a handler file (if the user has been editing the source)

Ask the user to check Edit → Preferences → Workbenches and confirm AICopilot is
in the enabled list. If it's there but not loading, ask them to check the FreeCAD
startup log (Help → About → Copy to Clipboard on some platforms, or the terminal
output if launched from a terminal).

---

## 4. Operation Succeeded But Result Looks Wrong

**Symptom:** No error reported, but the geometry, placement, or dimensions don't
match what was intended.

**Step 1 — Look at the actual state:**
```
view_control(operation="screenshot")
```
Don't assume the model is in the state you expect. Look at it. FreeCAD's 3D view
shows exactly what's there, including misplacements, wrong orientations, and missing
features that were supposed to appear.

**Step 2 — List all objects and their visibility:**
```
view_control(operation="list_objects")
```
Check that the objects you intended to create exist and are visible. Invisible objects
don't render in the viewport and don't participate in boolean operations. Objects with
`Visibility: false` need to be shown explicitly.

**Step 3 — Measure what's actually there:**
```
measurement_operations(operation="get_bounding_box", object_name="...")
measurement_operations(operation="get_volume", object_name="...")
measurement_operations(operation="list_faces", object_name="...")
```
Compare measured dimensions against intended dimensions. A bounding box tells you
size and position; face normals from `list_faces` tell you orientation.

**Step 4 — Check if a previous operation produced bad intermediate state:**
```
get_debug_logs()
```
Look for any earlier operations that produced unexpected results. A sketch that was
under-constrained will produce geometry that looks reasonable but has wrong dimensions;
a pad on such a sketch inherits the error.

**Step 5 — Undo and rebuild rather than patching:**
```
view_control(operation="undo")
```
Use undo as many times as needed to get back to a known good state. Then rebuild with
explicit verification steps — check sketch constraints, verify sketch is fully
constrained before closing, check solid validity after each boolean.

---

## 5. Sketch or Constraint Failure

**Symptom:** A sketch operation fails, the sketch solver reports redundant or
conflicting constraints, or the padded/pocketed result has wrong geometry.

**Step 1 — Check sketch validity and constraint count:**
```
sketch_operations(operation="verify_sketch", sketch_name="SketchName")
```
This reports whether the sketch is valid, how many degrees of freedom remain (should
be 0 for fully constrained), and any solver issues.

**Step 2 — List all constraints:**
```
sketch_operations(operation="list_constraints", sketch_name="SketchName")
```
Look for:
- Redundant constraints — the same dimension constrained twice, or a geometric
  constraint that's already implied by another. Remove duplicates.
- Conflicting constraints — two constraints that can't both be satisfied (e.g.,
  a horizontal constraint on a line that also has a fixed angle). One must be removed.
- Missing constraints — degrees of freedom > 0 means the sketch is under-constrained.
  Geometry will be placed at FreeCAD's best guess, which may not be what you want.

**Step 3 — Check the Report View for solver output:**
```
view_control(operation="get_report_view", tail=20, filter="sketch")
```
FreeCAD's sketch solver logs detail here when it fails to converge or detects
redundancy.

**Step 4 — Common constraint mistakes:**

- Geometry IDs are assigned sequentially from 0 as geometry is added. Special IDs:
  `-1` = X axis, `-2` = Y axis. If you add geometry in a different order than
  expected, the IDs shift.
- Point IDs: `0` = on edge (any point), `1` = start vertex, `2` = end vertex,
  `3` = center (for arcs/circles).
- A rectangle added with `add_rectangle` creates 4 line segments (geo IDs 0–3) plus
  a construction point. Constraints on the rectangle reference these IDs.

---

## 6. External Link / Reference Broken

**Symptom:** On document open, the Report View shows messages like:
```
Link not restored
Linked object: Body
Linked document: general-windows
```
or:
```
Standard Window Master A: Link broken!
Object: Body
File: ../../../General Parts/general-windows.FCStd
```

**What this means:** An `App::Link` object in the current document points to an
object by name in an external `.FCStd` file. That named object no longer exists —
either the file was rebuilt (the object was renamed or replaced), the file was moved,
or the link was saved pointing to an object that never existed.

Note: the missing object (`Body` in the example) is in the *external* file, not in
the current document. Don't look for it in the current document's object list.

**Step 1 — Identify which links are broken:**
```python
execute_python("""
doc = FreeCAD.ActiveDocument
links = [o for o in doc.Objects if o.TypeId == 'App::Link']
[(l.Name, l.Label, str(l.LinkedObject)) for l in links]
""")
```
Any link where `LinkedObject` prints as `None` is broken.

**Step 2 — Find the external file path:**
```python
execute_python("""
import FreeCAD
# Get the source document — it may be open as a partial document
for name, doc in FreeCAD.listDocuments().items():
    print(name, doc.FileName)
""")
```

**Step 3 — Check what objects actually exist in the external file:**

The external document may be loaded as a "partial document" — FreeCAD lazy-loads
external references, so `doc.Objects` may be empty even though the file is open.
Read the file directly:
```python
execute_python("""
import zipfile, re
path = '/actual/path/to/external.FCStd'
with zipfile.ZipFile(path) as z:
    xml = z.read('Document.xml').decode()
names = re.findall(r'<Object name="([^"]+)"', xml)
names
""")
```
This lists every object in the file without triggering a full load.

**Step 4 — Force-load the partial document if needed:**
```python
execute_python("""
ext_doc = FreeCAD.getDocument('external_doc_name')
ext_doc.restore()   # forces full load
[(o.Name, o.Label, o.TypeId) for o in ext_doc.Objects]
""")
```

**Step 5 — Repair the link:**

Once you know the correct target object name:
```python
execute_python("""
doc = FreeCAD.ActiveDocument
ext_doc = FreeCAD.getDocument('external_doc_name')
lnk = doc.getObject('Link002')
target = ext_doc.getObject('Fusion')   # correct object name
lnk.LinkedObject = target
doc.recompute()
print('LinkedObject now:', lnk.LinkedObject)
""")
```

**Step 6 — Save to make the fix permanent:**
```
view_control(operation="save_document")
```
Without saving, the fix will be lost if FreeCAD closes or crashes.

---

## 7. Topology Naming Warnings (hasher mismatch, ElementMap errors)

**Symptom:** After a recompute or document open, the Report View contains:
```
<TopoShape> TopoShapeExpansion.cpp(989): hasher mismatch
```
or:
```
<ElementMap> <string>(1): Invalid element name string id
```

**What these mean:** FreeCAD's topology naming system assigns stable string IDs to
individual faces, edges, and vertices so that downstream features (fillets, sketch
attachments) can reference them reliably even when the model changes. These warnings
fire when shapes from different sources (different documents, different FreeCAD
versions) have incompatible hashers — the internal ID generators don't match — or
when element name string IDs are malformed.

**Do you need to act on these?**

Usually no. These warnings matter only if something downstream holds a reference to
a specific face or edge of the affected geometry — for example, a fillet that
references a specific edge by name, or a sketch attached to a specific face. If no
downstream features reference topology of the affected objects, the warnings are
noise and the geometry is correct.

To determine whether to act: check whether any objects report `State: Invalid` or
`State: Error` after the recompute. If everything recomputes cleanly, the warnings
are harmless. If objects are invalid, the topology naming issue may be contributing.

---

## 8. Document Recompute Failures

**Symptom:** After creating or modifying objects, some show orange or red icons in
the model tree, or `view_control(operation="list_objects")` shows objects with error
states.

**Step 1 — Force recompute:**
```python
execute_python("FreeCAD.ActiveDocument.recompute()")
```
Some recomputes are deferred. Forcing one often resolves apparent errors.

**Step 2 — Check what's invalid:**
```python
execute_python("""
[(o.Name, o.Label, o.TypeId, getattr(o, 'State', []))
 for o in FreeCAD.ActiveDocument.Objects
 if getattr(o, 'State', []) and 'Invalid' in getattr(o, 'State', [])]
""")
```

**Step 3 — Trace the dependency chain:**

Objects in FreeCAD have dependencies — a Pad depends on a Sketch, a Boolean depends
on its inputs. An invalid object invalidates everything downstream. Find the earliest
invalid object in the dependency chain; fixing that one will often fix everything
downstream automatically after recompute.

**Step 4 — Read the Report View:**
```
view_control(operation="get_report_view", tail=50, filter="recompute")
```
FreeCAD logs which objects failed recompute and why.

---

## General Principles

**Read before guessing.** Use `get_report_view`, `get_debug_logs`, and `list_objects`
before forming a hypothesis. The diagnostic tools were built specifically because
FreeCAD's error reporting is cryptic — use them.

**Don't patch bad state forward.** If an object is in an invalid state, undo to
before it was created and rebuild it correctly. Trying to fix a broken object in
place usually propagates the problem to downstream features.

**Validate geometry after boolean operations.** After every `fuse`, `cut`, or
`common`, call `measurement_operations(operation="check_solid")` on the result.
Boolean operations on invalid input silently produce invalid output. Building further
features on invalid geometry leads to cascading failures that are hard to diagnose.

**Use checkpoints before risky operations.** Before any operation that could crash
or corrupt the document:
```
view_control(operation="checkpoint", name="before_risky_op")
```
If something goes wrong, you can roll back to this state rather than having to start
over. Call `view_control(operation="rollback_to_checkpoint", name="before_risky_op")`
to restore.

**Break large `execute_python` calls into smaller ones.** A crash during a long
operation loses the whole thing. A boolean on large geometry, a complex loft, or a
long array generation are all candidates for splitting. Run the expensive geometry
first, verify it, then proceed.

**Report crash details to the user.** If FreeCAD crashed, tell the user exactly what
`manage_connection(action="status")` shows — what operation was in flight, what the
crash watcher recorded, whether recovery files are present. This is the information
needed to file a useful bug report at https://github.com/blwfish/freecad-mcp/issues.

**Save after successful fixes.** After repairing a broken link, fixing invalid
geometry, or completing any significant work, save explicitly:
```
view_control(operation="save_document")
```
FreeCAD's autosave recovery mechanism is a fallback, not a substitute for saving.
