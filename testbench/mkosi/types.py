# SPDX-License-Identifier: LGPL-2.1+

import argparse
from enum import Enum
from typing import Optional


class OutputFormat(Enum):
    raw_ext4 = 1
    raw_gpt = 1  # Kept for backwards compatibility
    raw_btrfs = 2
    raw_squashfs = 3
    directory = 4
    subvolume = 5
    tar = 6
    raw_xfs = 7

RAW_RW_FS_FORMATS = (
    OutputFormat.raw_ext4,
    OutputFormat.raw_btrfs,
    OutputFormat.raw_xfs
)

RAW_FORMATS = (*RAW_RW_FS_FORMATS, OutputFormat.raw_squashfs)

class CommandLineArguments(argparse.Namespace):
    """Type-hinted storage for command line arguments."""

    output: str
    swap_partno: Optional[int] = None
    esp_partno: Optional[int] = None
