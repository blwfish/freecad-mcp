# Known Issues - FreeCAD MCP

## Threading / GIL Deadlock Issues

### Issue: Document Creation from Socket Thread Causes Crashes

**Status**: FIXED (2026-02-27)
**Severity**: High (Caused FreeCAD crashes)
**Affected**: v3.4.1+

#### Problem

Calling `FreeCAD.newDocument()` from the socket server thread causes Python GIL deadlocks when Qt tries to update the GUI.

#### Root Cause

`BaseHandler.get_document(create_if_missing=True)` called `FreeCAD.newDocument()` directly from the socket server thread. When FreeCAD creates a document, it triggers Qt GUI updates. Qt's event filter tries to acquire the Python GIL, but the GIL is already held by the socket thread, causing a deadlock.

#### Fix Applied

`base.py` - `get_document()` and `create_body_if_needed()` (2026-02-27):
- `get_document(create_if_missing=True)` now routes `FreeCAD.newDocument()` through the GUI thread via `run_on_gui_thread()`, using the same QTimer-based task queue that `document_ops.create_document()` uses
- `create_body_if_needed()` delegates to `get_document(create_if_missing=True)` instead of calling `FreeCAD.newDocument()` directly
- All handlers that use `create_if_missing=True` (primitives, sketch, partdesign) are now safe automatically
- Falls back to direct call in headless/console mode where there is no Qt event loop

**Superseded (v5.8.1, later):** the above `create_if_missing` mechanism was later
removed entirely rather than hardened further. `base.py`'s `get_document()` now
takes no arguments and never auto-creates a document under any circumstance —
callers must check for `None` and return an error directing the caller to
`view_control(operation="create_document")` first. This is a stricter fix for
the same GIL-deadlock class, not an extension of the mechanism described above.

## CAM Tool Creation

### Issue: create_tool sent a stale parameter format to ToolBit.from_dict()

**Status**: FIXED (2026-07-20)
**Severity**: High (unconditional failure on FreeCAD weekly builds from roughly mid-2026 onward)
**Affected**: `cam_tools.py`'s `create_tool()`, discovered while verifying the
`dev-weekly` CI slot's tag bump from `weekly-2026.04.01` to `weekly-2026.07.15`

#### Problem

`create_tool()` built its `ToolBit.from_dict()` parameter dict as
`{"Diameter": {"type": "Length", "value": "6.0 mm"}, ...}`. On
`weekly-2026.07.15`, this raised `Error in create_tool: Either quantity,
float with units or string expected` every time.

#### Root Cause

FreeCAD's `Path/Tool/toolbit/models/base.py` (`ToolBit.from_shape()`) hands
each `parameter` dict value straight to `PathUtil.setProperty()`, which
sets it directly on the underlying `Base::Quantity`-typed property — it
never unwraps a `{"type": ..., "value": ...}` wrapper. That wrapper shape
was valid against an older `ToolBit` implementation; sometime between
`weekly-2026.04.01` and `weekly-2026.07.15` upstream rewrote `ToolBit` as
an `Asset`/`ABC`-based class whose `to_dict()`/`from_dict()` round-trip
uses plain scalars (confirmed via `to_json()`: `FreeCAD.Units.Quantity` ->
`.UserString`, a plain string like `"6 mm"`, not a wrapped dict) — the
same plain-scalar shape `update_tool()` in the same file already
correctly used. Confirmed as a genuine regression, not a
pre-existing/tolerated bug: CI was green against the (then-)pinned
`weekly-2026.04.01` immediately before this fix.

#### Fix Applied

`create_tool()` now builds `parameters["Diameter"] = f"{diameter} mm"`
etc. — plain scalars, matching `update_tool()`'s existing (correct) form.

#### Second, deeper crash on Linux (both archs) — FIXED 2026-07-22 (our side; upstream defect remains)

**Status**: FIXED in our own code 2026-07-22, verified against the real
broken build. See the dated updates below for the full investigation;
skip to "2026-07-22 update #4" for the resolution if you just need the
fix.

The parameter-format fix above is real and necessary but **not sufficient**.
The *same* `create_tool()` call — with the fix applied — still crashes the
whole FreeCAD process (not a clean Python exception) on `weekly-2026.07.15`:
first confirmed on a local Docker container's Linux aarch64 build, then
**confirmed again on real CI** (`gh run 29740175585`, Linux x86_64,
`ubuntu-latest`) after the dev-weekly tag bump + parameter fix were pushed
together — `test_create_endmill` fails and kills the shared FreeCAD
instance, cascading into ~138 downstream test errors for the rest of that
CI job. The identical fixed code runs cleanly (no crash) on FC-clone's
locally-built macOS arm64 from the same weekly source — this is Linux-only,
not platform-universal, and it is **not** an aarch64-only artifact as first
suspected; it reproduces on the actual x86_64 CI runner too.

