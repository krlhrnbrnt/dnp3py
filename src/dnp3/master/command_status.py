"""Per-point results of a control request, read from the outstation's echo.

A response to SELECT, OPERATE or DIRECT_OPERATE repeats the request's object
headers and objects, setting only each object's status octet (IEEE 1815-2012
4.4.4.3 Rules 7 and 8). Matching follows opendnp3's `TypedCommandHeader`: echo
header *i* answers request header *i*, and points match by position. Echoed
values are compared octet for octet, as Rule 8 states, where opendnp3 compares
decoded values.
"""

import logging
from collections.abc import Sequence

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.core.enums import CommandStatus, FunctionCode
from dnp3.master.handler import CommandPointResult, CommandPointState
from dnp3.master.master import _decode_object_layout, _iter_object_slots
from dnp3.objects.layout import object_width

logger = logging.getLogger(__name__)

_STATUS_MASK = 0x7F  # Bit 7 of the status octet is reserved (Table 4-2).


def command_point_results(request: RequestFragment, response: Sequence[ObjectBlock]) -> tuple[CommandPointResult, ...]:
    """Match a control response's objects to the request's points.

    A point stays `INIT` when its echo header is missing, has another group,
    variation or qualifier, holds more points than were sent, or echoes
    another index at the point's position.

    Args:
        request: The SELECT, OPERATE or DIRECT_OPERATE that was sent.
        response: Object blocks of its response.

    Returns:
        One result per commanded point, in request order.
    """
    selecting = request.header.function is FunctionCode.SELECT
    if len(response) > len(request.objects):
        # opendnp3 fails the whole task here. Ignoring the extra headers keeps
        # the matched points; an unmatched point never reaches SELECT_SUCCESS.
        logger.warning(
            "Control response has %d object headers, request had %d; ignoring the extra",
            len(response),
            len(request.objects),
        )
    results: list[CommandPointResult] = []
    for header_index, sent in enumerate(request.objects):
        echoed = response[header_index] if header_index < len(response) else None
        results.extend(_match_header(header_index, sent, echoed, selecting=selecting))
    return tuple(results)


def _match_header(
    header_index: int, sent: ObjectBlock, echoed: ObjectBlock | None, *, selecting: bool
) -> list[CommandPointResult]:
    sent_objects = _objects(sent)
    results = [CommandPointResult(header_index, index, CommandPointState.INIT) for index, _ in sent_objects]
    if echoed is None or echoed.header != sent.header:
        return results
    echoed_objects = _objects(echoed)
    if len(echoed_objects) > len(sent_objects):
        return results

    for position, ((index, body), (echoed_index, echoed_body)) in enumerate(
        zip(sent_objects, echoed_objects, strict=False)
    ):
        if echoed_index != index:
            continue
        # The status octet is last in g12v1 and every g41 variation.
        if echoed_body[:-1] != body[:-1]:
            state = CommandPointState.SELECT_MISMATCH if selecting else CommandPointState.OPERATE_FAIL
            results[position] = CommandPointResult(header_index, index, state)
            continue
        status = _status(echoed_body[-1])
        if not selecting:
            results[position] = CommandPointResult(header_index, index, CommandPointState.SUCCESS, status)
        elif status is CommandStatus.SUCCESS:
            results[position] = CommandPointResult(header_index, index, CommandPointState.SELECT_SUCCESS)
        else:
            results[position] = CommandPointResult(header_index, index, CommandPointState.SELECT_FAIL, status)
    return results


def _objects(block: ObjectBlock) -> list[tuple[int, bytes]]:
    """(index, object octets) for each whole object in a block; empty if it cannot be walked."""
    width = object_width(block.header.group, block.header.variation)
    layout = _decode_object_layout(block.header.qualifier, block.data)
    if width is None or layout is None:
        return []
    return [
        (index, block.data[offset : offset + width]) for index, offset in _iter_object_slots(layout, block.data, width)
    ]


def _status(octet: int) -> CommandStatus:
    try:
        return CommandStatus(octet & _STATUS_MASK)
    except ValueError:
        return CommandStatus.UNDEFINED
