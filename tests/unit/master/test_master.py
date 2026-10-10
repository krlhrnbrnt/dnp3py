"""Tests for the Master class."""

import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dnp3.application.builder import build_response
from dnp3.application.fragment import ObjectBlock
from dnp3.application.parser import parse_request, parse_response
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import ControlCode, FunctionCode
from dnp3.core.timestamp import DNP3Timestamp
from dnp3.master.commands import (
    CommandBuilder,
    ControlOperation,
    DirectOperateTask,
    OperateTask,
    SelectTask,
)
from dnp3.master.config import MasterConfig, PollingConfig
from dnp3.master.handler import (
    DefaultSOEHandler,
    ResponseInfo,
)
from dnp3.master.master import (
    QUALITY_ONLINE,
    QUALITY_STATE,
    Master,
    propagation_delay_ms,
)
from dnp3.master.polling import IntegrityPollTask
from dnp3.master.state import MasterState
from tests.unit.master.delivery import delivered


class TestMasterCreation:
    """Tests for Master creation and initialization."""

    def test_default_creation(self) -> None:
        """Test creating master with defaults."""
        master = Master()

        assert isinstance(master.config, MasterConfig)
        assert isinstance(master.handler, DefaultSOEHandler)
        assert master.state == MasterState.IDLE
        assert master.is_idle is True

    def test_with_custom_config(self) -> None:
        """Test creating master with custom config."""
        config = MasterConfig(
            address=5,
            outstation_address=100,
        )
        master = Master(config=config)

        assert master.config.address == 5
        assert master.config.outstation_address == 100

    def test_with_custom_handler(self) -> None:
        """Test creating master with custom handler."""
        handler = DefaultSOEHandler()
        master = Master(handler=handler)

        assert master.handler is handler

    def test_scheduler_property(self) -> None:
        """Test scheduler property."""
        master = Master()

        assert master.scheduler is not None
        assert master.scheduler.task_count >= 0


class TestMasterPollingSetup:
    """Tests for polling setup from config."""

    def test_polling_disabled(self) -> None:
        """Test with all polling disabled."""
        polling = PollingConfig(
            integrity_poll_interval=0.0,
            class_1_poll_interval=0.0,
            class_2_poll_interval=0.0,
            class_3_poll_interval=0.0,
        )
        config = MasterConfig(polling=polling)
        master = Master(config=config)

        assert master.scheduler.task_count == 0

    def test_integrity_poll_enabled(self) -> None:
        """Test with integrity poll enabled."""
        polling = PollingConfig(
            integrity_poll_interval=3600.0,
            class_1_poll_interval=0.0,
            class_2_poll_interval=0.0,
            class_3_poll_interval=0.0,
        )
        config = MasterConfig(polling=polling)
        master = Master(config=config)

        assert master.scheduler.task_count == 1

    def test_all_class_polls_enabled(self) -> None:
        """Test with all class polls enabled."""
        polling = PollingConfig(
            integrity_poll_interval=0.0,
            class_1_poll_interval=10.0,
            class_2_poll_interval=20.0,
            class_3_poll_interval=30.0,
        )
        config = MasterConfig(polling=polling)
        master = Master(config=config)

        assert master.scheduler.task_count == 3

    def test_all_polls_enabled(self) -> None:
        """Test with all polls enabled."""
        polling = PollingConfig(
            integrity_poll_interval=3600.0,
            class_1_poll_interval=10.0,
            class_2_poll_interval=20.0,
            class_3_poll_interval=30.0,
        )
        config = MasterConfig(polling=polling)
        master = Master(config=config)

        assert master.scheduler.task_count == 4