**Root cause not confirmed, despite substantial investigation.** A
promising lead surfaced via bisection: merely importing `handlers.primitives`
(before calling anything CAM-related) was enough to make an otherwise-clean
`ToolBit.from_dict()` call crash. `primitives.py`, `partdesign_ops.py`,
`document_ops.py`, and `view_ops.py` all do an *unconditional*
`import FreeCADGui` at module level, unlike `base.py`/`execute_python_ops.py`/
`diagnostics_ops.py`, which correctly guard it behind `if FreeCAD.GuiUp:`
— a real, pre-existing inconsistency worth fixing regardless. But this
lead did not hold up under closer isolation: a minimal repro built around
exactly this guard (skipping the `FreeCADGui` import entirely when
`FreeCAD.GuiUp` is `False`, which it is in headless mode) **still crashed**,
while functionally-identical code typed inline into the same command
**did not** — i.e. the crash's presence depended on *how the reproduction
script was invoked* (read from a file vs. typed inline), not on its content.
That is not a signature a Python-level code fix can be verified against with
confidence; it smells of a genuine, timing/environment-sensitive native
crash inside this FreeCAD build's CAM/Material/Gui interaction on Linux,
not a deterministic logic bug in `cam_tools.py`.

**Decision (2026-07-20):** rather than guess at a fix that can't be reliably
verified, or block the rest of the `dev-weekly` bump (Goal 1 of
`SPEC-fc-api-drift-detection.md`) on it, this is left as a known, open,
tracked failure. The `dev-weekly` CI slot is expected to fail on CAM tests
until this is properly root-caused — check CI status before relying on that
slot as a CAM-regression gate in the meantime.

**Next steps for whoever picks this up:** get a real core dump + `gdb`
backtrace from the actual crash (not just the `SIGSEGV`/`__kernel_rt_sigreturn`
frame this session captured, which has no useful symbol information) — that
requires enabling core dumps in whatever environment reproduces it and
attaching gdb post-mortem, not just watching stderr. Confirm/deny the
`FreeCADGui`-guard lead properly by applying the guard for real (not a
one-off repro string) to all four files and running the actual `dev-weekly`
CI job end to end, since local reproduction has proven unreliable enough
that CI itself may be the only trustworthy signal here.

#### 2026-07-22 update — first real crash evidence from CI, confirms genuine SIGSEGV

`tests/integration/conftest.py`'s spawned headless instance has its
stdout/stderr piped (`subprocess.PIPE`) but nothing ever drained those pipes
while tests ran — a mid-run crash's own error output was captured into the
kernel pipe buffer and then silently discarded when the session ended,
without ever reaching the CI log. `_socket_call()` in
`test_e2e_workflows.py` now calls `conftest.diagnose_dead_spawned_process()`
on any connection failure, which detects the spawned process has exited and
attaches its captured stdout/stderr tail to the raised `ConnectionError`
(cached after the first read, since `communicate()` only works once).

First real capture (`gh run 29937909054`, Linux x86_64, `dev-weekly` slot,
still `test_create_endmill`):

```
Spawned FreeCAD process died unexpectedly: returncode=1
--- stderr (tail) ---
Program received signal SIGSEGV, Segmentation fault.
#0  /lib/x86_64-linux-gnu/libc.so.6(+0x45330) [0x7ffbb1245330]
```

Two things this changes:

1. **Confirms a genuine SIGSEGV**, not a hang, OOM-kill, or GH-runner-level
   termination — `returncode=1` (not `-11`/`139`) because FreeCAD's own
   internal crash handler intercepts the signal, prints a one-frame
   backtrace, and exits cleanly rather than dying with the raw signal.
