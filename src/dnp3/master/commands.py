"""Command operations for DNP3 master.

Provides classes for building and executing control commands
including SELECT, OPERATE, and DIRECT_OPERATE.
"""

import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto

from dnp3.application.builder import (
    build_direct_operate_request,
    build_operate_request,
    build_select_request,
)
from dnp3.application.fragment import ObjectBlock, RequestFragment
from dnp3.application.qualifiers import ObjectHeader
from dnp3.core.enums import ControlCode

# CROB group/variation
CROB_GROUP = 12
CROB_VARIATION = 1

# Analog output group/variation
ANALOG_OUTPUT_GROUP = 41
ANALOG_OUTPUT_16_VARIATION = 2  # 16-bit
ANALOG_OUTPUT_32_VARIATION = 1  # 32-bit
ANALOG_OUTPUT_FLOAT_VARIATION = 3  # Single float
ANALOG_OUTPUT_DOUBLE_VARIATION = 4  # Double float

# Qualifier constants
QUALIFIER_1BYTE_INDEX = 0x17  # 1-byte count, 1-byte index prefix
QUALIFIER_2BYTE_INDEX = 0x28  # 2-byte count, 2-byte index prefix
MAX_1BYTE_INDEX = 255  # Maximum index for 1-byte qualifier


class ControlMode(Enum):
    """Mode of control operation."""

    SELECT_BEFORE_OPERATE = auto()  # Two-step: SELECT then OPERATE
    DIRECT_OPERATE = auto()  # Single-step operation
    DIRECT_OPERATE_NO_ACK = auto()  # No acknowledgment


@dataclass(frozen=True)
class ControlOperation:
    """A control operation to perform.

    Attributes:
        index: Point index.
        control_code: Control code for binary outputs.
        count: Operation count.
        on_time: On time in milliseconds.
        off_time: Off time in milliseconds.
        analog_value: Value for analog outputs.
        is_analog: True if this is an analog output.
    """

    index: int
    control_code: ControlCode = ControlCode.LATCH_ON
    count: int = 1
    on_time: int = 0
    off_time: int = 0
    analog_value: float = 0.0
    is_analog: bool = False


@dataclass
class CommandTask(ABC):
    """Base class for command tasks.

    Attributes:
        operations: List of control operations.
        mode: Control mode.
    """

    operations: list[ControlOperation] = field(default_factory=list)
    mode: ControlMode = ControlMode.SELECT_BEFORE_OPERATE

    @abstractmethod
    def build_request(self, seq: int = 0) -> RequestFragment:
        """Build the command request.

        Args:
            seq: Sequence number for request.

        Returns:
            Request fragment for this command.
        """
        ...

    def add_operation(self, operation: ControlOperation) -> None:
        """Add an operation to this command.

        Args:
            operation: Control operation to add.
        """
        self.operations.append(operation)

    def _build_control_blocks(self) -> list[ObjectBlock]:
        """Build a CROB block and an analog output block, omitting either when it has no operations."""
        crobs = [
            (op.index, struct.pack("<BBIIB", int(op.control_code), op.count, op.on_time, op.off_time, 0))
            for op in self.operations
            if not op.is_analog
        ]
        analogs = [(op.index, struct.pack("<iB", int(op.analog_value), 0)) for op in self.operations if op.is_analog]

        blocks = [_prefixed_block(CROB_GROUP, CROB_VARIATION, crobs)] if crobs else []
        if analogs:
            blocks.append(_prefixed_block(ANALOG_OUTPUT_GROUP, ANALOG_OUTPUT_32_VARIATION, analogs))
        return blocks


def _prefixed_block(group: int, variation: int, items: list[tuple[int, bytes]]) -> ObjectBlock:
    """Build an index-prefixed block from (index, object body) pairs."""
    if max(index for index, _ in items) <= MAX_1BYTE_INDEX:
        qualifier, index_size = QUALIFIER_1BYTE_INDEX, 1
    else:
        qualifier, index_size = QUALIFIER_2BYTE_INDEX, 2

    # Qualifier 0x28 requires a 2-byte count (IEEE 1815-2012 Table 4-3), but this still writes 1 byte.
    data = bytes([len(items)]) + b"".join(index.to_bytes(index_size, "little") + body for index, body in items)
    return ObjectBlock(header=ObjectHeader(group=group, variation=variation, qualifier=qualifier), data=data)


