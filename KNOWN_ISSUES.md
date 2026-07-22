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
