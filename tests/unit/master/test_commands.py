"""Tests for command operations."""

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dnp3.core.enums import ControlCode, FunctionCode
from dnp3.master.commands import (
    ANALOG_OUTPUT_32_VARIATION,
    ANALOG_OUTPUT_GROUP,
    CROB_GROUP,
    CROB_VARIATION,
    AnalogOutputVariation,
    CommandBuilder,
    CommandTask,
    ControlMode,
    ControlOperation,
    DirectOperateTask,
    OperateTask,
    SelectTask,
    _prefixed_block,
)


class TestControlMode:
    """Tests for ControlMode enum."""

    def test_modes_exist(self) -> None:
        """Test all expected modes exist."""
        assert ControlMode.SELECT_BEFORE_OPERATE is not None
        assert ControlMode.DIRECT_OPERATE is not None
        assert ControlMode.DIRECT_OPERATE_NO_ACK is not None

    def test_modes_are_distinct(self) -> None:
        """Test modes are distinct values."""
        modes = [
            ControlMode.SELECT_BEFORE_OPERATE,
            ControlMode.DIRECT_OPERATE,
            ControlMode.DIRECT_OPERATE_NO_ACK,
        ]
        assert len(set(modes)) == 3


class TestControlOperation:
    """Tests for ControlOperation."""

    def test_default_values(self) -> None:
        """Test default operation values."""
        op = ControlOperation(index=0)

        assert op.index == 0
        assert op.control_code == ControlCode.LATCH_ON
        assert op.count == 1
        assert op.on_time == 0
        assert op.off_time == 0
        assert op.analog_value == 0.0
        assert op.is_analog is False

    def test_binary_operation(self) -> None:
        """Test binary control operation."""
        op = ControlOperation(
            index=5,
            control_code=ControlCode.PULSE_ON,
            count=3,
            on_time=1000,
            off_time=500,
        )

        assert op.index == 5
        assert op.control_code == ControlCode.PULSE_ON
        assert op.count == 3
        assert op.on_time == 1000
        assert op.off_time == 500
        assert op.is_analog is False

    def test_analog_operation(self) -> None:
        """Test analog control operation."""
        op = ControlOperation(
            index=10,
            analog_value=123.45,
            is_analog=True,
            analog_variation=AnalogOutputVariation.DOUBLE64,
        )

        assert op.index == 10
        assert op.analog_value == 123.45
        assert op.is_analog is True

    def test_is_frozen(self) -> None:
        """Test that ControlOperation is immutable."""
        op = ControlOperation(index=0)
        with pytest.raises(AttributeError):
            op.index = 1  # type: ignore[misc]

    @given(
        index=st.integers(min_value=0, max_value=65535),
        count=st.integers(min_value=1, max_value=255),
        on_time=st.integers(min_value=0, max_value=2**32 - 1),
        off_time=st.integers(min_value=0, max_value=2**32 - 1),
    )
    @settings(max_examples=50)
    def test_property_based_binary(self, index: int, count: int, on_time: int, off_time: int) -> None:
        """Test binary operation with various values."""
        op = ControlOperation(
            index=index,
            control_code=ControlCode.PULSE_ON,
            count=count,
            on_time=on_time,
            off_time=off_time,
        )

        assert op.index == index
        assert op.count == count
        assert op.on_time == on_time
        assert op.off_time == off_time


