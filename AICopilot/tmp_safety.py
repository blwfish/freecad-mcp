"""Shared helper for creating/using /tmp-rooted paths without silently
following a pre-planted symlink.

/tmp is world-writable, and the debug/crash/last-op paths in this
codebase (freecad_debug.py, freecad_health.py, crash_watcher.py) use
fixed, predictable names — an attacker with local shell access could
plant a symlink at one of those paths before this process starts. Naive
mkdir(exist_ok=True)/open(path, "w") both silently follow an existing
symlink (os.path.isdir()/open() resolve symlinks by design), which would
redirect debug/crash writes to wherever the attacker pointed it.

This module is imported by three files that all run inside the same
FreeCAD process (AICopilot/) — it is NOT shared with the bridge process
(freecad_mcp_server.py), which is a separate OS process with its own
install and cannot import from AICopilot/.
"""

import os


def refuse_if_symlink(path: str) -> None:
    """Raise if path already exists as a symlink.

    Call before the first create-or-write through a fixed, predictable
    /tmp path in this process. Does not fully eliminate the TOCTOU window
    between this check and the subsequent write (a perfect fix would need
    an atomic O_NOFOLLOW-style directory open, which mkdir/pathlib don't
    expose) — this is a defensive check appropriate to a local-attacker,
    not a fully race-proof one.
    """
    if os.path.islink(path):
        raise RuntimeError(
            f"Refusing to use {path}: it already exists as a symlink, "
            "which could redirect writes to an unintended location."
        )


def safe_mkdir(path: str, mode: int = 0o700) -> None:
    """os.makedirs(path, exist_ok=True) that refuses to silently follow a
    pre-existing symlink at `path` first.

    exist_ok=True's own "does this already exist" check is os.path.isdir(),
    which follows symlinks by design — so a symlinked path would otherwise
    be silently accepted as "already exists, fine" without this check.
    """
    refuse_if_symlink(path)
    os.makedirs(path, mode=mode, exist_ok=True)
