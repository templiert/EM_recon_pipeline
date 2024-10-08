"""XDim."""

from enum import StrEnum, auto


class XDim(StrEnum):
    """Main dimensions of the xarray relevant for data ingestion."""

    SCAN = auto()
    SLAB = auto()
    MFOV = auto()
    SFOV = auto()
    BIN = auto()
    """histogram bins. [0, ..., 255], inclusive."""