class TestSelectTask:
    """Tests for SelectTask."""

    def test_creation(self) -> None:
        """Test creating select task."""
        task = SelectTask()

        assert task.operations == []
        assert task.mode == ControlMode.SELECT_BEFORE_OPERATE

    def test_add_operation(self) -> None:
        """Test adding operations."""
        task = SelectTask()
        op1 = ControlOperation(index=0, control_code=ControlCode.LATCH_ON)
        op2 = ControlOperation(index=1, control_code=ControlCode.LATCH_OFF)

        task.add_operation(op1)
        task.add_operation(op2)

        assert len(task.operations) == 2
        assert task.operations[0] == op1
        assert task.operations[1] == op2

    def test_build_request_binary(self) -> None:
        """Test building SELECT request with binary operations."""
        task = SelectTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        fragment = task.build_request(seq=3)

        assert fragment.header.function == FunctionCode.SELECT
        assert fragment.header.control.seq == 3
        assert len(fragment.objects) == 1

    def test_build_request_analog(self) -> None:
        """Test building SELECT request with analog operations."""
        task = SelectTask()
        task.add_operation(ControlOperation(index=0, analog_value=100.0, is_analog=True))

        fragment = task.build_request(seq=4)

        assert fragment.header.function == FunctionCode.SELECT
        assert len(fragment.objects) == 1

    def test_build_request_mixed(self) -> None:
        """Test building SELECT request with mixed operations."""
        task = SelectTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))
        task.add_operation(ControlOperation(index=0, analog_value=50.0, is_analog=True))

        fragment = task.build_request(seq=5)

        assert fragment.header.function == FunctionCode.SELECT
        assert len(fragment.objects) == 2  # Binary block + analog block

    def test_build_request_small_index(self) -> None:
        """Test building request with small index (1-byte)."""
        task = SelectTask()
        task.add_operation(ControlOperation(index=100, control_code=ControlCode.LATCH_ON))

        fragment = task.build_request()

        # Should use 1-byte index qualifier (0x17)
        assert fragment.objects[0].header.qualifier == 0x17

    def test_build_request_large_index(self) -> None:
        """Test building request with large index (2-byte)."""
        task = SelectTask()
        task.add_operation(ControlOperation(index=500, control_code=ControlCode.LATCH_ON))

        fragment = task.build_request()

        # Should use 2-byte index qualifier (0x28)
        assert fragment.objects[0].header.qualifier == 0x28

    def test_build_request_more_than_255_operations(self) -> None:
        """A count above 255 needs the 2-byte count of 0x28 even when every index fits in one byte."""
        task = SelectTask(operations=[ControlOperation(index=i % 256) for i in range(256)])

        block = task.build_request().objects[0]

        assert block.header.qualifier == 0x28
        assert block.data[:2] == (256).to_bytes(2, "little")


class TestOperateTask:
    """Tests for OperateTask."""

    def test_creation(self) -> None:
        """Test creating operate task."""
        task = OperateTask()

        assert task.operations == []
        assert task.mode == ControlMode.SELECT_BEFORE_OPERATE

    def test_build_request_binary(self) -> None:
        """Test building OPERATE request with binary operations."""
        task = OperateTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        fragment = task.build_request(seq=6)

        assert fragment.header.function == FunctionCode.OPERATE
        assert fragment.header.control.seq == 6

    def test_build_request_analog(self) -> None:
        """Test building OPERATE request with analog operations."""
        task = OperateTask()
        task.add_operation(ControlOperation(index=0, analog_value=75, is_analog=True))

        fragment = task.build_request(seq=7)

        assert fragment.header.function == FunctionCode.OPERATE

    def test_build_request_mixed(self) -> None:
        """Test building OPERATE request with mixed operations."""
        task = OperateTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.PULSE_ON))
        task.add_operation(ControlOperation(index=1, analog_value=25.0, is_analog=True))

        fragment = task.build_request()

        assert fragment.header.function == FunctionCode.OPERATE
        assert len(fragment.objects) == 2


class TestDirectOperateTask:
    """Tests for DirectOperateTask."""

    def test_creation(self) -> None:
        """Test creating direct operate task."""
        task = DirectOperateTask()

        assert task.operations == []
        assert task.mode == ControlMode.DIRECT_OPERATE

    def test_build_request_binary(self) -> None:
        """Test building DIRECT_OPERATE request."""
        task = DirectOperateTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))

        fragment = task.build_request(seq=8)

        assert fragment.header.function == FunctionCode.DIRECT_OPERATE
        assert fragment.header.control.seq == 8

    def test_build_request_analog(self) -> None:
        """Test building DIRECT_OPERATE request with analog."""
        task = DirectOperateTask()
        task.add_operation(ControlOperation(index=2, analog_value=-50.0, is_analog=True))

        fragment = task.build_request()

        assert fragment.header.function == FunctionCode.DIRECT_OPERATE

    def test_multiple_binary_operations(self) -> None:
        """Test multiple binary operations in one request."""
        task = DirectOperateTask()
        task.add_operation(ControlOperation(index=0, control_code=ControlCode.LATCH_ON))
        task.add_operation(ControlOperation(index=1, control_code=ControlCode.LATCH_OFF))
        task.add_operation(ControlOperation(index=2, control_code=ControlCode.PULSE_ON))

        fragment = task.build_request()

        assert fragment.header.function == FunctionCode.DIRECT_OPERATE
        assert len(fragment.objects) == 1  # All in one CROB block


