# SPDX-License-Identifier: LGPL-2.1+

import os
import shutil

from ..docker import run_in_docker
from ..types import CommandLineArguments
from .build import init_namespace
from .withmount import osi_mount

NEEDS_ROOT = False
NEEDS_BUILD = True
HAS_ARGS = False
FORCE_UNLINKS = False

def do_inner(args: CommandLineArguments) -> None:
    init_namespace(args)
    with osi_mount(args) as mountpoint:
        shutil.copyfile(os.path.join(mountpoint, "var/log/testbench-run.tap"),
                        args.output[:-4])  # .tap.osi → .tap

def do(args: CommandLineArguments) -> None:
    assert args.output.endswith(".tap.osi")
    run_in_docker(do_inner, [args], docker_args=[
        "--privileged",  # needs to (1) have access to loop devices, (2) be able to mount things
        "--volume=/dev:/dev",  # https://github.com/moby/moby/issues/27886
        "--volume={path}:{path}".format(path=os.path.abspath(os.path.dirname(args.output)))
    ])