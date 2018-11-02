# SPDX-License-Identifier: LGPL-2.1+

from typing import Optional


def partition(loopdev: str, partno: Optional[int]) -> Optional[str]:
    if partno is None:
        return None

    return ensured_partition(loopdev, partno)

def ensured_partition(loopdev: str, partno: int) -> str:
    return loopdev + "p" + str(partno)