class TestCommandBuilder:
    """Tests for CommandBuilder."""

    def test_empty_builder(self) -> None:
        """Test empty command builder."""
        builder = CommandBuilder()

        select = builder.build_select()
        assert len(select.operations) == 0

    def test_add_crob(self) -> None:
        """Test adding CROB operation."""
        builder = CommandBuilder()

        builder.add_crob(
            index=5,
            code=ControlCode.PULSE_ON,
            count=2,
            on_time=1000,
            off_time=500,
        )

        select = builder.build_select()
        assert len(select.operations) == 1
        op = select.operations[0]
        assert op.index == 5
        assert op.control_code == ControlCode.PULSE_ON
        assert op.count == 2
        assert op.on_time == 1000
        assert op.off_time == 500
        assert op.is_analog is False

    def test_add_analog(self) -> None:
        """Test adding analog operation."""
        builder = CommandBuilder()

        builder.add_analog(index=10, value=123.45, variation=AnalogOutputVariation.DOUBLE64)

        select = builder.build_select()
        assert len(select.operations) == 1
        op = select.operations[0]
        assert op.index == 10
        assert op.analog_value == 123.45
        assert op.is_analog is True
        assert op.analog_variation == AnalogOutputVariation.DOUBLE64

    def test_latch_on(self) -> None:
        """Test latch_on convenience method."""
        builder = CommandBuilder()

        builder.latch_on(index=3)

        select = builder.build_select()
        op = select.operations[0]
        assert op.index == 3
        assert op.control_code == ControlCode.LATCH_ON

    def test_latch_off(self) -> None:
        """Test latch_off convenience method."""
        builder = CommandBuilder()

        builder.latch_off(index=4)

        select = builder.build_select()
        op = select.operations[0]
        assert op.index == 4
        assert op.control_code == ControlCode.LATCH_OFF

    def test_pulse_on(self) -> None:
        """Test pulse_on convenience method."""
        builder = CommandBuilder()

        builder.pulse_on(index=5, on_time=2000, off_time=1000, count=3)

        select = builder.build_select()
        op = select.operations[0]
        assert op.index == 5
        assert op.control_code == ControlCode.PULSE_ON
        assert op.on_time == 2000
        assert op.off_time == 1000
        assert op.count == 3

    def test_pulse_on_defaults(self) -> None:
        """Test pulse_on with default values."""
        builder = CommandBuilder()

        builder.pulse_on(index=6)

        select = builder.build_select()
        op = select.operations[0]
        assert op.on_time == 1000
        assert op.off_time == 0
        assert op.count == 1

    def test_pulse_off(self) -> None:
        """Test pulse_off convenience method."""
        builder = CommandBuilder()

        builder.pulse_off(index=7, on_time=500, off_time=2000, count=2)

        select = builder.build_select()
        op = select.operations[0]
        assert op.index == 7
        assert op.control_code == ControlCode.PULSE_OFF
        assert op.on_time == 500
        assert op.off_time == 2000
        assert op.count == 2

    def test_pulse_off_defaults(self) -> None:
        """Test pulse_off with default values."""
        builder = CommandBuilder()

        builder.pulse_off(index=8)

        select = builder.build_select()
        op = select.operations[0]
        assert op.on_time == 0
        assert op.off_time == 1000
        assert op.count == 1

    def test_chaining(self) -> None:
        """Test method chaining."""
        builder = CommandBuilder()

        result = builder.latch_on(0).latch_off(1).pulse_on(2, on_time=1000).add_analog(3, 50.0)

        # Should return self for chaining
        assert result is builder

        select = builder.build_select()
        assert len(select.operations) == 4

    def test_build_select(self) -> None:
        """Test building SELECT task."""
        builder = CommandBuilder()
        builder.latch_on(0)

        task = builder.build_select()

        assert isinstance(task, SelectTask)
        assert len(task.operations) == 1

    def test_build_operate(self) -> None:
        """Test building OPERATE task."""
        builder = CommandBuilder()
        builder.latch_on(0)

        task = builder.build_operate()

        assert isinstance(task, OperateTask)
        assert len(task.operations) == 1

    def test_build_direct_operate(self) -> None:
        """Test building DIRECT_OPERATE task."""
        builder = CommandBuilder()
        builder.latch_on(0)

        task = builder.build_direct_operate()

        assert isinstance(task, DirectOperateTask)
        assert len(task.operations) == 1

    def test_build_copies_operations(self) -> None:
        """Test that build methods copy operations."""
        builder = CommandBuilder()
        builder.latch_on(0)

        select = builder.build_select()
        builder.latch_off(1)  # Add more after building

        # Original select should not be affected
        assert len(select.operations) == 1

    def test_clear(self) -> None:
        """Test clearing operations."""
        builder = CommandBuilder()
        builder.latch_on(0).latch_off(1).add_analog(2, 100.0)

        result = builder.clear()

        # Should return self
        assert result is builder

        # Should be empty
        select = builder.build_select()
        assert len(select.operations) == 0

    @given(
        indices=st.lists(st.integers(min_value=0, max_value=65535), min_size=1, max_size=10),
    )
    @settings(max_examples=25)
    def test_property_based_multiple_operations(self, indices: list[int]) -> None:
        """Test building with multiple operations."""
        builder = CommandBuilder()

        for idx in indices:
            builder.latch_on(idx)

        task = builder.build_direct_operate()
        assert len(task.operations) == len(indices)

        # All operations should be binary
        for op in task.operations:
            assert op.is_analog is False


