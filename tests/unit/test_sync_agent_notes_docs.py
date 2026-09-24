"""Tests for scripts/sync_agent_notes_docs.py.

Background (full-review 2026-09-23, Medium finding #61): this script had
0% coverage -- it was never even imported by any test. It's a small,
self-contained CI/docs-sync utility (regenerates AGENT-INSTALL.md's
"Critical Rules" block from usage_guidance.NOTES), so these tests cover
its pure logic (marker regex, block rendering, file sync) directly rather
than exercising the real repo files main() operates on.
"""

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_agent_notes_docs.py"


@pytest.fixture
def sync_module(monkeypatch):
    """Import sync_agent_notes_docs.py fresh, with usage_guidance.NOTES
    replaced by a small, deterministic fixture set (real NOTES content
    changes over time and isn't this test's concern -- only the script's
    own logic is)."""
    from mcp_agent_notes import Note, NoteKind, Priority

    fixture_notes = (
        Note(
            id="z-note", added=date(2026, 1, 1), priority=Priority.CRITICAL,
            kind=NoteKind.TACTIC, summary="Z comes first alphabetically but sorts by id.",
        ),
        Note(
            id="a-note", added=date(2026, 1, 2), priority=Priority.HIGH,
            kind=NoteKind.TACTIC, summary="A HIGH-priority note, must NOT appear in critical output.",
        ),
        Note(
            id="m-note", added=date(2026, 1, 3), priority=Priority.CRITICAL,
            kind=NoteKind.STRATEGY, summary="M is CRITICAL and must appear, sorted after z-note by id.",
        ),
    )
    monkeypatch.setattr("usage_guidance.NOTES", fixture_notes, raising=False)

    spec = importlib.util.spec_from_file_location("sync_agent_notes_docs", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_agent_notes_docs"] = mod
    spec.loader.exec_module(mod)
    # exec_module already imported the real usage_guidance at module load
    # time (before our monkeypatch could apply to the script's own bound
    # name) -- rebind NOTES directly on the freshly-loaded module so
    # _critical_notes() sees the fixture set.
    mod.NOTES = fixture_notes
    yield mod
    del sys.modules["sync_agent_notes_docs"]


class TestCriticalNotes:
    def test_filters_to_critical_priority_only(self, sync_module):
        notes = sync_module._critical_notes()
        assert all(n.priority.name == "CRITICAL" for n in notes)
        assert len(notes) == 2  # z-note, m-note (a-note is HIGH, excluded)

    def test_sorted_by_id_not_insertion_order(self, sync_module):
        # Fixture order is z-note, a-note, m-note -- critical output must
        # be sorted by id ("m-note" < "z-note" alphabetically), not left
        # in NOTES' own insertion order.
        notes = sync_module._critical_notes()
        assert [n.id for n in notes] == ["m-note", "z-note"]

    def test_empty_when_no_critical_notes(self, sync_module, monkeypatch):
        from mcp_agent_notes import Note, NoteKind, Priority
        non_critical = (
            Note(id="x", added=date(2026, 1, 1), priority=Priority.LOW,
                 kind=NoteKind.TACTIC, summary="not critical"),
        )
        sync_module.NOTES = non_critical
        assert sync_module._critical_notes() == []


class TestRenderInstallBlock:
    def test_renders_numbered_list_with_markers(self, sync_module):
        block = sync_module._render_install_block()
        assert block.startswith("<!-- agent-notes:critical -->\n")
        assert block.endswith("\n<!-- /agent-notes:critical -->")
        assert "1. M is CRITICAL and must appear, sorted after z-note by id." in block
        assert "2. Z comes first alphabetically but sorts by id." in block

    def test_high_priority_note_excluded_from_rendered_block(self, sync_module):
        block = sync_module._render_install_block()
        assert "HIGH-priority note" not in block

    def test_empty_critical_set_renders_empty_body_between_markers(self, sync_module):
        sync_module.NOTES = ()
        block = sync_module._render_install_block()
        assert block == "<!-- agent-notes:critical -->\n\n<!-- /agent-notes:critical -->"


class TestMarkerRegex:
    def test_matches_multiline_content_between_markers(self, sync_module):
        text = "before\n<!-- agent-notes:critical -->\n1. old\n2. stale\n<!-- /agent-notes:critical -->\nafter"
        m = sync_module.MARKER_RE.search(text)
        assert m is not None
        assert "1. old" in m.group(0)

    def test_no_match_when_markers_absent(self, sync_module):
        assert sync_module.MARKER_RE.search("no markers here at all") is None


class TestSyncFile:
    def test_replaces_marked_block_and_reports_changed(self, sync_module, tmp_path):
        f = tmp_path / "TARGET.md"
        f.write_text(
            "# Doc\n\n<!-- agent-notes:critical -->\nstale content\n<!-- /agent-notes:critical -->\n",
            encoding="utf-8",
        )
        changed = sync_module.sync_file(f, sync_module._render_install_block)
        assert changed is True
        new_text = f.read_text(encoding="utf-8")
        assert "stale content" not in new_text
        assert "M is CRITICAL and must appear" in new_text

    def test_already_in_sync_reports_unchanged(self, sync_module, tmp_path):
        f = tmp_path / "TARGET.md"
        block = sync_module._render_install_block()
        f.write_text(f"# Doc\n\n{block}\n", encoding="utf-8")
        changed = sync_module.sync_file(f, sync_module._render_install_block)
        assert changed is False
        # File content must be byte-identical, not just "logically the
        # same" -- a spurious rewrite would create a no-op git diff churn.
        assert f.read_text(encoding="utf-8") == f"# Doc\n\n{block}\n"

    def test_missing_markers_warns_and_reports_unchanged(self, sync_module, tmp_path, capsys):
        f = tmp_path / "TARGET.md"
        original = "# Doc\n\nno markers in this file\n"
        f.write_text(original, encoding="utf-8")
        changed = sync_module.sync_file(f, sync_module._render_install_block)
        assert changed is False
        assert f.read_text(encoding="utf-8") == original
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "no <!-- agent-notes:critical -->" in captured.out


class TestRealAgentInstallFile:
    """The script's actual TARGETS entry points at the real
    AGENT-INSTALL.md -- confirm it has the markers this script depends on,
    so a removed marker pair (the one failure mode sync_file itself can
    only warn about, not fail on) doesn't go unnoticed."""

    def test_agent_install_md_has_critical_markers(self):
        path = REPO_ROOT / "AGENT-INSTALL.md"
        text = path.read_text(encoding="utf-8")
        assert "<!-- agent-notes:critical -->" in text
        assert "<!-- /agent-notes:critical -->" in text
