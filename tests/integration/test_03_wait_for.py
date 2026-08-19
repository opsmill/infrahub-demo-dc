"""How ``wait_for`` classifies the exceptions a check raises, which decides what a failure costs.

Marked ``offline``: the checks here are injected callables, so nothing is polled but this suite's own
functions. The classification is the point. A bug in the check itself -- an SDK attribute a version
bump renamed, which is precisely what this suite exists to catch -- must be reported in a second; a
partially populated response from a deployment that is still starting must be retried. Getting that
backwards costs the caller their whole timeout budget (up to 40 minutes) and then reports the wrong
cause.
"""

from __future__ import annotations

import pytest

from .helpers import wait_for

pytestmark = pytest.mark.offline


async def test_attribute_error_propagates_without_retrying() -> None:
    """An ``AttributeError`` is a bug in the check, so retrying it only delays the report.

    The call count is asserted, not just the exception: a retried ``AttributeError`` would still
    surface eventually, as a timeout naming the wrong cause after the whole budget is spent.
    """
    calls = 0

    async def check() -> tuple[bool, str]:
        nonlocal calls
        calls += 1
        msg = "'CoreRepository' object has no attribute 'sync_status'"
        raise AttributeError(msg)

    with pytest.raises(AttributeError, match="sync_status"):
        await wait_for(check, "an SDK rename", timeout=60, interval=1)
    assert calls == 1


async def test_key_error_is_retried_until_the_check_succeeds() -> None:
    """A ``KeyError`` is what a still-starting deployment legitimately produces.

    A partially populated response is missing keys for a moment and then is not, so this is the one
    lookup failure that must stay transient rather than joining the fast-fail set above.
    """
    calls = 0

    async def check() -> tuple[bool, str]:
        nonlocal calls
        calls += 1
        if calls < 3:
            msg = "edges"
            raise KeyError(msg)
        return True, "in-sync"

    assert await wait_for(check, "a slow deployment", timeout=60, interval=1) == "in-sync"
    assert calls == 3


async def test_timeout_message_carries_the_last_payload() -> None:
    """The timeout has to say what it observed, or the log is the only way to diagnose it.

    A check hands back the diagnostic state it saw on the way to timing out, and that state is what
    distinguishes "never started" from "started and stalled at this step".
    """

    async def check() -> tuple[bool, dict[str, str]]:
        return False, {"sync_status": "syncing"}

    with pytest.raises(AssertionError, match="sync_status.*syncing"):
        await wait_for(check, "a repository that never syncs", timeout=2, interval=1)