2. **The crash frame is inside libc, not FreeCAD/OCCT/Coin3D code.** A
   frame in `libc.so.6` at a `malloc`/`free`/`memcpy`-family offset (no
   symbols to say which) is the classic signature of *heap corruption*
   manifesting later inside an allocator call, rather than a straightforward
   null-pointer dereference at the actual buggy call site. This weakens
   (doesn't rule out) the `FreeCADGui`-import-guard lead above — a missing
   GUI-thread guard would more plausibly crash inside Coin3D/Qt symbols, not
   raw libc allocator internals.

Still no symbolized backtrace of the actual corrupting write — this only
proves *that* it's heap corruption and roughly *when* (during/shortly after
`create_tool`'s `ToolBit.from_dict()` call), not *where*. The diagnostic
capture above is now permanent, so every future `dev-weekly` CI failure on
this test will automatically surface the real stderr going forward — no
more guessing from a bare `ConnectionError`. Next real step is still a
symbolized core dump (`ulimit -c unlimited` + `gdb -batch -ex "bt full"` in
CI, or bisecting the exact weekly build between the last-known-good
`weekly-2026.04.01` and broken `weekly-2026.07.15` to narrow which upstream
change introduced the corruption).

#### 2026-07-22 update #2 — root cause found: expat symbol collision between Python and Coin3D

`ulimit -c`/`core_pattern` never worked as a capture mechanism: FreeCAD's
own SIGSEGV handler (the one-frame printer above) intercepts the signal and
exits cleanly (`returncode=1`, not a signal death) before the OS ever sees
an *unhandled* fatal signal, so no core file is ever produced regardless of
core-dump configuration. Fixed by running the spawned headless instance
under `gdb -batch -ex run ...` from launch instead
(`tests/integration/conftest.py`'s `_spawn_headless`, gated behind
`FREECAD_MCP_TEST_GDB_TRAP=1`, now set for the `dev-weekly` CI slot and the
`bisect-cam-crash.yml` workflow) — gdb's default signal disposition stops
the process *before* passing the signal to FreeCAD's own handler, so a real
backtrace can be captured at the actual fault site. Getting there took
several dead ends worth recording so they aren't re-discovered: gdb can't
load a shebang script via `--args` (must launch the interpreter and let
`follow-exec-mode same` follow the re-exec); `thread apply all bt full` on
every thread hung the whole CI run indefinitely (`bt full`'s deep
pointer-chasing through corrupted memory, or simply a great many threads,
made it pathologically slow — dropped to `bt full` on just the
gdb-auto-selected faulting thread); gdb's default per-signal disposition
stops on signals besides SIGSEGV too, so `handle all nostop noprint pass`
/ `handle SIGSEGV stop print nopass` was needed to stop only for the real
fault, not some benign signal FreeCAD/Python's runtime uses internally.

The real backtrace (`gh run 29943979760`, Linux x86_64, `dev-weekly` slot):

```
It stopped with signal SIGSEGV, Segmentation fault.
#0  0x0000000000000000 in ?? ()
No symbol table info available.
#1  0x00007fffef165ea2 in XML_ParseBuffer ()
      from .../squashfs-root/usr/lib/libCoin.so.80
#2  expat_parse (self=..., data="<?xml version='1.0' encoding='utf-8'?>\n<!--\n
      FreeCAD Document, see https://www.freecad.org...\n-->\n<Document
      SchemaVersion=\"4\" ProgramVersion=\"1.2R45099 (Git)\" ...", ...)
      at .../Modules/_elementtree.c:3827
#3  _elementtree_XMLParser__parse_whole (...) at .../_elementtree.c:4018
    ... ordinary CPython eval-loop frames ...
#10 task_step_impl (...) at .../Modules/_asynciomodule.c:2693
    ... our own async job-dispatch machinery ...
```

**This is not heap corruption in FreeCAD/OCCT/our own code. It's an ELF
symbol collision.** CPython's built-in `xml.etree.ElementTree` accelerator
(`_elementtree.c`) calls `XML_ParseBuffer` expecting *its own* linked
libexpat. `libCoin.so.80` (Coin3D, FreeCAD's scene-graph library) also
exports a symbol named `XML_ParseBuffer` — its own bundled/statically
-linked expat, built against a different internal struct layout. On Linux,
the default ELF symbol resolution is a flat, global namespace: with lazy
binding, whichever shared object providing a matching symbol name is
resolved *first* wins for the rest of the process's life, regardless of
which library a given caller was actually linked against. Once
`libCoin.so.80` is loaded into the process (headless `FreeCADCmd` still
loads it, apparently for unrelated reasons), any future call to
`XML_ParseBuffer` — including Python's *own* `_elementtree` C accelerator's
internal calls — can get bound to Coin's incompatible version instead of
Python's real libexpat. The two `XML_Parser` struct layouts don't match, so
Python hands Coin's expat pointers/data it never expects; frame #0 is a
call through an all-zero function pointer, the direct result of dereferenc-
ing a garbage vtable/function-table entry read from a misinterpreted
struct.

This also explains, independently and for free, why the crash is
**Linux-only and never reproduced on macOS**: macOS's dynamic linker uses a
two-level namespace by default, which resolves each symbol reference
against the *specific* library it was linked against, not a flat global
search — the exact class of collision this is simply cannot happen there.
That's strong independent confirmation this is the real mechanism, not a
coincidence.

The XML content in frame #2's `data` argument
(`<Document SchemaVersion="4" ...>`) is FreeCAD's own internal document
serialization format — this is *general* document XML (de)serialization,
not something CAM-specific. `create_tool` is very likely just the first
operation in the test suite's execution order that happens to trigger a
Python-level `xml.etree.ElementTree` parse of document content (plausibly
via the `ToolBit` Asset/ABC rewrite noted above) while `libCoin.so` is
already resident — any other operation hitting the same combination
(Python XML parsing + Coin already loaded) should crash identically.

**Likely upstream, not ours.** This is a FreeCAD/Coin3D AppImage packaging
characteristic (a global-visibility symbol collision), not a bug in
`cam_tools.py` or this project's own code. Worth a FreeCAD upstream bug
report once confirmed via the bisection below.

**A real, testable workaround exists, not yet implemented:** ELF lazy
binding resolves (and caches) a symbol on *first use*. Forcing Python's own
`xml.etree.ElementTree` to parse something trivial very early in
`headless_server.py`'s startup — before anything that would load
`libCoin.so` — should bind `XML_ParseBuffer` to Python's own correct
libexpat for the rest of the process's lifetime, before Coin's colliding
symbol is even in the picture. Untested; flagged as the concrete next step
rather than implemented speculatively.

**Bisection — DONE, regression isolated to a single week.** Using
`.github/workflows/bisect-cam-crash.yml` (`workflow_dispatch`, takes a
`tag` input — downloads one FreeCAD weekly AppImage, runs the CAM
tool-creation tests, and does a static `nm -D` check on `libCoin.so.80`
for a defined `XML_ParseBuffer` export, no live crash-test needed for that
part), binary search across the 16 weekly tags between
`weekly-2026.04.01` (good) and `weekly-2026.07.15` (bad) converged in 4
dispatches:

| tag | CAM tests | `XML_ParseBuffer` in `libCoin.so.80` |
|---|---|---|
| `weekly-2026.06.03` | 5 passed | `U` (undefined — external symbol, no collision possible) |
| `weekly-2026.06.24` | 5 passed | `U` |
| `weekly-2026.07.08` | 5 passed | `U` |
| **`weekly-2026.07.09`** | **1 failed, 4 errors** | **`T` (defined/exported — collides)** |
| `weekly-2026.07.15` | crashes (original finding) | `T` |

The `U` → `T` flip lands *exactly* on the `weekly-2026.07.08` →
`weekly-2026.07.09` boundary — every earlier build checked has Coin
dynamically linking an external expat (same shared library both Coin and
Python would resolve to, so no ABI mismatch is possible); every build from
`07.09` onward has Coin exporting its own `XML_ParseBuffer` with global
visibility, which is what creates the collision. This is a single week's
Coin3D build/packaging change, not a gradual drift.

Timing lines up with FreeCAD's own bundled-Coin/Pivy submodule work:
[#31120](https://github.com/FreeCAD/FreeCAD/issues/31120) ("current build
system no longer works due to bundled coin / pivy submodules," filed
2026-07-03) closed via
[#31122](https://github.com/FreeCAD/FreeCAD/pull/31122) ("3rdParty:
install only bundled Coin runtime" — explicitly aimed at making bundled
Coin "private to the FreeCAD install"), merged **2026-07-09T08:20 UTC**,
the same day our bisection found the first broken build. Plausible that
#31122 solved the *installed-file* conflict (headers, pkg-config,
`coin-default.cfg`) it targeted but didn't reach symbol-visibility inside
the compiled `.so` itself. Not independently confirmed against that PR's
actual CMake changes.

#### 2026-07-22 update #3 — minimal repro found: no CAM/ToolBit code needed at all

Three rounds of `.github/workflows/minimal-repro-expat-crash.yml`
(`workflow_dispatch`, bare `FreeCADCmd` + a plain Python script, no
freecad-mcp socket server or async job dispatch in the loop) to close the
"is this really upstream, or is our own code somehow relevant"
question before filing anything:

1. **Import `Path` and `Path.Tool.Bit.ToolBit` alone:** no crash, `libCoin`
   never loaded. Disproves the assumption that the CAM/Path Asset-based
   `ToolBit` code is what pulls Coin in.
2. **Replicate `create_tool()`'s exact call chain** (`ToolBit.from_dict()`,
   `attach_to_doc()`, on a real document): still no crash, still no
   `libCoin`. Our CAM handler code itself is not the trigger.
