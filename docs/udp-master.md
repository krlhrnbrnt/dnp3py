# DNP3 master over UDP

`MasterUdpRunner` runs a dnp3py master against an outstation over UDP. It has
the same API as `MasterTcpRunner`: both are `MasterRunner`s and differ only in
the channel they open. Requests and responses both travel over UDP, one runner
per outstation.

```python
from dnp3.master import DefaultSOEHandler, Master, MasterConfig, MasterUdpRunner
from dnp3.transport_io import UdpConfig

handler = DefaultSOEHandler()
master = Master(handler=handler, config=MasterConfig(address=2, outstation_address=1))
udp = UdpConfig(remote_host="10.0.0.5", remote_port=20000)

async with MasterUdpRunner(master=master, udp=udp) as runner:
    await runner.integrity_poll()
```

## `UdpConfig`

| Field | Default | Meaning |
|---|---|---|
| `remote_host` | required | Outstation host name or address, IPv4 or IPv6. Datagrams from any other source are dropped. |
| `remote_port` | `20000` | Outstation UDP port. |
| `local_host` | `None` | Local address to bind; `None` binds every address of the remote's family. |
| `local_port` | `0` | Local port; `0` picks an ephemeral one. |

Some outstations send unsolicited responses to a fixed, configured master
port rather than to the port a request came from. For those, set `local_port`
to that port.

Each datagram must hold whole link frames. A truncated or corrupt frame is
dropped and counted in `channel.statistics.errors`; it never joins the next
datagram.

## Lifecycle

`open()` resolves `remote_host` and binds the local socket; `close()` releases
it. `async with` does both. Unlike TCP, opening does not prove the
outstation is there: the first sign of a missing outstation is a
`ResponseTimeoutError`.

The runner does not reset the data link by default
(`link_reset=LinkResetPolicy.NEVER`): link resets and confirmed link frames add
round trips that DNP3 over UDP does not use in practice. Pass
`link_reset=LinkResetPolicy.ON_OPEN` for an outstation that expects one.

## Polling

`integrity_poll()`, `class_poll()`, `request()` and `startup()` work as over
TCP. A lost request or response raises `ResponseTimeoutError`; on-demand calls
are not retried. `run_polls()` drives the master's `PollScheduler`, listens for
unsolicited responses between polls, and retries a poll that timed out after
`poll_retry_delay` seconds.

## Unsolicited responses

`listen_unsolicited()`, and `run_polls()` between polls, report unsolicited
responses and confirm them when the outstation asks. If a CONFIRM is lost, the
outstation sends the same response again and the master reports it again, as
opendnp3's master does. Handlers must tolerate seeing an event twice.

## Controls

`direct_operate()` and `select_and_operate()` work as over TCP; see
[DNP3 Control Commands](control-commands.md). Controls are never retried: over
UDP a lost response cannot be told apart from a lost request, and repeating an
OPERATE could carry it out twice.

## Errors

| Situation | Behavior |
|---|---|
| Datagram from a source other than the remote | Dropped, `statistics.errors` incremented, logged at debug level |
| Truncated or corrupt frame in a datagram | Dropped, `statistics.errors` incremented |
| Wrong link address or direction | Dropped by the runner |
| Stale or out-of-sequence fragment | A stale first fragment is dropped; a broken sequence walk raises `MasterRunnerError` |
| READ with no response | `ResponseTimeoutError`; `run_polls()` retries after `poll_retry_delay` |
| OPERATE / DIRECT_OPERATE with no response | `ResponseTimeoutError`, never retried |
| ICMP port unreachable | Logged and ignored; the outstation may be restarting |
| `close()` | Closes the socket; a pending read raises `ChannelClosedError`, which the runner raises as `LinkError` |
