"""
Surface gate: every route from AICopilot's own source to a Document write is CLAIMED, shape by shape.

Why this exists (measured 2026-09-14): the addon auto-saved the active document before every
`execute_python` call and before every boolean op — two hand-written copies of one policy,
neither switchable, and one of them rewrote a version-controlled CAD master three times in two
minutes.  The policy now has ONE home, `handlers.base.autosave_before`.

WHY THIS IS A POSITIVE MODEL AND NOT A LIST OF SUSPICIOUS SPELLINGS.  Three review rounds each
exhibited one more spelling that reached `doc.save` past the previous scanner (an alias, a
constant `getattr`, a bound `__getattribute__`, a qualified `builtins.exec`, a dispatcher fetched
by `getattr`, a string injected inside an already-registered `exec`).  A gate that enumerates
spellings is a denylist over an open set and cannot converge.  The rule: invert.  This gate
enumerates the CLOSED sets the language offers for reaching a method without naming it —
Python's reflection surface — and refuses any use of them, plus any direct reference, that is not
claimed by the register with its EXACT SHAPE.

The surface (each key is intrinsic to the form; none is a declaration an author could omit):
  A. any ATTRIBUTE REFERENCE `.save` / `.saveAs` / `.saveCopy`, on any receiver, call or not;
  B. any use of a REFLECTION capability that can reach an attribute or run text — as a call or a
     bare reference (an alias): `getattr` with a NON-literal name or with a literal save name,
     `__getattribute__`, `__getattr__`, `exec`, `eval`, `compile`, `vars`, `__import__`,
     `import_module`, the `builtins` module, and `__dict__` access;
  C. any assignment whose right-hand side is one of the names in B, or of an earlier alias in
     the same function (a finite closure over that function's assignments) — an alias is a site;
  D. any reference to a GUI command dispatcher (`runCommand`, `SendMsgToActiveView`, `doCommand`)
     on any receiver, call or not, and any call to one whose command is not a string literal;
  E. any string literal equal to a save attribute name or a save GUI command (`Std_Save*`,
     `Save`, `SaveAs`, `SaveAll`, `SaveCopy`), wherever it appears, and any `+` of two string
     literals (an assembled literal).
  This uses the standard-library parser — the canonical one for Python syntax.

THE BOUND, STATED: an f-string, a name read from data, or a value computed at runtime is outside
static reach and is NOT claimed to be caught.  Everything in A–E is.

Register semantics: an entry claims a function and the EXACT multiset of site shapes inside it
(the unparsed source of each site).  A new site, a removed site, or a changed site inside a
registered function is red until the register says why.  An unrequested save is never a new
entry — it is a call to `autosave_before`.
"""

import ast
import os

AICOPILOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

DOCUMENT_SAVE_ATTRS = frozenset({"save", "saveAs", "saveCopy"})
REFLECTION = frozenset({
    "getattr", "__getattribute__", "__getattr__", "exec", "eval", "compile", "vars",
    "__import__", "import_module", "builtins", "__dict__",
})
# The BUILTINS among the reflection names.  Reached as an attribute, they are the builtin only on
# the `builtins` module: `re.compile(pattern)` and `expr.eval()` are those objects' own methods
# and can run no text (measured on upstream 7.5.0: crash_watcher.py and spreadsheet_ops.py compile
# regexes at module scope).  Every dunder, and `import_module`, is reflection on any receiver.
BUILTIN_ONLY = frozenset({"exec", "eval", "compile", "vars"})
GUI_COMMAND_FUNCS = frozenset({"runCommand", "SendMsgToActiveView", "doCommand"})
GUI_SAVE_PREFIX = "Std_Save"
GUI_SAVE_MESSAGES = frozenset({"Save", "SaveAs", "SaveAll", "SaveCopy"})


def _is_save_literal(value):
    return value in DOCUMENT_SAVE_ATTRS or value.startswith(GUI_SAVE_PREFIX) or value in GUI_SAVE_MESSAGES