3. **Add `import FreeCADGui`** (with `FreeCAD.GuiUp == 0`, genuinely
   headless) right after `import FreeCAD`, before anything else: **loads
   `libCoin.so.80.0.10` immediately**, and the very next call —
   `FreeCAD.newDocument("ReproDoc")`, nothing CAM-related — **crashes with
   the identical signature**: `Thread 1 "freecadcmd" received signal
   SIGSEGV` / `#0 0x0000000000000000 in ?? ()` / `#1 ... XML_ParseBuffer ()
   from .../libCoin.so.80`.

**This means the real trigger is `import FreeCADGui` itself, not CAM tool
creation.** `create_tool` was never special — it's simply the first
operation in the test suite's execution order that happens to run after
all handler modules have been imported. And **this part is in our own
control, independent of the upstream Coin/expat visibility issue**:
`primitives.py`, `partdesign_ops.py`, `document_ops.py`, and `view_ops.py`
all do an unconditional `import FreeCADGui` at module level, unlike
`base.py`/`execute_python_ops.py`/`diagnostics_ops.py`, which correctly
guard it behind `if FreeCAD.GuiUp:` — exactly the inconsistency flagged
back on 2026-07-20 (see above), whose lead was dropped at the time because
an *ad hoc local* differential test (file-read repro vs. inline-typed
command) gave inconsistent results. That test wasn't isolated properly;
this gdb-confirmed, CI-reproduced result supersedes it. **Guarding those
four imports behind `if FreeCAD.GuiUp:` (matching the already-correct
files) should prevent `libCoin.so` from ever loading in headless MCP
usage, eliminating this crash for our own users regardless of whether or
when upstream fixes Coin's symbol visibility.** Not yet implemented —
flagged as the concrete next step.

#### 2026-07-22 update #4 — fixed and verified against the real broken build

`primitives.py` and `partdesign_ops.py` imported `FreeCADGui` unconditionally
but never referenced it anywhere in either file — dead imports, deleted
outright. `document_ops.py` and `view_ops.py` use it for real GUI-only
operations (Selection, undo/redo, workbench activation, view control) —
every call site in both files was already wrapped in `try/except`, so
guarded to `FreeCADGui = None` in headless mode, those now raise a caught
`AttributeError` instead of segfaulting the whole process. Not a polished
error message, but safe, and these were never meaningful operations
headless anyway.

First verification run against `weekly-2026.07.15` still failed —
`cam_operations`'s `create_job()` turned out to have the *same* bug via a
different route: `from Path.Main.Gui.Job import ViewProvider`, wrapped
only in `try/except ImportError`, which guards against import *failure*,
not the Coin3D load that happens as a side effect of the import
*succeeding*. Fixed by checking `FreeCAD.GuiUp` before attempting the
import at all, same pattern.