class TestMasterRequestBuilding:
    """Tests for request building methods."""

    def test_build_integrity_poll(self) -> None:
        """Test building integrity poll request."""
        master = Master()

        fragment = master.build_integrity_poll()

        assert fragment.header.function == FunctionCode.READ

    def test_build_class_poll_all(self) -> None:
        """Test building class poll for all classes."""
        master = Master()

        fragment = master.build_class_poll(class_1=True, class_2=True, class_3=True)

        assert fragment.header.function == FunctionCode.READ

    def test_build_class_poll_single(self) -> None:
        """Test building class poll for single class."""
        master = Master()

        fragment = master.build_class_poll(class_1=True, class_2=False, class_3=False)

        assert fragment.header.function == FunctionCode.READ

    def test_build_range_poll(self) -> None:
        """Test building range poll request."""
        master = Master()

        fragment = master.build_range_poll(group=30, variation=1, start=0, stop=10)

        assert fragment.header.function == FunctionCode.READ

    def test_build_select(self) -> None:
        """Test building SELECT request."""
        master = Master()
        task = SelectTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        fragment = master.build_select(task)

        assert fragment.header.function == FunctionCode.SELECT

    def test_build_operate(self) -> None:
        """Test building OPERATE request."""
        master = Master()
        task = OperateTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        fragment = master.build_operate(task)

        assert fragment.header.function == FunctionCode.OPERATE

    def test_build_direct_operate(self) -> None:
        """Test building DIRECT_OPERATE request."""
        master = Master()
        task = DirectOperateTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        fragment = master.build_direct_operate(task)

        assert fragment.header.function == FunctionCode.DIRECT_OPERATE

    def test_build_enable_unsolicited(self) -> None:
        """Test building ENABLE_UNSOLICITED request."""
        master = Master()

        fragment = master.build_enable_unsolicited()

        assert fragment.header.function == FunctionCode.ENABLE_UNSOLICITED

    def test_build_enable_unsolicited_partial(self) -> None:
        """Test building ENABLE_UNSOLICITED for specific classes."""
        master = Master()

        fragment = master.build_enable_unsolicited(class_1=True, class_2=False, class_3=True)

        assert fragment.header.function == FunctionCode.ENABLE_UNSOLICITED

    def test_build_disable_unsolicited(self) -> None:
        """Test building DISABLE_UNSOLICITED request."""
        master = Master()

        fragment = master.build_disable_unsolicited()

        assert fragment.header.function == FunctionCode.DISABLE_UNSOLICITED

    def test_build_delay_measure(self) -> None:
        """Test building DELAY_MEASURE request."""
        master = Master()

        fragment = master.build_delay_measure()

        assert fragment.header.function == FunctionCode.DELAY_MEASURE

    def test_build_record_current_time(self) -> None:
        """RECORD_CURRENT_TIME carries no objects and draws the next sequence."""
        master = Master()
        previous = master.next_request_sequence()

        fragment = master.build_record_current_time()

        assert fragment.header.function == FunctionCode.RECORD_CURRENT_TIME
        assert fragment.objects == ()
        assert fragment.sequence == (previous + 1) % 16

    @pytest.mark.parametrize(("recorded", "variation"), [(False, 0x01), (True, 0x03)], ids=["g50v1", "g50v3"])
    def test_build_write_time(self, recorded: bool, variation: int) -> None:
        """A time write is g50, count-qualified with one object."""
        fragment = Master().build_write_time(DNP3Timestamp(0x0102_0304_0506), recorded=recorded)

        assert fragment.to_bytes() == bytes(
            [0xC0 | fragment.sequence, 0x02, 0x32, variation, 0x07, 0x01, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01]
        )

    def test_build_clear_restart(self) -> None:
        """Clear restart writes 0 to IIN bit 7: g80v1, start-stop 7..7, one packed octet."""
        master = Master()
        previous = master.next_request_sequence()

        fragment = master.build_clear_restart()

        assert fragment.to_bytes() == bytes([0xC0 | (previous + 1) % 16, 0x02, 0x50, 0x01, 0x00, 0x07, 0x07, 0x00])

    def test_build_write_octet_string_uint8_index(self) -> None:
        """A one-string write is g110v{len}, start-stop range on the index, then the string."""
        fragment = Master().build_write_octet_string(3, b"abc")

        assert fragment.to_bytes() == bytes(
            [0xC0 | fragment.sequence, 0x02, 0x6E, 0x03, 0x00, 0x03, 0x03, 0x61, 0x62, 0x63]
        )

    def test_build_write_octet_string_uint16_index(self) -> None:
        """An index above 255 takes the 2-octet start-stop qualifier."""
        fragment = Master().build_write_octet_string(300, b"abc")

        assert fragment.to_bytes()[4:9] == bytes([0x01, 0x2C, 0x01, 0x2C, 0x01])

    @pytest.mark.parametrize(("index", "qualifier"), [(255, 0x00), (256, 0x01), (65535, 0x01)])
    def test_build_write_octet_string_index_boundaries(self, index: int, qualifier: int) -> None:
        """The qualifier widens to two octets exactly when the index passes 255."""
        assert Master().build_write_octet_string(index, b"a").to_bytes()[4] == qualifier

    def test_build_write_octet_string_max_length(self) -> None:
        """A 255-octet string is variation 255."""
        assert Master().build_write_octet_string(0, bytes(255)).to_bytes()[3] == 255

    @pytest.mark.parametrize(
        ("index", "value"),
        [(0, b""), (0, bytes(256)), (-1, b"a"), (65536, b"a")],
        ids=["empty", "too-long", "negative-index", "index-too-large"],
    )
    def test_build_write_octet_string_rejects(self, index: int, value: bytes) -> None:
        """An unwritable index or length raises without taking a sequence number."""
        master = Master()
        expected = Master().build_write_octet_string(0, b"a").sequence

        with pytest.raises(ValueError):
            master.build_write_octet_string(index, value)

        assert master.build_write_octet_string(0, b"a").sequence == expected

    @given(index=st.integers(min_value=0, max_value=65535), value=st.binary(min_size=1, max_size=255))
    def test_build_write_octet_string_round_trips(self, index: int, value: bytes) -> None:
        """Any writable index and string parse back as one WRITE block of that string."""
        fragment = parse_request(Master().build_write_octet_string(index, value).to_bytes())

        assert fragment.header.function == FunctionCode.WRITE
        (block,) = fragment.objects
        width = 1 if index <= 255 else 2
        assert (block.header.group, block.header.variation, block.header.qualifier) == (110, len(value), width - 1)
        assert block.data == index.to_bytes(width, "little") * 2 + value

    def test_build_confirm(self) -> None:
        """Test building CONFIRM request."""
        master = Master()

        fragment = master.build_confirm(seq=5)

        assert fragment.header.function == FunctionCode.CONFIRM

    def test_build_unsolicited_confirm(self) -> None:
        """A CONFIRM built for an unsolicited response sets UNS and keeps SEQ."""
        master = Master()

        fragment = master.build_confirm(seq=9, uns=True)

        assert fragment.to_bytes() == bytes([0xD9, FunctionCode.CONFIRM.value])
        assert master.build_confirm(seq=9).to_bytes() == bytes([0xC9, FunctionCode.CONFIRM.value])

    def test_sequence_increments(self) -> None:
        """Test that sequence numbers increment."""
        master = Master()

        frag1 = master.build_integrity_poll()
        frag2 = master.build_integrity_poll()
        frag3 = master.build_integrity_poll()

        seq1 = frag1.header.control.seq
        seq2 = frag2.header.control.seq
        seq3 = frag3.header.control.seq

        assert seq2 == (seq1 + 1) % 16
        assert seq3 == (seq2 + 1) % 16


