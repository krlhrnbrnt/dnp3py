"""TCP transport for the DNP3 master runner.

`MasterTcpRunner` is a `MasterRunner` that opens a `TcpClientChannel` to the
outstation when no channel is supplied.

    runner = MasterTcpRunner(master=Master(handler=handler), host="10.0.0.5")
    await runner.open()
    try:
        await runner.integrity_poll()
    finally:
        await runner.close()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dnp3.master.runner import (
    LinkError,
    LinkResetPolicy,
    MasterRunner,
    MasterRunnerError,
    RequestRejectedError,
    ResponseTimeoutError,
    TimeSyncError,
)
from dnp3.transport_io.channel import Channel, TcpConfig
from dnp3.transport_io.tcp_client import TcpClientChannel

__all__ = [
    "LinkError",
    "LinkResetPolicy",
    "MasterRunnerError",
    "MasterTcpRunner",
    "RequestRejectedError",
    "ResponseTimeoutError",
    "TimeSyncError",
]

logger = logging.getLogger(__name__)


@dataclass
class MasterTcpRunner(MasterRunner):
    """Runs a DNP3 master over TCP.

    Attributes:
        host: Outstation host to connect to.
        port: Outstation TCP port.
    """

    host: str = "127.0.0.1"
    port: int = 20000

    def _create_channel(self) -> Channel:
        logger.info("Master connecting to %s:%d", self.host, self.port)
        return TcpClientChannel(config=TcpConfig(host=self.host, port=self.port))
