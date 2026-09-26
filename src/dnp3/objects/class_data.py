"""Class Data objects per IEEE 1815-2012.

Group 60: Class Data
- Variation 1: Class 0 data (static data)
- Variation 2: Class 1 events
- Variation 3: Class 2 events
- Variation 4: Class 3 events

These objects are used in READ requests to request data by class.
They have no data content - they are identifiers only.
"""

from dataclasses import dataclass

from dnp3.objects.base import FixedSizeObject, StaticObject
from dnp3.objects.registry import register

# Group number
CLASS_DATA_GROUP = 60


@register
@dataclass(frozen=True, slots=True)
class ClassData0(FixedSizeObject, StaticObject):
    """Class 0 Data request (g60v1).

    Used to request all static (current) data.
    Has no data content - object header only.
    """

    GROUP = CLASS_DATA_GROUP
    VARIATION = 1
    FORMAT = "<"


@register
@dataclass(frozen=True, slots=True)
class ClassData1(FixedSizeObject, StaticObject):
    """Class 1 Data request (g60v2).

    Used to request Class 1 events.
    Has no data content - object header only.
    """

    GROUP = CLASS_DATA_GROUP
    VARIATION = 2
    FORMAT = "<"


@register
@dataclass(frozen=True, slots=True)
class ClassData2(FixedSizeObject, StaticObject):
    """Class 2 Data request (g60v3).

    Used to request Class 2 events.
    Has no data content - object header only.
    """

    GROUP = CLASS_DATA_GROUP
    VARIATION = 3
    FORMAT = "<"


@register
@dataclass(frozen=True, slots=True)
class ClassData3(FixedSizeObject, StaticObject):
    """Class 3 Data request (g60v4).

    Used to request Class 3 events.
    Has no data content - object header only.
    """

    GROUP = CLASS_DATA_GROUP
    VARIATION = 4
    FORMAT = "<"
