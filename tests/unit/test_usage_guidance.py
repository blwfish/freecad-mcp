"""Tests for usage_guidance.py's NOTES content and query() dispatch.

This is a thin wire-up of mcp-agent-notes into freecad-mcp's raw-SDK
dispatch idiom (mirroring kicad-mcp's and jmri-mcp's test_usage_guidance.py)
-- rendering/ranking logic itself is tested upstream in that package. These
tests cover: NOTES content (the gotchas previously hand-maintained in the
inline get_usage_guidance dict and in CLAUDE.md's Mandatory Rules survive
the migration into structured data), and query()'s own dispatch (operation
validation, argument plumbing). Protocol-level wiring (tool schema,
InitializeResult.instructions) is covered in test_mcp_protocol.py instead.
"""

import os
import sys

from mcp_agent_notes import NoteKind, Priority

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from usage_guidance import NOTES, query


class TestDispatch:
    def test_default_operation_is_strategy(self):
        assert query() == query(operation="strategy")

    def test_strategy_no_topic(self):
        result = query("strategy")
        assert isinstance(result, str) and result

    def test_tactics_no_topic_lists_topics(self):
        result = query("tactics")
        assert "measurement" in result

    def test_tactics_with_topic(self):
        result = query("tactics", topic="undo")
        assert "checkpoint" in result.lower()

    def test_find_requires_problem(self):
        result = query("find")
        assert "error" in result
        assert "problem" in result

    def test_find_with_problem(self):
        result = query("find", problem="undo did nothing")
        assert "checkpoint" in result.lower()

    def test_unknown_operation(self):
        result = query("bogus")
        assert "error" in result
        assert "unknown operation" in result
        assert "strategy|tactics|find" in result


class TestNotesContentCoversMigratedGotchas:
    """Every gotcha from the old inline get_usage_guidance dict and from
    CLAUDE.md's Mandatory Rules must survive as a Note -- this payload is
    the fallback channel for clients that drop the `instructions` field
    entirely (confirmed for LM Studio), so silently losing a gotcha in the
    migration would be a real regression."""

    def _note_text(self, note):
        return f"{note.summary} {note.detail}"

    def _all_text(self):
        return " ".join(self._note_text(n) for n in NOTES)

    def test_mentions_check_connection_first(self):
        assert "check_freecad_connection" in self._all_text()

    def test_mentions_prefer_primary_tool_over_execute_python(self):
        assert "execute_python" in self._all_text()

    def test_mentions_find_root_cause(self):
        text = self._all_text()
        assert "find_root_cause" in text
        assert "check_solid" in text

    def test_mentions_create_document_before_objects(self):
        assert "create_document" in self._all_text()

    def test_mentions_interactive_selection(self):
        assert "awaiting_selection" in self._all_text()
        assert "continue_selection" in self._all_text()

    def test_mentions_undo_redo_inert(self):
        text = self._all_text()
        assert "checkpoint" in text
        assert "rollback_to_checkpoint" in text

    def test_mentions_varset_quirks(self):
        text = self._all_text()
        assert "removeProperty" in text
        assert "PropertyEnumeration" in text

    def test_three_critical_notes(self):
        critical_ids = {n.id for n in NOTES if n.priority is Priority.CRITICAL}
        assert critical_ids == {
            "always-check-connection-first",
            "prefer-primary-tool-over-execute-python",
            "find-root-cause-not-symptom",
        }

    def test_at_least_one_strategy_and_one_tactic_note(self):
        kinds = {n.kind for n in NOTES}
        assert kinds == {NoteKind.STRATEGY, NoteKind.TACTIC}

    def test_note_ids_are_unique(self):
        ids = [n.id for n in NOTES]
        assert len(ids) == len(set(ids))
