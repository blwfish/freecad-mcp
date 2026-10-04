"""Unit tests for the background-task watchdog (issue #91).

main() used to do ``health_task = asyncio.create_task(health_check_loop())``
and never look at the task again: the event loop holds only a weak reference
to a task, and nothing awaited it or attached a callback, so if the health
loop ever died (uncaught exception, CancelledError) monitoring stopped with
no signal at all.

The registry (``_background_tasks``) and the done-callback
(``_on_background_task_done``) are plain module-level objects in
freecad_mcp_server.py, so they're directly testable without main().
"""

import asyncio
import contextlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import freecad_mcp_server as bridge


@pytest.fixture
def fake_debugger():
    dbg = MagicMock()
    with patch.object(bridge, "debugger", dbg):
        yield dbg


@pytest.fixture(autouse=True)
def _clean_registry():
    bridge._background_tasks.clear()
    yield
    bridge._background_tasks.clear()


def _run(coro_factory):
    """Run a coroutine factory under a fresh loop, registering its task the
    same way main() does, and return the finished task."""

    async def driver():
        task = asyncio.create_task(coro_factory(), name="t")
        bridge._background_tasks.add(task)
        task.add_done_callback(bridge._on_background_task_done)
        return task

    async def runner():
        task = await driver()
        # Let the task finish (or be cancelled by the factory), then let the
        # loop run the done-callback, which is scheduled via call_soon.
        with contextlib.suppress(BaseException):
            await task
        await asyncio.sleep(0)
        return task

    return asyncio.run(runner())


class TestOnBackgroundTaskDone:
    def test_exception_is_logged_at_error_with_type_and_message(self, fake_debugger):
        async def boom():
            raise ValueError("monitor exploded")

        task = _run(boom)

        assert task.done()
        fake_debugger.logger.error.assert_called_once()
        msg = fake_debugger.logger.error.call_args.args[0]
        assert "'t'" in msg
        assert "ValueError" in msg
        assert "monitor exploded" in msg

    def test_unexpected_cancellation_is_logged(self, fake_debugger):
        async def cancel_self():
            raise asyncio.CancelledError

        task = _run(cancel_self)

        assert task.cancelled()
        fake_debugger.logger.error.assert_called_once()
        assert "cancelled" in fake_debugger.logger.error.call_args.args[0]

    def test_clean_return_is_logged_because_loop_should_never_exit(self, fake_debugger):
        async def returns():
            return None

        _run(returns)

        fake_debugger.logger.error.assert_called_once()
        assert "returned unexpectedly" in fake_debugger.logger.error.call_args.args[0]

    def test_finished_task_is_dropped_from_registry(self, fake_debugger):
        async def returns():
            return None

        task = _run(returns)

        assert task not in bridge._background_tasks

    def test_no_debugger_falls_back_to_stderr_instead_of_crashing(self, capsys):
        async def boom():
            raise RuntimeError("no debugger present")

        with patch.object(bridge, "debugger", None):
            _run(boom)

        err = capsys.readouterr().err
        assert "RuntimeError" in err
        assert "no debugger present" in err


class TestShutdownIsNotAFailure:
    def test_removing_callback_before_cancel_logs_nothing_and_clears_registry(
        self, fake_debugger
    ):
        """Mirrors main()'s finally-block: remove callback, cancel, discard."""

        async def runner():
            task = asyncio.create_task(asyncio.sleep(3600), name="health_check_loop")
            bridge._background_tasks.add(task)
            task.add_done_callback(bridge._on_background_task_done)
            await asyncio.sleep(0)  # let it start

            task.remove_done_callback(bridge._on_background_task_done)
            task.cancel()
            bridge._background_tasks.discard(task)
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            return task

        task = asyncio.run(runner())

        assert task.cancelled()
        fake_debugger.logger.error.assert_not_called()
        assert not bridge._background_tasks
