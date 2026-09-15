"""
Surface gate for the handler family: one registry, no hand list anywhere else — and a hot
reload that is serialised whole, adopts the reloaded class, and drops de-listed handlers.

handler_registry.py is the family's one home ({attr_name: class name}). This gate makes a second
home unbuildable and pins the reload's structural guarantees. Discovery keys are intrinsic, so a
newcomer cannot avoid them:
  1. any `class <Name>Handler` defined in a module under handlers/ (BaseHandler is the base, not a
     member) must be claimed by exactly one registry entry whose attr name is that module, with
     the constructor kind the class's own __init__ signature implies (queues -> GUI-sensitive).
  2. freecad_mcp_handler.py must contain NO reference to a registered class name, NO call that
     constructs a `*Handler`, and NO string literal naming a `handlers.<module>` — the three
     shapes hand lists take. Module names are derived by the registry (module_names()).
  3. the reload: ONE with-block on a lock created atomically on the instance, no enumeration of
     method names, nothing resolved through the instance's (old) class, and a snapshot scan in
     the adopter.
The registry is loaded from its file, never through the package, so this runs without FreeCAD.
"""

import ast
import os
import importlib.util

AICOPILOT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))
HANDLERS_DIR = os.path.join(AICOPILOT_DIR, "handlers")
SERVER_FILE = os.path.join(AICOPILOT_DIR, "freecad_mcp_handler.py")