A second verification run then showed `test_job_status` still failing —
but with the `FREECAD_MCP_TEST_GDB_TRAP` debugging aid (added earlier in
this investigation) still enabled on the main CI slot, and zero
`GDB-STOPPED`/`SIGSEGV` output anywhere in the log despite a real
connection failure. Root cause of *that*: `tests/integration/conftest.py`'s
`_stop_headless()` teardown only waits 5 seconds before SIGKILLing the
spawned process — not long enough for gdb's `bt full` to finish on a slow
crash, so our own teardown was killing gdb mid-backtrace and discarding
the very diagnostic we needed. Removed `FREECAD_MCP_TEST_GDB_TRAP` from
the routine `dev-weekly` CI slot (still available for focused
investigation by re-adding the env var; needs a longer teardown budget
before it's safe to leave on by default).

With gdb-trap removed, the **full integration suite passes cleanly against
`weekly-2026.07.15`: 157 passed, 1 skipped, 0 failed, 0 errors** (previously
100% broken on this test file). `test_job_status`'s earlier failure was an
artifact of our own gdb instrumentation, not a real remaining crash — both
fixes above were necessary and sufficient.

**This is a mitigation for our own headless usage, not a fix for the
underlying defect.** `libCoin.so.80` exporting `XML_ParseBuffer` with
global visibility is still a real bug in how Coin3D is built/bundled,
independent of us — anyone else who imports `FreeCADGui` (for any reason,
even headlessly) and then does any Python-level XML parsing is still
exposed. Upstream report still worth filing (draft prepared separately,
not yet submitted).

#### 2026-07-22 update #5 — gdb-trap is NOT safe to leave on for the full suite; correcting update #4

Widened `_stop_headless()`'s teardown timeout from 5s to 95s (still in
place — correct and harmless on its own: this fixture is session-scoped,
runs once per CI job not once per test, and `proc.wait(timeout=N)`
returns the instant the process actually exits rather than blocking for
the full duration, so a healthy run pays nothing extra). Re-enabled
`FREECAD_MCP_TEST_GDB_TRAP` on the routine `dev-weekly` slot on the
theory that the timeout widening had fixed what made it unsafe.

**That theory was wrong.** The exact same `test_job_status` cascade
failure came back — but this time with a real ~20-second stall (not
instant EAGAIN) immediately after `test_configure_job_stock`, every time,
and zero `GDB-STOPPED`/`SIGSEGV` output despite the wider teardown
window. The reasoning error: `diagnose_dead_spawned_process()` is a
**non-blocking** `proc.poll()` check made *during* the test run, at the
moment a connection fails — it can only report a backtrace if the
process has *already* exited by then. Widening the teardown timeout only
changes what happens at the very *end* of a session (final cleanup); it
cannot retroactively help a check that already ran and saw "still
running" mid-session. Those are two different problems that happened to
look similar.

Reverted `FREECAD_MCP_TEST_GDB_TRAP` back to off on the routine suite.
Whatever gdb-wrapping the *entire* 158-test session costs around job
creation/configuration — a real, reproducible stall, not a false
alarm — is a separate, unresolved problem (candidate: ptrace overhead
interacting badly with signal-heavy code somewhere in that path, but not
investigated further). **`FREECAD_MCP_TEST_GDB_TRAP` remains available
and appropriate for a narrowly-scoped run** — a single test or file, via
`bisect-cam-crash.yml` or `minimal-repro-expat-crash.yml`, both of which
worked reliably throughout this investigation — just not wrapped around
the full suite until this mid-session cost is separately diagnosed.

Final state, confirmed via CI: **157 passed, 1 skipped, 0 failed, 0
errors against `weekly-2026.07.15`**, gdb-trap off, wider teardown
timeout in place.

## Large Document Handling

### Issue: list_objects Crashes on Large DXF Imports

**Status**: FIXED (2025-12-26)
**Severity**: High (Causes FreeCAD crashes)

#### Problem

Importing DXF files (e.g., from 3rdPlanit) creates many objects including `App::FeaturePython` layer objects. Calling `list_objects` on documents with 1000+ objects caused FreeCAD to crash with exit code 141 (SIGPIPE).

#### Root Cause

The original `list_objects` handler attempted to serialize all objects in the document at once. With large documents (1000+ objects), this created:
1. Very large JSON payloads that could overwhelm the socket communication
2. Potential GIL issues when accessing properties on many FeaturePython objects

#### Fix Applied

`document_ops.py` - `list_objects()` (2025-12-26):
- Added pagination with `limit` (default 100, max 500) and `offset` parameters
- Added `type_filter` parameter to filter by TypeId
- Wrapped property access in try/except to handle problematic objects
- Returns metadata: `total`, `returned`, `offset`, `limit` along with `objects` array

#### Example Usage

```python
# Get first 100 objects (default)
list_objects()

# Get objects 100-199
list_objects(offset=100)

# Get only Part::Feature objects
list_objects(type_filter="Part::Feature")

# Get up to 500 objects
list_objects(limit=500)
```

## Sketch Operations

### Issue: `sketch_operations(operation="add_constraint")` cannot bind a constraint to a spreadsheet expression

**Status**: FIXED (2026-07-27), verified live against real FreeCAD 26.3.0.
See "Resolution" at the end of this section.
**Severity**: Medium — no crash, but it silently blocks the tool's most useful
use case (spreadsheet-parametric sketches) and forces `execute_python()` as
the only way to build them
**Affected**: `AICopilot/handlers/sketch_ops.py`, `add_constraint()` (currently
around line 550-685); the `value` field in the `sketch_operations` tool schema
in `freecad_mcp_server.py` (around line 953)
**Discovered**: 2026-07-27, building a spreadsheet-driven "master sketch" for
an HO-scale coach side kit (window array + registration pins), where every
constraint needed to track a `Dimensions` spreadsheet alias rather than a
literal number

#### Problem