class TestCommandTaskPolymorphism:
    """Tests for CommandTask base class behavior."""

    def test_all_task_types_build_request(self) -> None:
        """Test all task types can build requests."""
        op = ControlOperation(index=0, control_code=ControlCode.LATCH_ON)

        select = SelectTask()
        select.add_operation(op)

        operate = OperateTask()
        operate.add_operation(op)

        direct = DirectOperateTask()
        direct.add_operation(op)

        tasks: list[CommandTask] = [select, operate, direct]

        expected_functions = [
            FunctionCode.SELECT,
            FunctionCode.OPERATE,
            FunctionCode.DIRECT_OPERATE,
        ]

        for task, expected_func in zip(tasks, expected_functions, strict=False):
            fragment = task.build_request()
            assert fragment.header.function == expected_func

    def test_object_block_structure(self) -> None:
        """Test that object blocks have correct structure."""
        task = DirectOperateTask()
        task.add_operation(
            ControlOperation(
                index=5,
                control_code=ControlCode.PULSE_ON,
                count=2,
                on_time=1000,
                off_time=500,
            )
        )

        fragment = task.build_request()
        block = fragment.objects[0]

        assert block.header.group == CROB_GROUP
        assert block.header.variation == CROB_VARIATION

    def test_analog_block_structure(self) -> None:
        """Test that analog blocks have correct structure."""
        task = DirectOperateTask()
        task.add_operation(ControlOperation(index=3, analog_value=1000, is_analog=True))

        fragment = task.build_request()
        block = fragment.objects[0]

        assert block.header.group == ANALOG_OUTPUT_GROUP
        assert block.header.variation == ANALOG_OUTPUT_32_VARIATION

    @pytest.mark.parametrize(
        ("high_index", "expected"),
        [
            (
                300,
                [
                    bytes.fromhex(
                        "0c0128" + "0200" + "0300" + "0102e8030000f401000000" + "2c01" + "0401000000000000000000"
                    ),
                    bytes.fromhex("290128" + "0200" + "0300" + "2efbffff00" + "2c01" + "7011010000"),
                ],
            ),
            (
                255,
                [
                    bytes.fromhex("0c0117" + "02" + "03" + "0102e8030000f401000000" + "ff" + "0401000000000000000000"),
                    bytes.fromhex("290117" + "02" + "03" + "2efbffff00" + "ff" + "7011010000"),
                ],
            ),
        ],
        ids=["0x28", "0x17"],
    )
    def test_all_task_types_encode_identical_blocks(self, high_index: int, expected: list[bytes]) -> None:
        """SELECT, OPERATE and DIRECT_OPERATE carry byte-identical object blocks."""
        operations = [
            ControlOperation(index=3, control_code=ControlCode.PULSE_ON, count=2, on_time=1000, off_time=500),
            ControlOperation(index=high_index, control_code=ControlCode.LATCH_OFF),
            ControlOperation(index=3, analog_value=-1234, is_analog=True),
            ControlOperation(index=high_index, analog_value=70000, is_analog=True),
        ]

        for task_type in (SelectTask, OperateTask, DirectOperateTask):
            fragment = task_type(operations=list(operations)).build_request()
            assert [block.to_bytes() for block in fragment.objects] == expected, task_type.__name__


