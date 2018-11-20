# SPDX-License-Identifier: LGPL-2.1+

import os
import shlex
import shutil
import uuid

from ..docker import run_in_docker
from ..luks import luks_setup_all
from ..types import CommandLineArguments
from ..utils import run_workspace_command
from .build import (
    attach_image_loopback,
    determine_partition_table,
    init_namespace,
    install_build_src,
    mount_image,
    setup_workspace,
)

NEEDS_ROOT = False
NEEDS_BUILD = True
HAS_ARGS = True
FORCE_UNLINKS = True

def setup(args: CommandLineArguments, workspace: str, mountpoint: str) -> None:
    with open(os.path.join(mountpoint, 'etc/testbench-run'), 'w') as f:
        f.writelines(["#!/bin/sh\n",
                      " ".join(shlex.quote(arg) for arg in args.cmdline)+"\n"])
    os.chmod(os.path.join(mountpoint, 'etc/testbench-run'), 0o755)

    os.makedirs(os.path.join(mountpoint, 'home/testbench/.kube'), mode=0o755)
    shutil.copy(args.output[:-8]+".knaut",
                os.path.join(mountpoint, "home/testbench/.kube/config"))

    install_build_src(args, workspace, True, False)

    run_workspace_command(args, workspace,
                          "chown", "-R", "testbench:", "/home/testbench")

def do_inner(args: CommandLineArguments) -> None:
    args.machine_id = uuid.uuid4().hex
    init_namespace(args)
    determine_partition_table(args)
    with setup_workspace(args) as workspace:
        with open(args.output, 'rb') as raw:
            with attach_image_loopback(args, raw) as loopdev:
                with luks_setup_all(args, loopdev, False) as (encrypted_root, encrypted_home, encrypted_srv):
                    with mount_image(args, workspace, loopdev, encrypted_root, encrypted_home, encrypted_srv):
                        setup(args, workspace, os.path.join(workspace, "root"))

def do(args: CommandLineArguments) -> None:
    assert args.output.endswith(".tap.osi")

    dirs = [
        args.build_sources,
        args.cache_path,
        os.path.dirname(args.output),
    ]
    # Normalize
    dirs = [os.path.abspath(d) for d in dirs if d is not None]
    # Filter duplicates/subdirs
    dirs = [x for x in dirs if not any(x.startswith(y+'/') for y in dirs)]

    run_in_docker(do_inner, [args], docker_args=[
        "--privileged",  # needs to (1) have access to loop devices, (2) be able to mount things
        "--volume=/dev:/dev",  # https://github.com/moby/moby/issues/27886
        "--volume=/sys/fs/cgroup:/sys/fs/cgroup:ro",
        *["--volume={path}:{path}".format(path=x) for x in dirs]
    ])
