"""Octet string values and their handler callback, per IEEE 1815-2012 Annex A g110 and g111.

An octet string carries raw bytes rather than a measurement, so it is delivered
on its own callback instead of on an ``SOEHandler`` method.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dnp3.master.handler import HeaderInfo, ResponseInfo

__all__ = [
    "OctetStringHandler",
    "OctetStringValue",
    "deliver_octet_string",
]


@dataclass(frozen=True)
class OctetStringValue:
    """Octet string value from a response (g110 static, g111 event).

    Attributes:
        index: Point index.
        value: The string's octets, with no implied text encoding.
        header: The object header the value was decoded from; None when built by hand.
            Left out of equality, so a decoded value equals one built from its fields.
    """

    index: int
    value: bytes
    header: HeaderInfo | None = field(default=None, compare=False)

    @property
    def timestamp(self) -> None:
        """Always None: g110 and g111 carry neither a time nor a quality flag."""
        return None


@runtime_checkable
class OctetStringHandler(Protocol):
    """A handler that accepts octet string values and events.

    Separate from ``SOEHandler`` so existing handlers need no new method; a
    handler without this callback is not given octet strings.
    """

    def on_octet_string(self, values: list[OctetStringValue], info: ResponseInfo) -> None:
        """Called when octet string values or events are received.

        Args:
            values: List of octet string values, in wire order.
            info: Response information.
        """
        ...


def deliver_octet_string(handler: object, values: list[OctetStringValue], info: ResponseInfo) -> None:
    """Hand values to a handler that implements `OctetStringHandler`; others get nothing."""
    if isinstance(handler, OctetStringHandler):
        handler.on_octet_string(values, info)