class TestAnalogVariations:
    """Analog output commands are encoded in the g41 variation the caller picks."""

    def test_default_is_int32(self) -> None:
        """The default variation is g41v1, a 32-bit integer then the status octet."""
        task = CommandBuilder().add_analog(3, 42).build_direct_operate()
        block = task.build_request().objects[0]

        assert (block.header.group, block.header.variation) == (41, 1)
        assert block.data == bytes.fromhex("01" + "03" + "2a00000000")

    @pytest.mark.parametrize(
        ("variation", "value", "body"),
        [
            (AnalogOutputVariation.INT16, -2, "feff00"),
            (AnalogOutputVariation.FLOAT32, 12.5, "0000484100"),
            (AnalogOutputVariation.DOUBLE64, 12.5, "000000000000294000"),
        ],
        ids=["int16", "float32", "double64"],
    )
    def test_variation_encoding(self, variation: AnalogOutputVariation, value: float, body: str) -> None:
        """Each variation encodes the value in its own width (IEEE 1815-2012 A.20)."""
        task = CommandBuilder().add_analog(7, value, variation=variation).build_direct_operate()
        block = task.build_request().objects[0]

        assert (block.header.group, block.header.variation) == (41, int(variation))
        assert block.data == bytes.fromhex("01" + "07" + body)

    def test_mixed_variations_one_block_each(self) -> None:
        """Operations are grouped into one block per variation, in first-appearance order, after the CROB block."""
        task = (
            CommandBuilder()
            .add_analog(0, 1)
            .latch_on(5)
            .add_analog(1, 2.5, variation=AnalogOutputVariation.FLOAT32)
            .add_analog(2, 3)
            .build_direct_operate()
        )
        blocks = task.build_request().objects

        assert [(b.header.group, b.header.variation) for b in blocks] == [(12, 1), (41, 1), (41, 3)]
        assert blocks[1].data == bytes.fromhex("02" + "00" + "0100000000" + "02" + "0300000000")
        assert blocks[2].data == bytes.fromhex("01" + "01" + "0000204000")


class TestAnalogValueChecks:
    """A value the chosen variation cannot carry is refused when the operation is created."""

    def test_fractional_value_rejected_for_integer_variation(self) -> None:
        """12.7 is refused for INT32 rather than truncated; 12.0 is sent as 12."""
        with pytest.raises(ValueError, match="FLOAT32"):
            CommandBuilder().add_analog(0, 12.7)

        block = CommandBuilder().add_analog(0, 12.0).build_direct_operate().build_request().objects[0]
        assert block.data == bytes.fromhex("01" + "00" + "0c00000000")

    @pytest.mark.parametrize(
        ("variation", "value"),
        [(AnalogOutputVariation.INT16, 40000), (AnalogOutputVariation.INT32, 2**31)],
        ids=["int16", "int32"],
    )
    def test_out_of_range_rejected_on_creation(self, variation: AnalogOutputVariation, value: int) -> None:
        """An integer outside the variation's range raises from add_analog, not from build_*."""
        with pytest.raises(ValueError):
            CommandBuilder().add_analog(0, value, variation=variation)

    @pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "inf"])
    def test_non_finite_rejected_for_integer_variation(self, value: float) -> None:
        """NaN and infinity have no integer encoding."""
        with pytest.raises(ValueError):
            CommandBuilder().add_analog(0, value)

    def test_non_float_numbers_checked_by_value(self) -> None:
        """Numbers that are neither int nor float (Decimal, Fraction, numpy scalars) are judged by their value."""
        block = CommandBuilder().add_analog(0, Decimal(12)).build_direct_operate().build_request().objects[0]
        assert block.data == bytes.fromhex("01" + "00" + "0c00000000")

        with pytest.raises(ValueError, match="FLOAT32"):
            CommandBuilder().add_analog(0, Fraction(5, 2))

    def test_float32_overflow_rejected(self) -> None:
        """A value beyond float32 range raises ValueError."""
        with pytest.raises(ValueError):
            CommandBuilder().add_analog(0, 1e39, variation=AnalogOutputVariation.FLOAT32)

    def test_direct_control_operation_checked(self) -> None:
        """ControlOperation built directly is checked too."""
        with pytest.raises(ValueError):
            ControlOperation(index=0, analog_value=12.7, is_analog=True)


class TestPrefixedBlock:
    """Tests for the index-prefixed block encoder."""

    def test_empty_items_encode_zero_count(self) -> None:
        """An empty block uses 0x17 with a zero count."""
        block = _prefixed_block(CROB_GROUP, CROB_VARIATION, [])

        assert block.header.qualifier == 0x17
        assert block.data == b"\x00"


class TestCROBConstants:
    """Tests for CROB constants."""

    def test_crob_group_variation(self) -> None:
        """Test CROB group/variation constants."""
        assert CROB_GROUP == 12
        assert CROB_VARIATION == 1

    def test_analog_output_constants(self) -> None:
        """Test analog output constants."""
        assert ANALOG_OUTPUT_GROUP == 41
        assert ANALOG_OUTPUT_32_VARIATION == 1
