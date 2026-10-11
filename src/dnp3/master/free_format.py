"""One object behind qualifier 0x5B: a 1-octet count, a 2-octet object size, the object.

IEEE 1815-2012 Table 4-6 pairs the free-format range only with a size prefix.
Group 70 file objects and group 120 authentication objects travel this way,
always one object per header.
"""

from __future__ import annotations

import struct

from dnp3.application.fragment import ObjectBlock
from dnp3.application.qualifiers import ObjectHeader, PrefixCode, RangeCode

QUALIFIER = 0x5B
_PREFIX_SIZE = 3  # object count (1 octet) and object size (2 octets)


def build_block(group: int, variation: int, obj: bytes) -> ObjectBlock:
    """One object behind qualifier 0x5B."""
    header = ObjectHeader.build(
        group=group, variation=variation, prefix=PrefixCode.UINT16_SIZE, range_code=RangeCode.FREE_FORMAT
    )
    return ObjectBlock(header=header, data=b"\x01" + struct.pack("<H", len(obj)) + obj)


def object_octets(block: ObjectBlock) -> bytes:
    """The one object a qualifier 0x5B block carries.

    Raises:
        ValueError: Another qualifier, a count other than 1, or a size that
            does not match the octets that follow it.
    """
    name = f"g{block.header.group}v{block.header.variation}"
    if block.header.qualifier != QUALIFIER:
        msg = f"{name} with qualifier 0x{block.header.qualifier:02X}, expected 0x5B"
        raise ValueError(msg)
    data = block.data
    if len(data) < _PREFIX_SIZE or data[0] != 1:
        msg = f"{name} must carry exactly one object"
        raise ValueError(msg)
    size = int.from_bytes(data[1:_PREFIX_SIZE], "little")
    body = data[_PREFIX_SIZE:]
    if len(body) != size:
        msg = f"{name} object size {size} does not match its {len(body)} octets"
        raise ValueError(msg)
    return body