def _registry():
    path = os.path.join(AICOPILOT_DIR, "handler_registry.py")
    spec = importlib.util.spec_from_file_location("handler_registry_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), path)


def _server_class(tree):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FreeCADSocketServer")


def _method(cls, name):
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _init_arg_names(fn):
    """Every parameter name an __init__ declares, whatever its kind.

    ast.arguments carries FIVE parameter sets (positional-only, positional, *args, keyword-only,
    **kwargs).  Reading `args` alone would report a keyword-only `gui_task_queue` as absent, so a
    GUI handler written with `*` would be classed standard and the gate would fail correct code.
    `self` is the first positional, dropped.
    """
    a = fn.args
    names = [p.arg for p in a.posonlyargs] + [p.arg for p in a.args]
    names = names[1:]  # self
    if a.vararg:
        names.append(a.vararg.arg)
    names += [p.arg for p in a.kwonlyargs]
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


def _handler_classes_on_disk(root=HANDLERS_DIR):
    """{class name: (dotted module, init arg names)} for every *Handler class under `root`, RECURSIVELY.

    A handler in a subpackage (handlers/cam/cam_tools.py -> module "cam.cam_tools") is a member like
    any other.  Discovery with os.listdir would see only the top level and read such a handler as a
    registry ghost.
    """
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for f in sorted(filenames):
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            if rel == "__init__.py":
                continue
            parts = rel[:-3].split(os.sep)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            module = ".".join(parts)
            for node in _parse(os.path.join(dirpath, f)).body:
                if isinstance(node, ast.ClassDef) and node.name.endswith("Handler") and node.name != "BaseHandler":
                    init_args = None
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                            init_args = _init_arg_names(item)
                    found[node.name] = (module, init_args)
    return found


_DOTTED_PATH = __import__("re").compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")


def _family_offences(tree, names, modules):
    """Every place a server source re-spells the handler family.  Positive model over the string
    surface: a string constant that carries `handlers.` is allowed ONLY as a whole dotted path that
    names a non-member (`handlers.base`); a member name, a bare prefix fragment (`"handlers."` —
    what `+` concatenation leaves behind), a template (`"handlers.%s"`) or any f-string carrying
    the prefix is an offence.  An f-string that builds `handlers.<x>` never has a legitimate use in
    the server — the registry derives module names — so it is refused whole.
    """
    offences = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            offences.append(f"L{node.lineno}: name {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in names:
            offences.append(f"L{node.lineno}: attribute {node.attr}")
        if isinstance(node, ast.ImportFrom) and node.module == "handlers":
            for a in node.names:
                if a.name in names:
                    offences.append(f"L{node.lineno}: from handlers import {a.name}")
        # A PATH SHAPE, never prose: "Route Part operations across multiple handlers." is a sentence
        # and must not be charged.  A path has no whitespace; a fragment left by concatenation or
        # by an f-string ends in "handlers."; a template carries a placeholder after it.  ONE rule
        # covers all three because ast.walk reaches an f-string's constant parts as ordinary
        # Constant nodes.
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "handlers." in node.value:
            s = node.value
            pathish = not any(ch.isspace() for ch in s)
            if pathish:
                rest = s.split("handlers.", 1)[1]
                if rest == "" or f"handlers.{rest}" in modules or not _DOTTED_PATH.fullmatch(rest):
                    offences.append(f"L{node.lineno}: literal {s!r}")
        if isinstance(node, ast.Call):
            fn = node.func
            callee = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if callee and callee.endswith("Handler") and callee != "BaseHandler":
                offences.append(f"L{node.lineno}: constructs {callee}(...)")
    return offences


# ---------------------------------------------------------------------------
# 1. the registry claims every handler class on disk, once, with the right constructor kind
# ---------------------------------------------------------------------------

def test_every_handler_class_on_disk_is_claimed_once_by_the_registry():
    r = _registry()
    on_disk = _handler_classes_on_disk()
    claimed = {}
    for attr, cls in r._HANDLER_CLASS_NAMES.items():
        assert cls not in claimed, f"{cls} is claimed twice"
        claimed[cls] = attr
    unclaimed = sorted(set(on_disk) - set(claimed))
    assert not unclaimed, f"handler class(es) on disk the registry does not claim: {unclaimed}"
    ghosts = sorted(set(claimed) - set(on_disk))
    assert not ghosts, f"registry entries with no class on disk: {ghosts}"
    for cls, attr in claimed.items():
        assert on_disk[cls][0] == attr, f"{cls}: registry attr {attr} must be its module, disk says {on_disk[cls][0]}"


def test_each_entry_is_gui_sensitive_exactly_when_its_constructor_takes_the_queues():
    """The kind is not a label: it is derived from the class's own __init__ signature, and the
    registry (not the server) says which attrs get the queues."""
    r = _registry()
    on_disk = _handler_classes_on_disk()
    for attr, cls in r._HANDLER_CLASS_NAMES.items():
        args = on_disk[cls][1]
        takes_queues = args is not None and "gui_task_queue" in args
        assert (attr in r._GUI_SENSITIVE) == takes_queues, \
            f"{cls}: __init__ args {args} imply gui-sensitive={takes_queues}, registry says {attr in r._GUI_SENSITIVE}"
    assert r._GUI_SENSITIVE <= set(r._HANDLER_CLASS_NAMES), "a gui-sensitive attr that is not a registered handler"


def test_attribute_names_are_identifier_shaped_and_module_names_derive_from_them():
    r = _registry()
    attrs = list(r._HANDLER_CLASS_NAMES)
    assert all(a.isidentifier() for a in attrs)
    assert r.module_names() == tuple(f"handlers.{a}" for a in attrs)


# ---------------------------------------------------------------------------
# 2. the server carries no hand list of the family
# ---------------------------------------------------------------------------

def test_the_server_carries_no_hand_list_of_the_family():
    r = _registry()
    names = set(r._HANDLER_CLASS_NAMES.values())
    modules = set(r.module_names())
    offences = _family_offences(_parse(SERVER_FILE), names, modules)
    assert not offences, "freecad_mcp_handler.py re-spells the handler family:\n  " + "\n  ".join(offences)


def _offences_of(src, names=("FixtureOpsHandler",), modules=("handlers.fixture_ops",)):
    return _family_offences(ast.parse(src), set(names), set(modules))


def test_the_family_gate_catches_every_dynamic_spelling_and_charges_no_legitimate_one():
    """The gate proven on synthetic sources — both directions, so a green run over the real server
    is a measurement and not a scanner that sees nothing."""
    # LEGITIMATE — the shapes the reload genuinely uses; charging these switches the gate off.
    assert _offences_of("_reload('handlers.base', b)") == []
    assert _offences_of("import handlers as pkg; _reload('handlers', pkg)") == []
    assert _offences_of("for n in registry.module_names(): reload(n)") == []
    # PROSE is not a path.
    assert _offences_of('"""Route Part operations across multiple handlers."""') == []
    assert _offences_of('log(f"Modular handlers required but not available: {e}")') == []
    assert _offences_of('log(f"reloaded {n} handlers.")') == []
    # OFFENCES — every spelling a literal-only check would let through.
    assert _offences_of("x = 'handlers.fixture_ops'")                                  # the whole member name
    assert _offences_of("import_module(f\"handlers.{'fixture_ops'}\")")                # f-string
    assert _offences_of("import_module('handlers.' + name)")                          # prefix fragment via +
    assert _offences_of("import_module('handlers.%s' % name)")                        # template
    assert _offences_of("import_module('handlers.{}'.format(name))")                  # template
    assert _offences_of("import_module(f'AICopilot.handlers.{name}')")               # prefixed f-string
    assert _offences_of("x = 'AICopilot.handlers.fixture_ops'")                       # prefixed member path
    assert _offences_of("h = FixtureOpsHandler(self)")                                # construction
    assert _offences_of("from handlers import FixtureOpsHandler")                     # import


def test_discovery_sees_subpackages_and_keyword_only_constructors(tmp_path):
    """The two blind spots: os.listdir stops at the top level, and `args.args` misses keyword-only
    parameters."""
    (tmp_path / "cam").mkdir()
    (tmp_path / "cam" / "cam_tools.py").write_text(
        "class CAMToolsHandler:\n    def __init__(self, server, *, gui_task_queue, gui_response_queue, log, cap):\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "flat.py").write_text(
        "class FlatHandler:\n    def __init__(self, server, /, log, cap, *rest, **kw):\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "ghost.py").write_text("class GhostHandler: pass\n", encoding="utf-8")
    found = _handler_classes_on_disk(str(tmp_path))
    assert found["CAMToolsHandler"] == ("cam.cam_tools", ["server", "gui_task_queue", "gui_response_queue", "log", "cap"])
    assert found["FlatHandler"] == ("flat", ["server", "log", "cap", "rest", "kw"])
    assert "GhostHandler" not in found


# ---------------------------------------------------------------------------
# 3. the reload: one lock, no enumeration, nothing through self, a snapshot in the adopter
# ---------------------------------------------------------------------------

def test_hot_reload_is_serialised_whole_by_one_lock_the_server_owns():
    """Every client connection runs on its own thread and two clients share this bridge, so two
    `reload_modules` calls can overlap.  An interleaved reload swaps sys.modules and rebuilds the
    handler set half-and-half.  The guarantee is structural and decidable: `_reload_handlers` has
    exactly one statement after its docstring, a `with` whose context is the instance's lock
    created atomically on first use — so no reload step can sit outside it, and an instance built
    before the lock existed (the one a hot reload upgrades) still gets one.  `__init__` does NOT
    create it: one home, its only user."""
    cls = _server_class(_parse(SERVER_FILE))
    fn = _method(cls, "_reload_handlers")
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    assert len(body) == 1 and isinstance(body[0], ast.With), "the reload body must be ONE with-block, nothing outside it"
    ctx = body[0].items[0].context_expr
    # self.__dict__.setdefault("_reload_lock", threading.RLock())
    assert isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Attribute) and ctx.func.attr == "setdefault", \
        "the lock must be created atomically on the instance (dict.setdefault), never read from an attribute __init__ may not have set"
    owner = ctx.func.value
    assert isinstance(owner, ast.Attribute) and owner.attr == "__dict__" and isinstance(owner.value, ast.Name) and owner.value.id == "self"
    assert isinstance(ctx.args[0], ast.Constant) and ctx.args[0].value == "_reload_lock"
    factory = ctx.args[1]
    assert isinstance(factory, ast.Call) and isinstance(factory.func, ast.Attribute) and factory.func.attr == "RLock", \
        "re-entrant: a nested reload call must not deadlock on itself"
    init = _method(cls, "__init__")
    second_home = [
        s for s in ast.walk(init)
        if isinstance(s, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "_reload_lock" for t in s.targets)
    ]
    assert not second_home, "__init__ creating the lock too would be a second home, and would miss instances born before it"


def test_the_reload_enumerates_no_method_names():
    """The reload once carried a hand list of ten method names to rebind and had already missed
    one.  Now the instance adopts the reloaded class, so the class is the register.  The gate keys
    on the SHAPE the defect took — a list or tuple of string literals inside `_reload_handlers` —
    not on any single string that happens to spell a method name."""
    fn = _method(_server_class(_parse(SERVER_FILE)), "_reload_handlers")
    offences = []
    for node in ast.walk(fn):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        ):
            offences.append(f"L{node.lineno}: a list of {len(node.elts)} string(s) — an enumeration")
    assert not offences, "the reload enumerates what the class already declares:\n  " + "\n  ".join(offences)


def test_the_reload_resolves_nothing_through_the_instances_class():
    """MEASURED on a running FreeCAD: a reload that called `self.<method>(...)` after the module
    swap reached the OLD class, which lacked the new method — the second reload failed and the
    instance was left half-upgraded.  After the swap, everything the reload needs comes from the
    freshly loaded module (`new_self`), never from `self`'s class.  Decidable: no call whose
    callee is `self.<name>` anywhere in `_reload_handlers`."""
    tree = _parse(SERVER_FILE)
    fn = _method(_server_class(tree), "_reload_handlers")
    calls_on_self = [
        f"L{n.lineno}: self.{n.func.attr}(...)"
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"
    ]
    assert not calls_on_self, "the reload must not resolve a method through the instance's (old) class:\n  " + "\n  ".join(calls_on_self)
    # And the adopter is a MODULE-level function, so it exists on the new module regardless of the class.
    assert any(isinstance(n, ast.FunctionDef) and n.name == "_adopt_reloaded_class" for n in tree.body), \
        "_adopt_reloaded_class must be module-level: reachable through new_self on a live upgrade"


SNAPSHOT_CTORS = frozenset({"list", "tuple", "dict", "set", "frozenset"})
DICT_VIEWS = frozenset({"items", "keys", "values"})


def _vars_uses(fn):
    """Every call to the builtin `vars` in `fn`, and which of them are NOT snapshotted.

    POSITIVE MODEL over a CLOSED set.  The ways a live dict can be iterated are an open set (a
    comprehension, a `for`, an assignment, `iter()` inside a `while`, ...); the set of `vars(...)`
    calls is closed: every one is a node.  So the rule is what each call MUST be, not what it must
    not: the direct argument of a snapshot constructor (`list(vars(o))`), optionally through one
    dict view (`list(vars(o).items())`).  Anything else is unclaimed and fails.
    """
    parent = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    uses, unsnapshotted = [], []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars"):
            continue
        uses.append(node)
        outer = parent.get(node)
        # optionally: vars(o).items()  ->  Attribute(vars(o), items) under Call(...)
        if isinstance(outer, ast.Attribute) and outer.attr in DICT_VIEWS:
            view_call = parent.get(outer)
            outer = parent.get(view_call) if isinstance(view_call, ast.Call) and view_call.func is outer else None
        snapshotted = (
            isinstance(outer, ast.Call)
            and isinstance(outer.func, ast.Name) and outer.func.id in SNAPSHOT_CTORS
            and len(outer.args) == 1 and not outer.keywords
        )
        if not snapshotted:
            unsnapshotted.append(node)
    return uses, unsnapshotted


def test_the_adopter_scans_a_snapshot_of_the_instance_dict():
    """Other threads write to the instance while the reload runs (a job result, a stale-request
    id).  Iterating the live `__dict__` under a concurrent insert raises "dictionary changed size
    during iteration" — an unhandled crash halfway through a reload.  A thread race is not a
    deterministic control, so the property is asserted structurally over the closed set of
    `vars(...)` calls: each must be snapshotted at the call."""
    tree = _parse(SERVER_FILE)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_adopt_reloaded_class")
    uses, unsnapshotted = _vars_uses(fn)
    assert uses, "the adopter must scan the instance's attributes with vars(...)"
    assert not unsnapshotted, "vars(...) escapes its snapshot at " + \
        ", ".join(f"L{n.lineno}" for n in unsnapshotted) + \
        "; the only permitted use is as the direct argument of list/tuple/dict/set, optionally through .items()/.keys()/.values()"


def test_the_snapshot_predicate_claims_every_use_of_vars_by_shape():
    """Both directions on synthetic sources: the escapes (a bare loop, an assignment, iter() in a
    while, a second argument), a decoy variable spelled like the builtin, and the legitimate shapes."""
    def fn_of(src):
        return ast.parse(src).body[0]
    def verdict(src):
        uses, bad = _vars_uses(fn_of(src))
        return (len(uses), len(bad))
    # LEGITIMATE
    assert verdict("def f(o):\n    return [k for k, v in list(vars(o).items())]\n") == (1, 0)
    assert verdict("def f(o):\n    for k in tuple(vars(o)):\n        pass\n") == (1, 0)
    # ESCAPES — every one is (1 use, 1 unsnapshotted)
    assert verdict("def f(o):\n    return [k for k, v in vars(o).items()]\n") == (1, 1)
    assert verdict("def f(o):\n    for k in vars(o):\n        pass\n") == (1, 1)
    assert verdict("def f(o):\n    attrs = vars(o)\n    for k in list(attrs):\n        pass\n") == (1, 1)   # assigned first
    assert verdict("def f(o):\n    it = iter(vars(o))\n    while True:\n        next(it)\n") == (1, 1)     # manual iteration
    assert verdict("def f(o):\n    return list(vars(o).items(), )\n") == (1, 0)
    assert verdict("def f(o):\n    return list(vars(o), extra)\n") == (1, 1)                                # not the sole argument
    # DECOY — not a use at all, so the liveness half ("must scan") is what fails
    assert verdict("def f(o):\n    other_vars = [1]\n    for _ in list(other_vars):\n        pass\n") == (0, 0)
