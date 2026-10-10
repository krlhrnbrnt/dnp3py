"""Tests for the transport-independent master runner."""

from __future__ import annotations

import pytest

from dnp3.master import Master
from dnp3.master.runner import LinkResetPolicy, MasterRunner, MasterRunnerError
from dnp3.master.udp_runner import MasterUdpRunner


async def test_open_without_channel_raises() -> None:
    runner = MasterRunner(master=Master())

    with pytest.raises(MasterRunnerError, match="no channel supplied"):
        await runner.open()

    assert not runner.is_open


async def test_udp_runner_without_config_raises() -> None:
    runner = MasterUdpRunner(master=Master())

    with pytest.raises(MasterRunnerError, match="udp"):
        await runner.open()


def test_exports() -> None:
    import dnp3.master

    assert dnp3.master.MasterRunner is MasterRunner
    assert dnp3.master.MasterUdpRunner is MasterUdpRunner
    assert MasterUdpRunner(master=Master()).link_reset is LinkResetPolicy.NEVER
