# SPDX-License-Identifier: LGPL-2.1+

from enum import Enum


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
