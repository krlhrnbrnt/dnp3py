"""Tests for matching a control response's echo to the request's points."""

import logging
import struct
from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import CommandStatus, ControlCode
from dnp3.master import command_point_results
from dnp3.master.commands import ControlOperation, DirectOperateTask, OperateTask, SelectTask
from dnp3.master.handler import CommandPointResult, CommandPointState
from dnp3.objects.layout import object_width

S = CommandStatus
P = CommandPointState


def crob(index: int, on_time: int = 0) -> ControlOperation:
    return ControlOperation(index=index, control_code=ControlCode.LATCH_ON, on_time=on_time)


def analog(index: int, value: float) -> ControlOperation:
    return ControlOperation(index=index, analog_value=value, is_analog=True)


def echo(request: RequestFragment, statuses: Sequence[CommandStatus]) -> list[ObjectBlock]:
    """The request's object blocks with each object's status octet set, in request order."""
    pending = list(statuses)
    blocks = []
    for block in request.objects:
        prefix = 1 if block.header.qualifier == 0x17 else 2
        width = object_width(block.header.group, block.header.variation)
        assert width is not None
        data = bytearray(block.data)
        count = int.from_bytes(data[:prefix], "little")
        for slot in range(count):
            data[prefix + (slot + 1) * (prefix + width) - 1] = pending.pop(0)
        blocks.append(ObjectBlock(header=block.header, data=bytes(data)))
    assert not pending
    return blocks


def states(results: Sequence[CommandPointResult]) -> list[tuple[int, int, P, S]]:
    return [(r.header_index, r.index, r.state, r.status) for r in results]


def with_data(block: ObjectBlock, data: bytes) -> ObjectBlock:
    return ObjectBlock(header=block.header, data=data)


def test_direct_operate_all_success() -> None:
    request = DirectOperateTask(operations=[crob(0), crob(1)]).build_request()

    results = command_point_results(request, echo(request, [S.SUCCESS, S.SUCCESS]))

    assert states(results) == [(0, 0, P.SUCCESS, S.SUCCESS), (0, 1, P.SUCCESS, S.SUCCESS)]


def test_out_of_range_on_one_point() -> None:
    request = OperateTask(operations=[crob(0), crob(1)]).build_request()

    results = command_point_results(request, echo(request, [S.SUCCESS, S.OUT_OF_RANGE]))

    assert states(results) == [(0, 0, P.SUCCESS, S.SUCCESS), (0, 1, P.SUCCESS, S.OUT_OF_RANGE)]


def test_select_success_and_fail() -> None:
    request = SelectTask(operations=[crob(0), crob(1)]).build_request()

    results = command_point_results(request, echo(request, [S.SUCCESS, S.LOCAL]))

    assert states(results) == [(0, 0, P.SELECT_SUCCESS, S.UNDEFINED), (0, 1, P.SELECT_FAIL, S.LOCAL)]


def test_select_value_mismatch() -> None:
    request = SelectTask(operations=[crob(0, on_time=100)]).build_request()
    changed = SelectTask(operations=[crob(0, on_time=200)]).build_request()

    results = command_point_results(request, echo(changed, [S.SUCCESS]))

    assert states(results) == [(0, 0, P.SELECT_MISMATCH, S.UNDEFINED)]


def test_operate_value_mismatch() -> None:
    request = OperateTask(operations=[crob(0, on_time=100)]).build_request()
    changed = OperateTask(operations=[crob(0, on_time=200)]).build_request()

    results = command_point_results(request, echo(changed, [S.SUCCESS]))

    assert states(results) == [(0, 0, P.OPERATE_FAIL, S.UNDEFINED)]


def test_index_mismatch_leaves_point_init() -> None:
    request = DirectOperateTask(operations=[crob(0), crob(1)]).build_request()
    other = DirectOperateTask(operations=[crob(0), crob(2)]).build_request()

    results = command_point_results(request, echo(other, [S.SUCCESS, S.SUCCESS]))

    assert states(results) == [(0, 0, P.SUCCESS, S.SUCCESS), (0, 1, P.INIT, S.UNDEFINED)]


def test_qualifier_mismatch_leaves_header_init() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()
    wide = DirectOperateTask(operations=[crob(0), crob(300)]).build_request()
    [block] = echo(wide, [S.SUCCESS, S.SUCCESS])
    one_point_0x28 = with_data(block, (1).to_bytes(2, "little") + block.data[2:15])

    results = command_point_results(request, [one_point_0x28])

    assert states(results) == [(0, 0, P.INIT, S.UNDEFINED)]


def test_group_or_variation_mismatch_leaves_header_init() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()
    [block] = echo(request, [S.SUCCESS])
    header = ObjectHeader(group=12, variation=2, qualifier=block.header.qualifier)

    results = command_point_results(request, [ObjectBlock(header=header, data=block.data)])

    assert states(results) == [(0, 0, P.INIT, S.UNDEFINED)]


