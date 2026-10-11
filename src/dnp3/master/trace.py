"""Debug trace of master traffic, one logger per protocol layer.

The loggers are children of the runner's logger, so `dnp3.master.runner` at
DEBUG turns all of them on and each layer can be silenced on its own. Each
`log_*` helper checks its logger first: with DEBUG off no hex is formatted and
no fragment is parsed a second time. The format is for people reading a failed
test's log, not a contract.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.header import ApplicationControl, ResponseHeader
from dnp3.application.parser import (
    _START_STOP_CODES,
    ParseError,
    _parse_range,
    parse_response,
    parse_response_header,
)
from dnp3.application.qualifiers import RangeCode
from dnp3.core.enums import LinkFunctionCode
from dnp3.core.flags import IIN
from dnp3.datalink.frame import DataLinkFrame
from dnp3.transport.segment import TransportSegment

link_logger = logging.getLogger("dnp3.master.runner.link")
transport_logger = logging.getLogger("dnp3.master.runner.transport")
app_logger = logging.getLogger("dnp3.master.runner.app")

# Primary and secondary link function codes reuse the same values (IEEE 1815-2012
# 9.2.4), so the name is looked up by PRM. `__members__` includes the aliases that
# iterating the enum skips.
_PRIMARY_NAMES = {
    code.value: name.removeprefix("PRI_")
    for name, code in LinkFunctionCode.__members__.items()
    if name.startswith("PRI_")
}
_SECONDARY_NAMES = {
    code.value: name.removeprefix("SEC_")
    for name, code in LinkFunctionCode.__members__.items()
    if name.startswith("SEC_")
}


def describe_link_frame(frame: DataLinkFrame) -> str:
    """One line for a link frame: function, addresses, control flags, LENGTH, wire hex with CRCs."""
    header = frame.header
    control = header.control
    names = _PRIMARY_NAMES if control.prm else _SECONDARY_NAMES
    parts = [
        names.get(control.function_code, f"FUNC_{control.function_code}"),
        f"{header.source}->{header.destination}",
    ]
    if control.dir_from_master:
        parts.append("DIR")
    if control.prm:
        parts.append("PRM")
        parts += [flag for flag, on in (("FCB", control.fcb), ("FCV", control.fcv)) if on]
    elif control.fcv:
        # In a secondary frame the FCV bit position carries DFC.
        parts.append("DFC")
    parts.append(f"len={header.length}")
    return f"{' '.join(parts)} | {frame.to_bytes().hex(' ')}"


def describe_segment(segment: TransportSegment) -> str:
    """One line for a transport segment; the byte count excludes the transport header."""
    flags = [flag for flag, on in (("FIR", segment.is_first), ("FIN", segment.is_final)) if on]
    return " ".join(["segment", *flags, f"seq={segment.sequence}", f"{len(segment.payload)} bytes"])


def describe_iin(iin: IIN) -> str:
    """IIN bits by name, then the two octets as they appear on the wire."""
    names = "|".join(str(flag.name) for flag in IIN if flag & iin) or "none"
    first, second = iin.to_bytes()
    return f"{names} (IIN1=0x{first:02x} IIN2=0x{second:02x})"


def describe_object_block(block: ObjectBlock) -> str:
    """Object header with its range, count or `all`, and the length of its object data."""
    header = block.header
    text = f"g{header.group}v{header.variation} qual=0x{header.qualifier:02x}"
    try:
        range_code = header.range_code
        parsed = _parse_range(block.data, range_code)
    except (ParseError, ValueError):
        # A reserved range code or a range cut short; the hex line still shows the bytes.
        return f"{text} range unreadable"
    if range_code is RangeCode.ALL_OBJECTS:
        text += " all"
    elif range_code in _START_STOP_CODES:
        text += f" range {parsed.start}-{parsed.stop}"
    elif parsed.bytes_consumed:
        text += f" count {parsed.count}"
    object_bytes = len(block.data) - parsed.bytes_consumed
    return f"{text} ({object_bytes} bytes)" if object_bytes else text


def _control_flags(control: ApplicationControl) -> list[str]:
    flags = (("FIR", control.fir), ("FIN", control.fin), ("CON", control.con), ("UNS", control.uns))
    return [flag for flag, on in flags if on]


def describe_request(request: RequestFragment) -> str:
    """Several lines for a request: header, one line per object header, fragment hex."""
    header = request.header
    lines = [" ".join([header.function.name, f"seq={header.control.seq}", *_control_flags(header.control)])]
    lines += [f"  {describe_object_block(block)}" for block in request.objects]
    lines.append(f"  hex: {request.to_bytes().hex(' ')}")
    return "\n".join(lines)


def describe_response(data: bytes, latency_ms: float | None) -> str:
    """Several lines for a received fragment, whether or not it parses.

    Args:
        data: Reassembled application fragment.
        latency_ms: Milliseconds since the outstanding request was sent, or
            None when no request is outstanding. Omitted for an unsolicited
            fragment, which answers no request.
    """
    header: ResponseHeader | None
    blocks: Sequence[ObjectBlock] = ()
    error: Exception | None = None
    # ValueError and IndexError too: a trace must never raise into the runner,
    # whatever the outstation sent.
    try:
        response = parse_response(data)
    except (ParseError, ValueError, IndexError) as exc:
        error = exc
        try:
            header, _ = parse_response_header(data)
        except (ParseError, ValueError, IndexError):
            header = None
    else:
        header, blocks = response.header, response.objects

    if header is None:
        lines = ["unparseable response header"]
    else:
        parts = [header.function.name, f"seq={header.control.seq}", *_control_flags(header.control)]
        if latency_ms is not None and not header.control.uns:
            parts.append(f"{latency_ms:.0f} ms after request")
        parts.append(f"IIN={describe_iin(header.iin)}")
        lines = [" ".join(parts)]
    lines += [f"  {describe_object_block(block)}" for block in blocks]
    if error is not None:
        lines.append(f"  unparseable: {error}")
    lines.append(f"  hex: {data.hex(' ')}")
    return "\n".join(lines)


def log_frame(direction: str, frame: DataLinkFrame) -> None:
    if link_logger.isEnabledFor(logging.DEBUG):
        link_logger.debug("%s %s", direction, describe_link_frame(frame))


def log_segment(direction: str, segment: TransportSegment) -> None:
    if transport_logger.isEnabledFor(logging.DEBUG):
        transport_logger.debug("%s %s", direction, describe_segment(segment))


def log_request(request: RequestFragment) -> None:
    if app_logger.isEnabledFor(logging.DEBUG):
        app_logger.debug("TX %s", describe_request(request))


def log_response(data: bytes, latency_ms: float | None) -> None:
    if app_logger.isEnabledFor(logging.DEBUG):
        app_logger.debug("RX %s", describe_response(data, latency_ms))
