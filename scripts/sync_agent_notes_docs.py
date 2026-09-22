#!/usr/bin/env python3
"""Sync AGENT-INSTALL.md's "Critical Rules" section from mcp-agent-notes
CRITICAL entries. Adapted from kicad-mcp's/jmri-mcp's script of the same
name.

Run before committing when a CRITICAL Note in usage_guidance.py is added,
changed, or removed:

    python scripts/sync_agent_notes_docs.py

CI runs this then checks `git diff --exit-code` (see
.github/workflows/docs-check.yml).

usage_guidance.NOTES is the single authored source for these rules --
AGENT-INSTALL.md's "Critical Rules" section is a generated view of it, not
independently authored, so it can no longer silently drift from what
SERVER_INSTRUCTIONS and get_usage_guidance() actually say. (freecad-mcp has
no separate AGENT-INSTRUCTIONS.md the way kicad-mcp does -- AGENT-INSTALL.md
already plays that combined role here, same as jmri-mcp, so there's only one
target file.)

Markers kept in sync
---------------------
<!-- agent-notes:critical --> ... <!-- /agent-notes:critical -->
"""
import re
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp_agent_notes import Note, Priority  # noqa: E402

from usage_guidance import NOTES  # noqa: E402

MARKER_RE = re.compile(
    r"<!-- agent-notes:critical -->.*?<!-- /agent-notes:critical -->",
    re.DOTALL,
)


def _critical_notes() -> list[Note]:
    return sorted((n for n in NOTES if n.priority is Priority.CRITICAL), key=lambda n: n.id)


def _render_install_block() -> str:
    # No separate bold lead: Note.summary is authored to stand alone (see
    # mcp_agent_notes.Note's docstring). Numbered rather than bulleted to
    # match kicad-mcp's/jmri-mcp's install-block convention.
    notes = _critical_notes()
    lines = [f"{i}. {n.summary}" for i, n in enumerate(notes, start=1)]
    body = "\n".join(lines)
    return f"<!-- agent-notes:critical -->\n{body}\n<!-- /agent-notes:critical -->"


TARGETS: list[tuple[Path, Callable[[], str]]] = [
    (ROOT / "AGENT-INSTALL.md", _render_install_block),
]


def sync_file(path: Path, render: Callable[[], str]) -> bool:
    """Replace the marked block in `path` with `render()`'s output.

    Returns True if the file changed. Warns (doesn't fail) if the markers
    are missing entirely -- likely a forgotten/removed marker pair.
    """
    text = path.read_text(encoding="utf-8")
    if not MARKER_RE.search(text):
        print(f"  WARNING: {path.name} has no <!-- agent-notes:critical --> markers")
        return False
    new = MARKER_RE.sub(lambda _m: render(), text)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def main() -> None:
    changed: list[str] = []
    for path, render in TARGETS:
        if sync_file(path, render):
            changed.append(path.name)
            print(f"  Updated {path.name}")

    if changed:
        print(f"\nUpdated {len(changed)} file(s): {', '.join(changed)}")
        print("Review the changes and commit them.")
    else:
        print("All files already in sync -- nothing to update.")


if __name__ == "__main__":
    main()
