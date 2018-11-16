# SPDX-License-Identifier: LGPL-2.1+

import os
from subprocess import DEVNULL, PIPE, run

from .ui import run_visible


def btrfs_subvol_create(path: str, mode: int=0o755) -> None:
    m = os.umask(~mode & 0o7777)
    run_visible(["btrfs", "subvol", "create", path], check=True)
    os.umask(m)

def btrfs_subvol_delete(path: str) -> None:
    # Extract the path of the subvolume relative to the filesystem
    c = run(["btrfs", "subvol", "show", path],
            stdout=PIPE, stderr=DEVNULL, universal_newlines=True, check=True)
    subvol_path = c.stdout.splitlines()[0]
    # Make the subvolume RW again if it was set RO by btrfs_subvol_delete
    run_visible(["btrfs", "property", "set", path, "ro", "false"], check=True)
    # Recursively delete the direct children of the subvolume
    c = run(["btrfs", "subvol", "list", "-o", path],
            stdout=PIPE, stderr=DEVNULL, universal_newlines=True, check=True)
    for line in c.stdout.splitlines():
        if not line:
            continue
        child_subvol_path = line.split(" ", 8)[-1]
        child_path = os.path.normpath(os.path.join(
            path,
            os.path.relpath(child_subvol_path, subvol_path)
        ))
        btrfs_subvol_delete(child_path)
    # Delete the subvolume now that all its descendants have been deleted
    run(["btrfs", "subvol", "delete", path], stdout=DEVNULL, stderr=DEVNULL, check=True)

def btrfs_subvol_make_ro(path: str, b: bool=True) -> None:
    run_visible(["btrfs", "property", "set", path, "ro", "true" if b else "false"], check=True)
