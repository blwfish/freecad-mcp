"""GUI-thread heartbeat — fast busy-vs-stuck diagnosis.

Before this existed, the only signal a caller got when FreeCAD's Qt main
thread was stuck (crashed mid-recompute, blocked in a modal dialog, macOS
App Nap throttling a backgrounded app, or any other reason the event loop
stopped pumping) was a slow timeout (30-120s) followed by a generic
"operation timeout" or connection-refused error -- indistinguishable from
"the GUI thread is just legitimately busy with a previous operation".

_process_gui_tasks already ticks every 100ms via QTimer.singleShot as long
as the Qt event loop is alive and processing events -- that's exactly the
signal a caller needs. GuiHeartbeat.stamp() should be called on every tick;
is_stale() then answers "has the event loop actually run recently" in
microseconds, before a request is queued, rather than after a full timeout.
"""

import time


class GuiHeartbeat:
    """Tracks the last time the Qt event loop was confirmed alive."""

    def __init__(self, stale_after: float = 2.0):
        """*stale_after*: seconds since the last stamp before is_stale() is
        True. Must comfortably exceed the _process_gui_tasks tick interval
        (100ms) -- 2s gives ~20 missed ticks of slack for normal scheduling
        jitter before declaring the event loop unresponsive.
        """
        self.stale_after = stale_after
        self._last_stamp: float | None = None

    def stamp(self) -> None:
        """Record that the event loop is alive right now."""
        self._last_stamp = time.time()

    def age(self) -> float | None:
        """Seconds since the last stamp, or None if never stamped."""
        if self._last_stamp is None:
            return None
        return time.time() - self._last_stamp

    def is_stale(self) -> bool:
        """True if the event loop has never been confirmed alive, or the
        last confirmation is older than stale_after seconds."""
        age = self.age()
        return age is None or age > self.stale_after