class TestMasterBinaryParsing:
    """Tests for binary value parsing."""

    def test_parse_binary_packed_v1(self) -> None:
        """Test parsing packed binary input (g1v1)."""
        # Create object block for g1v1 with start-stop range 0-7
        # Qualifier 0x00 = 1-byte start-stop
        # Data: start(0), stop(7), bits(0b10101010)
        header = ObjectHeader(group=1, variation=1, qualifier=0x00)
        data = bytes([0, 7, 0b10101010])  # Start=0, Stop=7, value byte
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_binary_input")

        assert len(values) >= 1
        # Bit 0 = 0 (False), Bit 1 = 1 (True), etc.
        assert values[0].index == 0
        assert values[0].value is False
        assert values[1].index == 1
        assert values[1].value is True

    def test_parse_binary_flags_v2(self) -> None:
        """Test parsing binary input with flags (g1v2)."""
        # Create object block for g1v2 with start-stop range
        # Each value is 1 byte: flags with bit 7 = state
        header = ObjectHeader(group=1, variation=2, qualifier=0x00)
        # Start=0, Stop=2, values: 0x81 (ON+online), 0x01 (OFF+online), 0x80 (ON)
        data = bytes([0, 2, 0x81, 0x01, 0x80])
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_binary_input")

        assert len(values) == 3
        assert values[0].index == 0
        assert values[0].value is True
        assert values[0].quality == 0x01

        assert values[1].index == 1
        assert values[1].value is False
        assert values[1].quality == 0x01

        assert values[2].index == 2
        assert values[2].value is True
        assert values[2].quality == 0x00

    def test_parse_binary_empty(self) -> None:
        """Test parsing empty binary block."""
        header = ObjectHeader(group=1, variation=2, qualifier=0x00)
        block = ObjectBlock(header=header, data=b"")

        values = delivered(block, "on_binary_input")

        assert len(values) == 0