`add_constraint`'s `value` argument is typed as a plain number end to end —
both in the JSON schema (`"value": {"type": "number", ...}`) and in the
handler, which does `value = args.get('value', None)` and passes it straight
into `Sketcher.Constraint(ct, ..., value)`. There is no way to pass an
expression string (e.g. `"Dimensions.WindowWidthStd"` or
`"Dimensions.PairCenter1 - Dimensions.PitchNarrow/2"`) instead of a bare
number. Every dimensional constraint (`Distance`, `DistanceX`, `DistanceY`,
`Radius`, `Diameter`, `Angle`) is affected identically.

This isn't a FreeCAD limitation — `Sketcher::SketchObject.setExpression()`
works exactly as documented once called directly:

```python
idx = sketch.addConstraint(Sketcher.Constraint('DistanceX', geo_id, 1, 0.0))
sketch.setExpression(f'Constraints[{idx}]', 'Dimensions.PanelLength / -2')
```

That's a real, working pattern — I used it by hand via `execute_python()` for
an entire sketch (dozens of constraints, all spreadsheet-bound, fully
resolves `FullyConstrained=True`) because `sketch_operations` had no way to
express it. `spreadsheet_operations`'s own handler
(`AICopilot/handlers/spreadsheet_ops.py:329`) already calls `setExpression()`
for cell-to-property bindings elsewhere in this same codebase, so the
capability and the calling convention are already established here — sketch
constraints just never picked it up.

#### Root Cause

Schema/implementation gap, not a FreeCAD API gap. `add_constraint` was built
around literal dimensioning (matching the GUI's "type a number" default
Ctrl workflow) and was never extended to accept an expression string, even
though the underlying `Sketcher.Constraint` + `setExpression()` combination
that would make it work is a two-line addition.

#### Suggested Fix

1. Add an `"expression"` string parameter to the `sketch_operations` schema
   in `freecad_mcp_server.py` (sibling to `value`, both optional — exactly
   one of the two should be provided for dimensional constraint types).
2. In `add_constraint()` (`sketch_ops.py`), after `idx = sketch.addConstraint(c)`,
   if `args.get('expression')` is set, call
   `sketch.setExpression(f'Constraints[{idx}]', expression)` instead of (or
   in addition to, for the initial literal seed value) using `value`.
3. Recompute (`self.recompute(doc)`) after setting the expression — expressions
   only evaluate on recompute, not immediately on `setExpression()`.
4. Worth doing at the same time since it's the same code path: consider
   exposing `sketch.FullyConstrained` / `sketch.DoF` / `sketch.ConflictingConstraints`
   / `sketch.RedundantConstraints` in the return message instead of (or
   alongside) the current `dof_msg` derived from `sketch.solve()`'s return
   value. `solve()`'s return code is a solver-success code (0 = solved
   without conflict), **not** a degrees-of-freedom count — a sketch can be
   genuinely under-constrained (real leftover DOF) and still get `solve()==0`,
   so the current `", DoF={dof}"` message is misleading. `sketch.DoF` and
   `sketch.FullyConstrained` are the properties that actually answer "is this
   sketch fully determined."

#### Related, NOT a bug: `Symmetric` constraint type is already supported here

While investigating this I confirmed `add_constraint(constraint_type="Symmetric",
geo_id1=..., pos_id1=..., geo_id2=..., pos_id2=..., sym_geo=..., sym_pos=0)`
already builds `Sketcher.Constraint('Symmetric', g1, p1, g2, p2, sym_geo, sym_pos)`
correctly — including mirroring about a *line* (not just a point) via
`sym_pos=0`, which FreeCAD's point-index convention (0=edge itself) maps onto
correctly. I didn't realize this tool already covered that case and instead
used FreeCAD's own `SketchObject.addSymmetric()` Python method directly via
`execute_python()`, which turned out to have its own unrelated upstream gap
(the Python binding never forwards the `addSymmetryConstraints` C++ parameter,
so it silently never creates real constraints no matter how it's called —
confirmed via `git blame`/`git log -S` against `FC-clone`, not something to
fix in this repo). Noting it here only so a future session doesn't waste time
suspecting `sketch_operations` itself of that problem too.

#### Resolution (2026-07-27)

Implemented the suggested fix essentially as written, plus item 4:

1. Added an `"expression"` string param to the `sketch_operations` schema in
   `freecad_mcp_server.py`, sibling to `value`.
2. `add_constraint()` (`sketch_ops.py`) now accepts `expression` for the six
   dimensional constraint types (`Distance`, `DistanceX`, `DistanceY`,
   `Radius`, `Diameter`, `Angle`). Exactly one of `value`/`expression` is
   required for those types; if both are given, `value` seeds the literal
   `Sketcher.Constraint(...)` call and `expression` still overrides it via
   `setExpression()` on the next recompute. Passing `expression` for a
   non-dimensional type (e.g. `Horizontal`) is rejected explicitly rather
   than silently ignored.
3. After `sketch.addConstraint(c)`, calls
   `sketch.setExpression(f'Constraints[{idx}]', expression)`, then
   `self.recompute(doc)` — expressions only evaluate on recompute.
4. `add_constraint`'s and `list_constraints`'s DoF reporting now reads
   `sketch.DoF` / `sketch.FullyConstrained` directly instead of treating
   `sketch.solve()`'s return value as a DoF count. `solve()` is still called
   once as a forcing function before reading those properties, but its
   return value is no longer part of the reported message.

