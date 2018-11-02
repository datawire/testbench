# SPDX-License-Identifier: LGPL-2.1+

import contextlib
import os
import uuid
from subprocess import run
from typing import Dict, Iterator, Optional, Tuple

from .cli import CommandLineArguments
from .disk import ensured_partition, partition
from .types import OutputFormat
from .ui import complete_step


def luks_format(dev: str, passphrase: Dict[str, str]) -> None:

    if passphrase['type'] == 'stdin':
        passphrase_content = (passphrase['content'] + "\n").encode("utf-8")
        run(["cryptsetup", "luksFormat", "--batch-mode", dev], input=passphrase_content, check=True)
    else:
        assert passphrase['type'] == 'file'
        run(["cryptsetup", "luksFormat", "--batch-mode", dev, passphrase['content']], check=True)

def luks_open(dev: str, passphrase: Dict[str, str]) -> str:

    name = str(uuid.uuid4())

    if passphrase['type'] == 'stdin':
        passphrase_content = (passphrase['content'] + "\n").encode("utf-8")
        run(["cryptsetup", "open", "--type", "luks", dev, name], input=passphrase_content, check=True)
    else:
        assert passphrase['type'] == 'file'
        run(["cryptsetup", "--key-file", passphrase['content'], "open", "--type", "luks", dev, name], check=True)

    return os.path.join("/dev/mapper", name)

def luks_close(dev: Optional[str], text: str) -> None:
    if dev is None:
        return

    with complete_step(text):
        run(["cryptsetup", "close", dev], check=True)

def luks_format_root(args: CommandLineArguments, loopdev: str, run_build_script: bool, cached: bool, inserting_squashfs: bool=False) -> None:

    if args.encrypt != "all":
        return
    if args.root_partno is None:
        return
    if args.output_format == OutputFormat.raw_squashfs and not inserting_squashfs:
        return
    if run_build_script:
        return
    if cached:
        return

    with complete_step("LUKS formatting root partition"):
        luks_format(ensured_partition(loopdev, args.root_partno), args.passphrase)

def luks_format_home(args: CommandLineArguments, loopdev: str, run_build_script: bool, cached: bool) -> None:

    if args.encrypt is None:
        return
    if args.home_partno is None:
        return
    if run_build_script:
        return
    if cached:
        return

    with complete_step("LUKS formatting home partition"):
        luks_format(ensured_partition(loopdev, args.home_partno), args.passphrase)

def luks_format_srv(args: CommandLineArguments, loopdev: str, run_build_script: bool, cached: bool) -> None:

    if args.encrypt is None:
        return
    if args.srv_partno is None:
        return
    if run_build_script:
        return
    if cached:
        return

    with complete_step("LUKS formatting server data partition"):
        luks_format(ensured_partition(loopdev, args.srv_partno), args.passphrase)

def luks_setup_root(args: CommandLineArguments, loopdev: str, run_build_script: bool, inserting_squashfs: bool=False) -> Optional[str]:

    if args.encrypt != "all":
        return None
    if args.root_partno is None:
        return None
    if args.output_format == OutputFormat.raw_squashfs and not inserting_squashfs:
        return None
    if run_build_script:
        return None

    with complete_step("Opening LUKS root partition"):
        return luks_open(ensured_partition(loopdev, args.root_partno), args.passphrase)

def luks_setup_home(args: CommandLineArguments, loopdev: str, run_build_script: bool) -> Optional[str]:

    if args.encrypt is None:
        return None
    if args.home_partno is None:
        return None
    if run_build_script:
        return None

    with complete_step("Opening LUKS home partition"):
        return luks_open(ensured_partition(loopdev, args.home_partno), args.passphrase)

def luks_setup_srv(args: CommandLineArguments, loopdev: str, run_build_script: bool) -> Optional[str]:

    if args.encrypt is None:
        return None
    if args.srv_partno is None:
        return None
    if run_build_script:
        return None

    with complete_step("Opening LUKS server data partition"):
        return luks_open(ensured_partition(loopdev, args.srv_partno), args.passphrase)

@contextlib.contextmanager
def luks_setup_all(args: CommandLineArguments, loopdev: Optional[str], run_build_script: bool) -> Iterator[Tuple[Optional[str], Optional[str], Optional[str]]]:

    if loopdev is None:
        assert args.output_format in (OutputFormat.directory, OutputFormat.subvolume, OutputFormat.tar)
        yield (None, None, None)
        return

    try:
        root = luks_setup_root(args, loopdev, run_build_script)
        try:
            home = luks_setup_home(args, loopdev, run_build_script)
            try:
                srv = luks_setup_srv(args, loopdev, run_build_script)

                yield (partition(loopdev, args.root_partno) if root is None else root,
                       partition(loopdev, args.home_partno) if home is None else home,
                       partition(loopdev, args.srv_partno) if srv is None else srv)
            finally:
                luks_close(srv, "Closing LUKS server data partition")
        finally:
            luks_close(home, "Closing LUKS home partition")
    finally:
        luks_close(root, "Closing LUKS root partition")