def _walk_sources(root):
    for dp, _dn, fn in os.walk(root):
        if "__pycache__" in dp:
            continue
        for f in sorted(fn):
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def _name_of(node):
    """The bare name a Name or Attribute node refers to, else None.

    A builtin-only reflection name reached as an ATTRIBUTE is that receiver's own method unless
    the receiver is the `builtins` module, so it is not reported as the builtin."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        if node.attr in BUILTIN_ONLY and _name_of(node.value) != "builtins":
            return None
        return node.attr
    return None


def _scope_nodes(root):
    """Every node in `root`'s OWN scope: nested function and class definitions are not entered
    (they are scanned under their own qualname); lambdas are, since they share the qualname."""
    out = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            out.append(child)
            visit(child)

    visit(root)
    return out


def _sites_in_function(fn_node):
    """Every site inside ONE function body (nested functions are their own scope and are
    visited separately by the caller).  Returns [(lineno, kind, shape)]."""
    sites = []
    aliases = set()
    scope = _scope_nodes(fn_node)
    callee_nodes = {id(n.func) for n in scope if isinstance(n, ast.Call)}

    def add(n, kind):
        sites.append((n.lineno, kind, ast.unparse(n)))

    def is_reflective_name(n):
        nm = _name_of(n)
        return nm in REFLECTION or (isinstance(n, ast.Name) and n.id in aliases)

    # Alias closure first: an assignment from a reflective name or a dispatcher reference or an
    # earlier alias makes the target an alias.  Finite: one pass per assignment in source order,
    # repeated until no new alias appears.
    assigns = [n for n in scope if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr))]
    changed = True
    while changed:
        changed = False
        for n in assigns:
            rhs = n.value
            if rhs is None:
                continue
            rhs_name = _name_of(rhs)
            reflective = is_reflective_name(rhs) or rhs_name in GUI_COMMAND_FUNCS or rhs_name in DOCUMENT_SAVE_ATTRS
            if not reflective:
                continue
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id not in aliases:
                    aliases.add(t.id)
                    changed = True

    for n in scope:
        # A. direct attribute reference to a write
        if isinstance(n, ast.Attribute) and n.attr in DOCUMENT_SAVE_ATTRS:
            add(n, "write-attribute")
        # D. dispatcher reference (call or alias)
        if isinstance(n, ast.Attribute) and n.attr in GUI_COMMAND_FUNCS:
            add(n, "gui-dispatcher")
        # B. reflection capability, as a call or a bare reference
        if isinstance(n, ast.Call):
            callee = _name_of(n.func)
            if callee == "getattr" or (isinstance(n.func, ast.Name) and n.func.id in aliases):
                # getattr with a literal, non-save name is ordinary attribute access — benign
                name_arg = n.args[1] if len(n.args) >= 2 else None
                benign = (callee == "getattr" and isinstance(name_arg, ast.Constant)
                          and isinstance(name_arg.value, str) and not _is_save_literal(name_arg.value))
                if not benign:
                    add(n, "reflection-call")
            elif callee in REFLECTION and callee != "getattr":
                # __getattribute__/__getattr__ take the name as the FIRST argument; the others are
                # unconditional (exec, eval, compile, vars, __import__, import_module)
                add(n, "reflection-call")
            elif isinstance(n.func, ast.Attribute) and n.func.attr in GUI_COMMAND_FUNCS and n.args:
                a = n.args[0]
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                    add(n, "dynamic-gui-command")
        elif isinstance(n, (ast.Name, ast.Attribute)) and _name_of(n) in REFLECTION and _name_of(n) != "getattr" \
                and id(n) not in callee_nodes:
            # a bare reference to exec/eval/builtins/... that is NOT the callee of a call — an
            # alias source or an argument handed elsewhere
            add(n, "reflection-reference")
        # C. alias creation is itself a site
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id in aliases for t in targets):
                add(n, "alias")
        # E. literals
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _is_save_literal(n.value):
            add(n, "save-literal")
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) \
                and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str) \
                and isinstance(n.right, ast.Constant) and isinstance(n.right.value, str):
            add(n, "assembled-literal")
    # de-duplicate: an Attribute that is both a call target and a reference is one site
    seen = set()
    out = []
    for s in sorted(sites):
        key = (s[0], s[2])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _functions(tree):
    """Yield (qualname, node) for every function, including nested ones, with a '<module>' entry
    for module-level statements."""
    stack = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.append(child.name)
                yield ".".join(stack), child
                yield from visit(child)
                stack.pop()
            elif isinstance(child, ast.ClassDef):
                stack.append(child.name)
                yield from visit(child)
                stack.pop()
            else:
                yield from visit(child)

    yield from visit(tree)


def observed(root=None):
    """{(relpath, qualname): [(lineno, kind, shape)]} over every site on the surface."""
    root = root or AICOPILOT_DIR
    result = {}
    for path in _walk_sources(root):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), path)
        for qual, fn in _functions(tree):
            s = _sites_in_function(fn)          # scope-aware: nested defs are not entered
            if s:
                result[(rel, qual)] = s
        m = _sites_in_function(tree)            # module scope: functions and classes not entered
        if m:
            result[(rel, "<module>")] = m
    return result


# (relative path, qualname) -> {"why": ..., "shapes": the exact site shapes inside that function}
#
# THREE CLASSES OF CLAIM, and every entry says which it is:
#   chokepoint  — the one unrequested write, gated by the preference
#   explicit    — the caller asked for this act by name (a tool, a flag)
#   dispatch    — reflection or a GUI dispatcher used to ROUTE, never to write; the shape pins
#                 what it routes to, so a routed write cannot appear without a red gate
SAVE_REGISTER = {
    ("handlers/base.py", "_autosave"): {
        "why": "chokepoint: the only unrequested save, after the AutoSaveBeforeRiskyOp preference",
        "shapes": ["doc.save"],
    },
    ("handlers/base.py", "_report"): {
        "why": "dispatch: the Report View level (PrintMessage/PrintWarning/PrintError/PrintLog) is chosen by name; the receiver is FreeCAD.Console, never a Document",
        "shapes": ["getattr(FreeCAD.Console, level)"],
    },
    ("handlers/document_ops.py", "DocumentOpsHandler.save_document"): {
        "why": "explicit: the save_document tool — saveAs when a filename is given, save otherwise",
        "shapes": ["doc.saveAs", "doc.save"],
    },
    ("handlers/diagnostics_ops.py", "DiagnosticsOpsHandler.restart_freecad"): {
        "why": "explicit: restart_freecad(save_documents=...) — the caller chose it per call",
        "shapes": ["doc.save"],
    },
    ("handlers/execute_python_ops.py", "ExecutePythonOpsHandler.run_code"): {
        "why": "explicit: the execute_python tool runs the CALLER'S code — statements, the trailing expression, or the whole module",
        "shapes": [
            "compile(exec_module, '<string>', 'exec')",
            "exec(compile(exec_module, '<string>', 'exec'), namespace)",
            "compile(expr_ast, '<string>', 'eval')",
            "eval(compile(expr_ast, '<string>', 'eval'), namespace)",
            "exec(code, namespace)",
        ],
    },
    ("handlers/macro_ops.py", "MacroOpsHandler.run"): {
        "why": "explicit: the macro tool compiles and runs the macro FILE the caller names",
        "shapes": ["compile(source, path, 'exec')", "exec(code_obj, namespace)"],
    },
    ("handlers/document_ops.py", "DocumentOpsHandler.get_object_properties"): {
        "why": "dispatch: reads a property the caller names off a document object; a read, never a call",
        "shapes": ["getattr(obj, prop_name)"],
    },
    ("freecad_mcp_handler.py", "FreeCADSocketServer._dispatch_to_handler"): {
        "why": "dispatch: routes a tool operation to the handler method of the same name",
        "shapes": ["getattr(handler, operation, None)"],
    },
    ("freecad_mcp_handler.py", "FreeCADSocketServer._dispatch_part_operations"): {
        "why": "dispatch: routes part operations to create_*/…_objects/…_object/part_ops methods",
        "shapes": [
            "getattr(self.primitives, f'create_{operation}', None)",
            "getattr(self.boolean_ops, f'{operation}_objects', None)",
            "getattr(self.transforms, f'{operation}_object', None)",
            "getattr(self.part_ops, operation, None)",
        ],
    },
    ("freecad_mcp_handler.py", "FreeCADSocketServer._reload_handlers"): {
        "why": "dispatch: rebinds the hand-listed dispatch methods from the reloaded module (a class lookup, never a Document)",
        "shapes": ["getattr(new_self.FreeCADSocketServer, method_name, None)"],
    },
    ("freecad_mcp_handler.py", "_build_handler_class_map"): {
        "why": "dispatch: resolves handler_registry's class NAMES against the handlers package; a class lookup, never a Document",
        "shapes": ["getattr(handlers_module, cls_name)"],
    },
    ("freecad_debug.py", "FreeCADDebugger._capture_object_state"): {
        "why": "dispatch: the diagnostic snapshot reads every property of an object by name; a read",
        "shapes": ["getattr(obj, prop_name)"],
    },
    ("handlers/assembly_ops.py", "AssemblyOpsHandler._validate_element"): {
        "why": "dispatch: reads the Faces/Edges/Vertexes collection named by the element prefix to bounds-check an index; a read",
        "shapes": ["getattr(obj.Shape, collection_attr, [])"],
    },
    ("handlers/assembly_ops.py", "AssemblyOpsHandler.get_part_status"): {
        "why": "dispatch: reads Reference1/Reference2 (a literal tuple) off each joint; a read",
        "shapes": ["getattr(j, ref_attr, None)"],
    },
    ("handlers/assembly_ops.py", "AssemblyOpsHandler.list_joints"): {
        "why": "dispatch: reads Detach1/Detach2 and the literal Enable*/Length*/Angle* limit pairs off each joint; reads",
        "shapes": [
            "getattr(j, f'Detach{connector}', False)",
            "getattr(j, enable_attr, False)",
            "getattr(j, value_attr, '?')",
        ],
    },
    ("handlers/assembly_ops.py", "AssemblyOpsHandler.set_joint_limits._effective"): {
        "why": "dispatch: reads the literal Enable*/value limit pair off the joint to validate a range; reads",
        "shapes": ["getattr(joint, enable_attr, False)", "getattr(joint, value_attr, None)"],
    },
    ("handlers/cam_ops.py", "CAMOpsHandler.get_operation"): {
        "why": "dispatch: reads operation-specific parameters from a literal tuple of names; a read",
        "shapes": ["getattr(operation, prop)"],
    },
    ("handlers/cam_ops.py", "CAMOpsHandler.simulate_job"): {
        "why": "dispatch: cmd is 'CAM_SimulatorGL' or 'CAM_Simulator' (conditional on use_gl); not a save",
        "shapes": ["FreeCADGui.runCommand", "FreeCADGui.runCommand(cmd, 0)"],
    },
    ("handlers/cam_tool_controllers.py", "CAMToolControllersHandler.get_tool_controller"): {
        "why": "dispatch: reads feed/rapid properties from a literal tuple of names; a read",
        "shapes": ["getattr(controller, prop)"],
    },
    ("handlers/cam_tools.py", "CAMToolsHandler.get_tool"): {
        "why": "dispatch: reads material/geometry properties from a literal tuple of names; a read",
        "shapes": ["getattr(tool, prop)"],
    },
    ("handlers/introspection_ops.py", "_resolve_path"): {
        "why": "dispatch: resolves a dotted path the caller names (introspection reads, never calls)",
        "shapes": ["__import__(head)", "getattr(obj, attr)"],
    },
    ("handlers/introspection_ops.py", "_collect_names.walk"): {
        "why": "dispatch: introspection walk reads attributes by name",
        "shapes": ["getattr(obj, name)"],
    },
    ("handlers/introspection_ops.py", "IntrospectionOpsHandler.inspect"): {
        "why": "dispatch: introspection reads an attribute by name",
        "shapes": ["getattr(obj, name)"],
    },
    ("handlers/introspection_ops.py", "IntrospectionOpsHandler.search"): {
        "why": "dispatch: introspection imports a module the caller names (through the allowlist) to search it",
        "shapes": ["__import__(mod_name)"],
    },
    ("handlers/varset_ops.py", "VarSetOpsHandler.get_property"): {
        "why": "dispatch: reads the VarSet property the caller names (checked against PropertiesList first); a read",
        "shapes": ["getattr(varset, name)"],
    },
    ("handlers/varset_ops.py", "VarSetOpsHandler.list_properties"): {
        "why": "dispatch: reads each dynamic VarSet property by name for the listing; a read",
        "shapes": ["getattr(varset, name)"],
    },
    ("handlers/view_ops.py", "ViewOpsHandler.set_view"): {
        "why": "dispatch: views[view_type] is drawn from a literal dict of Std_View* commands; not a save",
        "shapes": ["FreeCADGui.runCommand", "FreeCADGui.runCommand(views[view_type], 0)"],
    },
    ("handlers/view_ops.py", "ViewOpsHandler.fit_all"): {
        "why": "dispatch: ViewFit only (a literal)",
        "shapes": ["FreeCADGui.SendMsgToActiveView"],
    },
}


def _claims():
    return {k: sorted(v["shapes"]) for k, v in SAVE_REGISTER.items()}


def test_every_site_is_claimed_with_its_exact_shape():
    obs = observed()
    claims = _claims()
    problems = []
    for key, sites in sorted(obs.items()):
        shapes = sorted(s[2] for s in sites)
        if key not in claims:
            problems.append(f"UNCLAIMED {key[0]}::{key[1]}:\n" + "\n".join(f"      L{l} [{k}] {sh}" for l, k, sh in sites))
        elif shapes != claims[key]:
            problems.append(f"DRIFT {key[0]}::{key[1]}: claimed {claims[key]} found {shapes}")
    for key in claims:
        if key not in obs:
            problems.append(f"DEAD {key[0]}::{key[1]}: registered but no site found")
    assert not problems, (
        "Every route to a Document write must be claimed with its exact shape "
        "(an unrequested save is a call to handlers.base.autosave_before, never a new entry):\n  "
        + "\n  ".join(problems)
    )


def test_the_scanner_sees_every_enumerated_form(tmp_path):
    """The keys proven on a synthetic module: the forms three review rounds exhibited, plus
    benign uses that must stay quiet (liveness)."""
    src = tmp_path / "AICopilot"
    src.mkdir()
    (src / "probe.py").write_text(
        "import builtins\n"
        "def direct(doc):\n    doc.save()\n"
        "def alias(doc):\n    f = doc.save\n    f()\n"
        "def via_getattr(doc):\n    getattr(doc, 'save')()\n"
        "def bound(doc):\n    doc.__getattribute__('save')()\n"
        "def aliased_getattr(doc):\n    fetch = getattr\n    fetch(doc, 'save')()\n"
        "def folded(doc):\n    getattr(doc, 'sa' + 've')()\n"
        "def qualified_exec():\n    builtins.exec('x')\n"
        "def aliased_exec(code):\n    run = exec\n    run(code)\n"
        "def chained_alias(code):\n    a = exec\n    b = a\n    b(code)\n"
        "def indirect_gui():\n    dispatch = getattr(FreeCADGui, 'runCommand')\n    dispatch('Std_Save')\n"
        "def gui_alias():\n    d = FreeCADGui.runCommand\n    d('Std_SaveAll')\n"
        "def dyn(cmd):\n    FreeCADGui.runCommand(cmd, 0)\n"
        "def literal_only():\n    return 'saveAs'\n"
        "def benign(doc):\n    x = getattr(doc, 'FileName', '')\n    FreeCADGui.activateWorkbench('X')\n    return x\n"
        "def own_method(expr):\n    import re\n    pat = re.compile('x')\n    return expr.eval(), pat\n"
        "def quiet():\n    FreeCADGui.runCommand('Std_Redo')\n",
        encoding="utf-8",
    )
    obs = observed(str(src))
    by_fn = {q: [k for _l, k, _s in v] for (_r, q), v in obs.items()}
    for fn in ("direct", "alias", "via_getattr", "bound", "aliased_getattr", "folded", "qualified_exec",
               "aliased_exec", "chained_alias", "indirect_gui", "gui_alias", "dyn", "literal_only"):
        assert fn in by_fn, f"{fn} produced no site: {by_fn}"
    assert "benign" not in by_fn, f"benign reflection must stay quiet: {by_fn.get('benign')}"
    assert "own_method" not in by_fn, f"an object's own compile/eval is not the builtin: {by_fn.get('own_method')}"
    # `quiet` references the dispatcher — a reference IS a site under key D, so it is claimed
    # explicitly wherever it occurs in the real tree; here it only proves the key fires.
    assert "quiet" in by_fn


def test_a_content_change_inside_a_registered_site_is_red(tmp_path):
    """The shape pin: the same function, the same count, different bytes -> red."""
    src = tmp_path / "AICopilot"
    src.mkdir()
    (src / "m.py").write_text("def run(code, ns):\n    exec(code, ns)\n", encoding="utf-8")
    before = observed(str(src))[("m.py", "run")]
    (src / "m.py").write_text("def run(code, ns):\n    exec('a.save(); ' + code, ns)\n", encoding="utf-8")
    after = observed(str(src))[("m.py", "run")]
    assert sorted(s[2] for s in before) != sorted(s[2] for s in after)