Verified live against real FreeCAD 26.3.0 (not just unit tests): created a
spreadsheet with a `PanelLength` alias, bound a `DistanceX` constraint to
`Dimensions.PanelLength / -2` with no literal `value`, confirmed the
constraint resolved to the correct value and `sketch.ExpressionEngine` showed
the binding, then changed the spreadsheet cell and confirmed the constraint
value updated live on recompute. Also verified the value+expression seed path
and the non-dimensional-type rejection live. 5 new unit tests added in
`tests/unit/test_sketch_ops.py` (expression seeding, value+expression
combined, rejection for non-dimensional types, missing-value-and-expression
error, and DoF/FullyConstrained message sourcing).

Found in passing: `reload_modules` failed with `No module named
'universal_selector'`. Investigated and fixed as its own issue — see
"`reload_modules` dispatched off the GUI thread — crashed FreeCAD live" below.

## Server Infrastructure

### Issue: `reload_modules` dispatched off the GUI thread — crashed FreeCAD live

**Status**: FIXED (2026-07-27)
**Severity**: High — reproduced a hard SIGSEGV crash of a live, GUI FreeCAD
session (macOS, ~4h uptime) while investigating a lesser symptom
**Affected**: `AICopilot/freecad_mcp_handler.py`, `_execute_tool_inner()`'s
`reload_modules` dispatch (~line 1145) and `_reload_handlers()` (~line 1479)
**Discovered**: 2026-07-27, while chasing a `reload_modules` failure found
during the issue-48 fix above

#### Symptom #1 (the one that got investigated first): stale deployment

The first `reload_modules` call in this session failed with
`Handler reload failed: No module named 'universal_selector'`, thrown from
`_reload_handlers()`'s final step (reloading `freecad_mcp_handler.py` itself
via `importlib.util.spec_from_file_location` + `exec_module`, which
re-executes its top-level `from universal_selector import UniversalSelector`).

Root cause turned out to be mundane: the FreeCAD instance's actually-loaded
module directory (`FreeCAD-prefs/v26-3/Mod/AICopilot/` — see the corrected
dev-paths note below) was stale relative to this repo — `universal_selector.py`
plus several versions of other changes had never been rsynced there.
`sys.path` and the reload mechanism itself were fine; the file the reload was
trying to `from universal_selector import ...` genuinely did not exist yet at
that path. Confirmed by checking `sys.modules` / `__file__` /  `__version__`
on two long-running GUI instances: both had `freecad_mcp_handler.__version__
== "5.8.0"` in memory (current source is 6.1.0) and no `universal_selector`
in `sys.modules` at all — these processes had been running since well before
`universal_selector` was added, and `reload_modules` had apparently never
once succeeded end-to-end on them since, so none of the intervening deploys
had ever taken live effect. Fixed for this session by rsyncing
`AICopilot/` to `FreeCAD-prefs/v26-3/Mod/AICopilot/` (and the legacy
`Mod/AICopilot/` / `v1-2/Mod/AICopilot/` paths for consistency).

**Corrected dev-paths note**: `CLAUDE.md` documents the actual FreeCAD-side
load path as `FreeCAD-prefs/Mod/AICopilot/` (with `v1-2/Mod/AICopilot/` as a
secondary path for `pixi run freecad-release`). Neither is current — this
FreeCAD 26.3.0 build actually loads from `FreeCAD-prefs/v26-3/Mod/AICopilot/`
(a real directory, not a symlink; `Mod/AICopilot/` is a separate real
directory too, not the same install). `AICopilot/execute_python`'s
`os.path.realpath(module.__file__)` is the reliable way to check which path a
given running instance actually loaded from — don't trust the doc.

#### Symptom #2 (the real bug, found while investigating #1): thread-unsafe dispatch

While manually working around symptom #1 by re-executing
`freecad_mcp_handler.py`'s module code directly via `execute_python` (to
verify the reload path would work once the stale-deploy fix landed), FreeCAD
segfaulted and the process died. Crash report (macOS `.ips`, `SIGSEGV` /
`KERN_INVALID_ADDRESS`): a `QTimer` fired on the GUI thread, hit PySide's
`SignalManager::handleMetaCallError()`, which called `Py_Exit` and began
tearing down the Python interpreter (`Py_FinalizeEx` → `finalize_modules` →
GC) *while a `QPushButton` wrapper was mid-destroy*, crashing in Shiboken's
`BindingManager::getOverride` on a stale pointer.

That specific crash was caused by my own out-of-band `execute_python` call
(re-executing PySide-touching module code from the socket thread, bypassing
GUI-thread marshaling entirely) — but it exposed a real, pre-existing bug:
`_execute_tool_inner()`'s dispatch for `reload_modules` called
`self._reload_handlers()` **directly**, with no GUI-thread marshaling at all:

```python
if tool_name == "reload_modules":
    return self._reload_handlers()   # ran on the socket thread
```

Every other GUI-touching tool in this dispatcher (`_dispatch_to_handler`,
`_call_on_gui_thread_async` for primitives/booleans, etc.) routes through
`_run_on_gui_thread`/`_run_on_gui_thread_async` for exactly the reason the
crash demonstrates: `_reload_handlers()` re-executes `freecad_mcp_handler.py`
itself (touching `PySide`/`QtCore` imports) and rebuilds handler instances
that hold live Qt-bound state (`ViewOpsHandler._clip_planes`' Coin3D
scene-graph nodes, etc.) — doing any of that from the socket thread races the
live Qt event loop. `reload_modules` was the one dispatch entry that skipped
this and called straight through.

