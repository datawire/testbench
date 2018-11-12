# SPDX-License-Identifier: LGPL-2.1+

import os
from subprocess import run

from ..luks import luks_setup_all
from ..types import CommandLineArguments
from .build import (
    attach_image_loopback,
    determine_partition_table,
    init_namespace,
    mount_image,
    setup_workspace,
)

NEEDS_ROOT = True
NEEDS_BUILD = True

def do(args: CommandLineArguments) -> None:
    init_namespace(args)
    determine_partition_table(args)
    workspace = setup_workspace(args)
    with open(args.output, 'rb') as raw:
        with attach_image_loopback(args, raw) as loopdev:
            with luks_setup_all(args, loopdev, False) as (encrypted_root, encrypted_home, encrypted_srv):
                with mount_image(args, workspace.name, loopdev, encrypted_root, encrypted_home, encrypted_srv):
                    run(args.cmdline, cwd=os.path.join(workspace.name, "root"), check=True)