@dataclass
class SelectTask(CommandTask):
    """SELECT request for select-before-operate.

    First step of two-step control operation.
    """

    mode: ControlMode = field(default=ControlMode.SELECT_BEFORE_OPERATE, init=False)

    def build_request(self, seq: int = 0) -> RequestFragment:
        """Build SELECT request."""
        return build_select_request(objects=tuple(self._build_control_blocks()), seq=seq)


@dataclass
class OperateTask(CommandTask):
    """OPERATE request for select-before-operate.

    Second step of two-step control operation.
    """

    mode: ControlMode = field(default=ControlMode.SELECT_BEFORE_OPERATE, init=False)

    def build_request(self, seq: int = 0) -> RequestFragment:
        """Build OPERATE request."""
        return build_operate_request(objects=tuple(self._build_control_blocks()), seq=seq)


@dataclass
class DirectOperateTask(CommandTask):
    """DIRECT_OPERATE request for single-step control.

    Performs operation without prior SELECT.
    """

    mode: ControlMode = field(default=ControlMode.DIRECT_OPERATE, init=False)

    def build_request(self, seq: int = 0) -> RequestFragment:
        """Build DIRECT_OPERATE request."""
        return build_direct_operate_request(objects=tuple(self._build_control_blocks()), seq=seq)


class CommandBuilder:
    """Builder for creating control commands.

    Provides a fluent interface for building control operations.
    """

    def __init__(self) -> None:
        """Initialize the builder."""
        self._operations: list[ControlOperation] = []

    def add_crob(
        self,
        index: int,
        code: ControlCode,
        count: int = 1,
        on_time: int = 0,
        off_time: int = 0,
    ) -> "CommandBuilder":
        """Add a CROB operation.

        Args:
            index: Point index.
            code: Control code.
            count: Operation count.
            on_time: On time in milliseconds.
            off_time: Off time in milliseconds.

        Returns:
            Self for chaining.
        """
        self._operations.append(
            ControlOperation(
                index=index,
                control_code=code,
                count=count,
                on_time=on_time,
                off_time=off_time,
                is_analog=False,
            )
        )
        return self

    def add_analog(self, index: int, value: float) -> "CommandBuilder":
        """Add an analog output operation.

        Args:
            index: Point index.
            value: Analog value.

        Returns:
            Self for chaining.
        """
        self._operations.append(
            ControlOperation(
                index=index,
                analog_value=value,
                is_analog=True,
            )
        )
        return self

    def latch_on(self, index: int) -> "CommandBuilder":
        """Add a latch-on operation.

        Args:
            index: Point index.

        Returns:
            Self for chaining.
        """
        return self.add_crob(index, ControlCode.LATCH_ON)

    def latch_off(self, index: int) -> "CommandBuilder":
        """Add a latch-off operation.

        Args:
            index: Point index.

        Returns:
            Self for chaining.
        """
        return self.add_crob(index, ControlCode.LATCH_OFF)

    def pulse_on(self, index: int, on_time: int = 1000, off_time: int = 0, count: int = 1) -> "CommandBuilder":
        """Add a pulse-on operation.

        Args:
            index: Point index.
            on_time: On time in milliseconds.
            off_time: Off time in milliseconds.
            count: Number of pulses.

        Returns:
            Self for chaining.
        """
        return self.add_crob(index, ControlCode.PULSE_ON, count, on_time, off_time)

    def pulse_off(self, index: int, on_time: int = 0, off_time: int = 1000, count: int = 1) -> "CommandBuilder":
        """Add a pulse-off operation.

        Args:
            index: Point index.
            on_time: On time in milliseconds.
            off_time: Off time in milliseconds.
            count: Number of pulses.

        Returns:
            Self for chaining.
        """
        return self.add_crob(index, ControlCode.PULSE_OFF, count, on_time, off_time)

    def build_select(self) -> SelectTask:
        """Build a SELECT task.

        Returns:
            SelectTask with all operations.
        """
        task = SelectTask()
        task.operations = self._operations.copy()
        return task

    def build_operate(self) -> OperateTask:
        """Build an OPERATE task.

        Returns:
            OperateTask with all operations.
        """
        task = OperateTask()
        task.operations = self._operations.copy()
        return task

    def build_direct_operate(self) -> DirectOperateTask:
        """Build a DIRECT_OPERATE task.

        Returns:
            DirectOperateTask with all operations.
        """
        task = DirectOperateTask()
        task.operations = self._operations.copy()
        return task

    def clear(self) -> "CommandBuilder":
        """Clear all operations.

        Returns:
            Self for chaining.
        """
        self._operations.clear()
        return self