#### Fix

`_execute_tool_inner()` now calls a new `_call_on_gui_thread_reload()`
wrapper instead of `_reload_handlers()` directly. The wrapper runs
`_reload_handlers()` via the existing `_run_on_gui_thread` primitive (same
mechanism every other GUI-mutating tool already uses), so it now executes on
the Qt main thread instead of racing it.

One wrinkle: `_reload_handlers()` already returns a complete JSON string
(`{"result": ..., "modules_reloaded": N}` or `{"error": ...}`), unlike the
plain-string returns `_call_on_gui_thread`/`_call_on_gui_thread_async` are
built around — `_run_on_gui_thread`'s generic dict-to-JSON wrapping would
otherwise double-encode it as an escaped string inside another JSON object.
`_call_on_gui_thread_reload()` parses `_reload_handlers()`'s JSON back into a
dict first so the final response nests cleanly (`{"result": {"result": ...,
"modules_reloaded": N}}`) instead of embedding an escaped JSON blob. This is
a minor response-shape change from before (previously flat); nothing in this
repo parses `reload_modules`' output programmatically, so this was accepted
rather than preserving the old flat shape.

Also added `_call_on_gui_thread_reload` to `_reload_handlers()`'s own
self-rebind list (`_dispatch_methods`), so a future `reload_modules` call
picks up edits to the wrapper itself, consistent with how `_reload_handlers`
already rebinds itself there.

3 new unit tests in `tests/unit/test_freecad_mcp_handler.py`
(`TestCallOnGuiThreadReload`): correct nested (non-double-encoded) result
shape, error-JSON passthrough, and exception-instead-of-error-JSON handling.
Existing `test_reload_modules_routing` updated to assert dispatch now goes
through `_call_on_gui_thread_reload()` rather than calling
`_reload_handlers()` directly. Full suite: 1656 passed.

**Not re-verified live against a real GUI FreeCAD crash reproduction** —
given the fix was arrived at by causing a real crash once already this
session, a second live GUI-thread stress test wasn't attempted. The fix is
structurally identical to the pattern every other GUI-touching tool in this
file already uses successfully, and is covered by unit tests for the
JSON-shape logic, but the actual thread-race fix itself is only verified by
code inspection + the pattern match, not by reproducing the crash and
confirming it no longer happens.

## Execute Python

### Issue: `execute_python()` never surfaces FreeCAD Console warnings/errors, only Python stdout

**Status**: NOT FIXED (open) — design decision deferred, not just an
implementation gap
**Severity**: Low-medium — no crash, but real FreeCAD-level warnings
(deprecation notices, recompute warnings, etc.) triggered by code run through
`execute_python()` are silently invisible to the caller unless they separately
call `view_control(operation="get_report_view")` afterward
**Affected**: `AICopilot/handlers/execute_python_ops.py`, `run_code()`
**Discovered**: 2026-07-27, building the Branchline coach side model —
`execute_python()` code set the (deprecated, as of a recent FreeCAD version)
`Midplane` property on several `PartDesign::Pad`/`Pocket` objects. FreeCAD
logged a `PrintWarning`-level deprecation notice each time, but this only
surfaced when the file was later reopened in the GUI and the Report View was
checked by hand — several tool calls earlier, nothing in `execute_python()`'s
response indicated anything had happened.

#### Problem

`run_code()` redirects `sys.stdout` into an `io.StringIO()` buffer and
returns that plus the last expression's value. That only captures output from
the executed code's own `print()` calls. FreeCAD's own logging —
`Console.PrintWarning()`, `PrintError()`, and (more importantly) warnings
emitted internally by FreeCAD's C++ layer as a side effect of property
sets/recomputes (exactly what happened with the `Midplane` deprecation) — goes
through FreeCAD's separate Console/observer system, not Python's `stdout`.
None of it reaches `execute_python()`'s return value. It only becomes visible
via a manual, separate `get_report_view` call.

#### Investigated: no clean Python-level observer-registration API found

Looked for a way to register a Python callback directly against FreeCAD's
console so `run_code()` could capture messages as they're emitted, rather
than needing to diff Report View text. `FreeCAD.Console`'s exposed API
(`src/Base/FreeCAD.Console.module.pyi` in `FC-clone`) only has `Print*()`
output functions plus `SetStatus`/`GetStatus`/`GetObservers` for observers
that are already registered by name (e.g. the GUI Report View widget
registers itself natively in C++) — no `SetObserver`/callback-registration
entry point for arbitrary Python objects turned up in this version's source.

#### Suggested fix (not yet decided on)

Doesn't need that observer API — `get_report_view` already works (used
successfully by hand during the coach-side session). `run_code()` could
snapshot the Report View tail before executing user code, run it, then diff
and fold any new Warning/Error-level lines into the response automatically.
Same underlying mechanism as the manual workaround, just applied proactively.

**Open design question, deliberately left unresolved:** should this live only
in `execute_python()`, or should every mutating tool call pay the same
before/after Report View diff? `execute_python()` is the highest-value target
(arbitrary code, hardest to predict what it'll trigger), but the same class
of silent-warning problem could in principle happen from any handler that
calls into FreeCAD's property/recompute machinery. Deferred rather than
guessed at — needs a real decision, not a quick patch.
