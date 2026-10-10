"""UDP transport for the DNP3 master runner.

`MasterUdpRunner` is a `MasterRunner` that opens a `UdpChannel` to the
outstation when no channel is supplied.

    udp = UdpConfig(remote_host="10.0.0.5")
    async with MasterUdpRunner(master=Master(handler=handler), udp=udp) as runner:
        await runner.integrity_poll()

The link layer is not reset by default: link resets and confirmed link frames
add round trips that DNP3 over UDP does not use in practice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dnp3.master.runner import LinkResetPolicy, MasterRunner, MasterRunnerError
from dnp3.transport_io.channel import Channel
from dnp3.transport_io.udp import UdpChannel, UdpConfig

logger = logging.getLogger(__name__)


@dataclass
class MasterUdpRunner(MasterRunner):
    """Runs a DNP3 master over UDP.

    Attributes:
        udp: Outstation endpoint and local binding. Required unless a channel
            is supplied.
    """

    link_reset: LinkResetPolicy = LinkResetPolicy.NEVER
    udp: UdpConfig | None = None

    def _create_channel(self) -> Channel:
        if self.udp is None:
            msg = "MasterUdpRunner needs a udp config or a channel"
            raise MasterRunnerError(msg)
        logger.info("Master using UDP %s:%d", self.udp.remote_host, self.udp.remote_port)
        return UdpChannel(self.udp)