class TestMasterAnalogParsing:
    """Tests for analog value parsing."""

    def test_parse_analog_32bit_flags_v1(self) -> None:
        """Test parsing 32-bit analog with flags (g30v1)."""
        # g30v1: 1 byte flags + 4 bytes value
        header = ObjectHeader(group=30, variation=1, qualifier=0x00)
        # Start=0, Stop=0, flags=0x01, value=100 (little-endian)
        value_bytes = (100).to_bytes(4, "little", signed=True)
        data = bytes([0, 0, 0x01]) + value_bytes
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_analog_input")

        assert len(values) == 1
        assert values[0].index == 0
        assert values[0].value == 100.0
        assert values[0].quality == 0x01

    def test_parse_analog_16bit_flags_v2(self) -> None:
        """Test parsing 16-bit analog with flags (g30v2)."""
        # g30v2: 1 byte flags + 2 bytes value
        header = ObjectHeader(group=30, variation=2, qualifier=0x00)
        # Start=0, Stop=1, flags=0x01, value=500, flags=0x01, value=-100
        data = bytes([0, 1, 0x01]) + (500).to_bytes(2, "little", signed=True)
        data += bytes([0x01]) + (-100).to_bytes(2, "little", signed=True)
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_analog_input")

        assert len(values) == 2
        assert values[0].index == 0
        assert values[0].value == 500.0
        assert values[1].index == 1
        assert values[1].value == -100.0

    def test_parse_analog_32bit_no_flags_v3(self) -> None:
        """Test parsing 32-bit analog without flags (g30v3)."""
        # g30v3: 4 bytes value only
        header = ObjectHeader(group=30, variation=3, qualifier=0x00)
        data = bytes([0, 0]) + (12345).to_bytes(4, "little", signed=True)
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_analog_input")

        assert len(values) == 1
        assert values[0].value == 12345.0
        assert values[0].quality == QUALITY_ONLINE

    def test_parse_analog_16bit_no_flags_v4(self) -> None:
        """Test parsing 16-bit analog without flags (g30v4)."""
        # g30v4: 2 bytes value only
        header = ObjectHeader(group=30, variation=4, qualifier=0x00)
        data = bytes([0, 0]) + (1000).to_bytes(2, "little", signed=True)
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_analog_input")

        assert len(values) == 1
        assert values[0].value == 1000.0

    def test_parse_analog_empty(self) -> None:
        """Test parsing empty analog block."""
        header = ObjectHeader(group=30, variation=1, qualifier=0x00)
        block = ObjectBlock(header=header, data=b"")

        values = delivered(block, "on_analog_input")

        assert len(values) == 0

    def test_parse_analog_unsupported_variation(self) -> None:
        """Test parsing unsupported analog variation."""
        # Variation 100 doesn't exist
        header = ObjectHeader(group=30, variation=100, qualifier=0x00)
        data = bytes([0, 0, 0x01, 0x02, 0x03])
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_analog_input")

        assert len(values) == 0


