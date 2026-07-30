"""Tests for AICopilot/gui_heartbeat.py.

Uses a fake clock (monkeypatched gui_heartbeat.time.time) rather than real
sleeps, so the threshold boundary (stale_after) can be tested exactly --
at the value, just below, and just above -- without timing flakiness.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "AICopilot"))

import gui_heartbeat as hb


class _FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestGuiHeartbeat:

    def test_never_stamped_is_stale(self):
        h = hb.GuiHeartbeat(stale_after=2.0)
        assert h.is_stale() is True
        assert h.age() is None

    def test_fresh_stamp_is_not_stale(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(hb.time, "time", clock.time)
        h = hb.GuiHeartbeat(stale_after=2.0)
        h.stamp()
        assert h.is_stale() is False
        assert h.age() == 0.0

    def test_age_just_below_threshold_not_stale(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(hb.time, "time", clock.time)
        h = hb.GuiHeartbeat(stale_after=2.0)
        h.stamp()
        clock.advance(1.999)
        assert h.is_stale() is False

    def test_age_exactly_at_threshold_not_stale(self, monkeypatch):
        """is_stale is a strict > comparison -- exactly stale_after old is
        still fresh. Pin the boundary side explicitly."""
        clock = _FakeClock()
        monkeypatch.setattr(hb.time, "time", clock.time)
        h = hb.GuiHeartbeat(stale_after=2.0)
        h.stamp()
        clock.advance(2.0)
        assert h.is_stale() is False

    def test_age_just_above_threshold_is_stale(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(hb.time, "time", clock.time)
        h = hb.GuiHeartbeat(stale_after=2.0)
        h.stamp()
        clock.advance(2.001)
        assert h.is_stale() is True

    def test_restamp_after_stale_becomes_fresh_again(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(hb.time, "time", clock.time)
        h = hb.GuiHeartbeat(stale_after=2.0)
        h.stamp()
        clock.advance(10.0)
        assert h.is_stale() is True
        h.stamp()
        assert h.is_stale() is False

    def test_zero_stale_after_boundary(self, monkeypatch):
        """stale_after=0 is a legitimate (if extreme) configuration -- any
        elapsed time at all should be stale, but age()==0.0 immediately
        after stamping should not be (0.0 > 0 is False)."""
        clock = _FakeClock()
        monkeypatch.setattr(hb.time, "time", clock.time)
        h = hb.GuiHeartbeat(stale_after=0.0)
        h.stamp()
        assert h.is_stale() is False
        clock.advance(0.001)
        assert h.is_stale() is True

    def test_default_stale_after_is_2_seconds(self):
        h = hb.GuiHeartbeat()
        assert h.stale_after == 2.0