def test_short_echo_leaves_missing_points_init() -> None:
    request = DirectOperateTask(operations=[crob(0), crob(1)]).build_request()
    [block] = echo(request, [S.SUCCESS, S.SUCCESS])

    results = command_point_results(request, [with_data(block, b"\x01" + block.data[1:13])])

    assert states(results) == [(0, 0, P.SUCCESS, S.SUCCESS), (0, 1, P.INIT, S.UNDEFINED)]


def test_echo_with_more_points_than_sent_is_ignored() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()
    more = DirectOperateTask(operations=[crob(0), crob(1)]).build_request()

    results = command_point_results(request, echo(more, [S.SUCCESS, S.SUCCESS]))

    assert states(results) == [(0, 0, P.INIT, S.UNDEFINED)]


def test_missing_echo_header_leaves_points_init() -> None:
    request = DirectOperateTask(operations=[crob(0), analog(1, 5)]).build_request()
    crob_echo, _ = echo(request, [S.SUCCESS, S.SUCCESS])

    results = command_point_results(request, [crob_echo])

    assert states(results) == [(0, 0, P.SUCCESS, S.SUCCESS), (1, 1, P.INIT, S.UNDEFINED)]


def test_extra_echo_header_is_ignored_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()
    both = DirectOperateTask(operations=[crob(0), analog(1, 5)]).build_request()

    with caplog.at_level(logging.WARNING):
        results = command_point_results(request, echo(both, [S.SUCCESS, S.SUCCESS]))

    assert states(results) == [(0, 0, P.SUCCESS, S.SUCCESS)]
    assert "request had 1" in caplog.text


def test_unknown_status_code_is_undefined() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()

    results = command_point_results(request, echo(request, [0x55]))  # type: ignore[list-item]

    assert states(results) == [(0, 0, P.SUCCESS, S.UNDEFINED)]


def test_status_reserved_bit_is_masked() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()

    results = command_point_results(request, echo(request, [0x8C]))  # type: ignore[list-item]

    assert states(results) == [(0, 0, P.SUCCESS, S.OUT_OF_RANGE)]


def test_two_byte_indexes() -> None:
    request = DirectOperateTask(operations=[crob(300)]).build_request()
    assert request.objects[0].header.qualifier == 0x28

    results = command_point_results(request, echo(request, [S.SUCCESS]))

    assert states(results) == [(0, 300, P.SUCCESS, S.SUCCESS)]


def test_crob_and_analog_headers() -> None:
    request = DirectOperateTask(operations=[analog(4, 12), crob(2)]).build_request()

    results = command_point_results(request, echo(request, [S.SUCCESS, S.BLOCKED]))

    assert states(results) == [(0, 2, P.SUCCESS, S.SUCCESS), (1, 4, P.SUCCESS, S.BLOCKED)]


def test_empty_response_leaves_all_init() -> None:
    request = SelectTask(operations=[crob(0), analog(1, 5)]).build_request()

    results = command_point_results(request, [])

    assert states(results) == [(0, 0, P.INIT, S.UNDEFINED), (1, 1, P.INIT, S.UNDEFINED)]


def test_hostile_echo_count_reads_nothing() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()
    [block] = echo(request, [S.SUCCESS])

    results = command_point_results(request, [with_data(block, b"\xff" + block.data[1:])])

    assert states(results) == [(0, 0, P.INIT, S.UNDEFINED)]


operations = st.lists(
    st.one_of(
        st.builds(
            ControlOperation,
            index=st.integers(0, 65535),
            control_code=st.integers(0, 255).filter(lambda octet: octet & 0x0F <= 4).map(ControlCode),
            count=st.integers(0, 255),
            on_time=st.integers(0, 2**32 - 1),
            off_time=st.integers(0, 2**32 - 1),
        ),
        st.builds(
            ControlOperation,
            index=st.integers(0, 65535),
            analog_value=st.integers(-(2**31), 2**31 - 1).map(float),
            is_analog=st.just(True),
        ),
    ),
    min_size=1,
    max_size=10,
)


@given(operations, st.data())
def test_echoed_direct_operate_succeeds_with_echoed_statuses(ops: list[ControlOperation], data: st.DataObject) -> None:
    request = DirectOperateTask(operations=ops).build_request()
    # Request order: every CROB, then every analog output.
    ordered = [op for op in ops if not op.is_analog] + [op for op in ops if op.is_analog]
    statuses = data.draw(st.lists(st.sampled_from(list(CommandStatus)), min_size=len(ops), max_size=len(ops)))

    results = command_point_results(request, echo(request, statuses))

    assert [(r.index, r.state, r.status) for r in results] == [
        (op.index, P.SUCCESS, status) for op, status in zip(ordered, statuses, strict=True)
    ]


def test_g41v1_body_is_value_then_status() -> None:
    """Pins the analog request layout the echo helper relies on."""
    request = DirectOperateTask(operations=[analog(1, 7)]).build_request()

    assert request.objects[0].data == b"\x01\x01" + struct.pack("<iB", 7, 0)


def test_echo_without_count_field_leaves_points_init() -> None:
    request = DirectOperateTask(operations=[crob(0)]).build_request()
    [block] = echo(request, [S.SUCCESS])

    results = command_point_results(request, [with_data(block, b"")])

    assert states(results) == [(0, 0, P.INIT, S.UNDEFINED)]