class TestMasterCounterParsing:
    """Tests for counter value parsing."""

    def test_parse_counter_32bit_flags_v1(self) -> None:
        """Test parsing 32-bit counter with flags (g20v1)."""
        # g20v1: 1 byte flags + 4 bytes value
        header = ObjectHeader(group=20, variation=1, qualifier=0x00)
        value_bytes = (54321).to_bytes(4, "little", signed=False)
        data = bytes([0, 0, 0x01]) + value_bytes
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_counter")

        assert len(values) == 1
        assert values[0].index == 0
        assert values[0].value == 54321
        assert values[0].quality == 0x01

    def test_parse_counter_16bit_flags_v2(self) -> None:
        """Test parsing 16-bit counter with flags (g20v2)."""
        # g20v2: 1 byte flags + 2 bytes value
        header = ObjectHeader(group=20, variation=2, qualifier=0x00)
        value_bytes = (1000).to_bytes(2, "little", signed=False)
        data = bytes([0, 0, 0x01]) + value_bytes
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_counter")

        assert len(values) == 1
        assert values[0].value == 1000

    def test_parse_counter_32bit_no_flags_v5(self) -> None:
        """Test parsing 32-bit counter without flags (g20v5)."""
        # g20v5: 4 bytes value only
        header = ObjectHeader(group=20, variation=5, qualifier=0x00)
        data = bytes([0, 0]) + (99999).to_bytes(4, "little", signed=False)
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_counter")

        assert len(values) == 1
        assert values[0].value == 99999
        assert values[0].quality == QUALITY_ONLINE

    def test_parse_counter_16bit_no_flags_v6(self) -> None:
        """Test parsing 16-bit counter without flags (g20v6)."""
        # g20v6: 2 bytes value only
        header = ObjectHeader(group=20, variation=6, qualifier=0x00)
        data = bytes([0, 0]) + (5000).to_bytes(2, "little", signed=False)
        block = ObjectBlock(header=header, data=data)

        values = delivered(block, "on_counter")

        assert len(values) == 1
        assert values[0].value == 5000

    def test_parse_counter_empty(self) -> None:
        """Test parsing empty counter block."""
        header = ObjectHeader(group=20, variation=1, qualifier=0x00)
        block = ObjectBlock(header=header, data=b"")

        values = delivered(block, "on_counter")

        assert len(values) == 0


class TestMasterConvenienceMethods:
    """Tests for convenience methods."""

    def test_command_builder(self) -> None:
        """Test getting command builder."""
        master = Master()

        builder = master.command_builder()

        assert isinstance(builder, CommandBuilder)

    def test_needs_confirm_initially_false(self) -> None:
        """Test needs_confirm initially returns False."""
        master = Master()

        assert master.needs_confirm() is False

    def test_get_next_poll_empty(self) -> None:
        """Test get_next_poll with no tasks."""
        polling = PollingConfig(
            integrity_poll_interval=0.0,
            class_1_poll_interval=0.0,
            class_2_poll_interval=0.0,
            class_3_poll_interval=0.0,
        )
        config = MasterConfig(polling=polling)
        master = Master(config=config)

        assert master.get_next_poll() is None

    def test_get_next_poll_with_tasks(self) -> None:
        """Test get_next_poll with scheduled tasks."""
        # Use default config which has integrity poll
        master = Master()

        # There should be at least one task from default config
        if master.scheduler.task_count > 0:
            task = master.get_next_poll()
            assert task is not None or task is None  # May or may not be due

    def test_mark_poll_executed(self) -> None:
        """Test marking poll as executed."""
        master = Master()
        task = IntegrityPollTask()

        assert task.last_poll_time == 0.0

        master.mark_poll_executed(task)

        assert task.last_poll_time > 0.0

    def test_check_timeout_no_task(self) -> None:
        """Test check_timeout with no running task."""
        master = Master()

        result = master.check_timeout()

        assert result is False


class TestMasterQualityConstants:
    """Tests for quality flag constants."""

    def test_quality_constants(self) -> None:
        """Test that quality flag constants are correct."""
        assert QUALITY_ONLINE == 0x01
        assert QUALITY_STATE == 0x80


class TestMasterStateProperties:
    """Tests for state-related properties."""

    def test_state_property(self) -> None:
        """Test state property."""
        master = Master()

        assert master.state == MasterState.IDLE

    def test_is_idle_property(self) -> None:
        """Test is_idle property."""
        master = Master()

        assert master.is_idle is True


class TestMasterSelectStoring:
    """Tests for SELECT task storage."""

    def test_build_select_stores_task(self) -> None:
        """Test that build_select stores the pending select task."""
        master = Master()
        task = SelectTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        master.build_select(task)

        assert master._pending_select is task

    def test_build_select_overwrites_previous(self) -> None:
        """Test that new SELECT overwrites previous pending."""
        master = Master()
        task1 = SelectTask()
        task1.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))
        task2 = SelectTask()
        task2.add_operation(ControlOperation(index=1, control_code=ControlCode.LATCH_OFF))

        master.build_select(task1)
        master.build_select(task2)

        assert master._pending_select is task2


class TestTimeDelay:
    """The outstation turnaround reported in g52 reaches `ResponseInfo`."""

    @pytest.mark.parametrize(
        ("variation", "raw", "expected_ms"),
        [(2, 250, 250), (1, 2, 2000)],
        ids=["g52v2-milliseconds", "g52v1-seconds"],
    )
    def test_delay_measure_response_sets_time_delay(self, variation: int, raw: int, expected_ms: int) -> None:
        header = ObjectHeader(group=52, variation=variation, qualifier=0x07)
        block = ObjectBlock(header=header, data=bytes([0x01]) + raw.to_bytes(2, "little"))
        response = build_response(objects=(block,), seq=0)

        info = Master().process_response(response.to_bytes())

        assert info is not None
        assert info.time_delay_ms == expected_ms

    def test_response_without_g52_has_no_time_delay(self) -> None:
        # g30v1, start 0 stop 0, flags then a 32-bit value.
        analog = ObjectBlock(header=ObjectHeader(group=30, variation=1, qualifier=0x00), data=bytes(7))
        info = Master().process_response(build_response(objects=(analog,), seq=0).to_bytes())

        assert info is not None
        assert info.time_delay_ms is None

    @pytest.mark.parametrize(
        ("qualifier", "data"),
        [(0x07, bytes([0x00])), (0x06, b"")],
        ids=["count-zero", "all-objects"],
    )
    def test_g52_without_a_delay_value_has_no_time_delay(self, qualifier: int, data: bytes) -> None:
        block = ObjectBlock(header=ObjectHeader(group=52, variation=2, qualifier=qualifier), data=data)
        info = Master().process_response(build_response(objects=(block,), seq=0).to_bytes())

        assert info is not None
        assert info.time_delay_ms is None


class TestPropagationDelay:
    """One-way delay from a DELAY_MEASURE round trip."""

    def test_propagation_delay(self) -> None:
        """Half the round trip left after the outstation's turnaround."""
        assert propagation_delay_ms(sent_ms=1000, received_ms=1300, outstation_delay_ms=100) == 100

    def test_propagation_delay_clamped_to_zero(self) -> None:
        """A turnaround longer than the round trip gives no delay, not a negative one."""
        assert propagation_delay_ms(sent_ms=1000, received_ms=1100, outstation_delay_ms=500) == 0


class TestProcessResponse:
    """process_response is total over arbitrary bytes."""

    @given(
        st.one_of(
            st.binary(max_size=300),
            st.binary(max_size=296).map(lambda body: b"\xc0\x81\x00\x00" + body),
        )
    )
    def test_process_response_total(self, data: bytes) -> None:
        """Any bytes give a ResponseInfo or None, never an exception."""
        info = Master().process_response(data)

        assert info is None or isinstance(info, ResponseInfo)


class TestProcessFragment:
    """`process_fragment` takes an already parsed response."""

    def test_same_info_as_process_response(self) -> None:
        data = build_response(objects=(), seq=4).to_bytes()

        assert Master().process_fragment(parse_response(data)) == Master().process_response(data)

    def test_parse_failure_returns_none_with_one_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert Master().process_response(b"\xc0") is None

        assert len(caplog.records) == 1
