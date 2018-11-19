# SPDX-License-Identifier: LGPL-2.1+

import os
import shlex
import shutil

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

# Kinda like Bash <<-'EOT' here-docs
def trim(s: str) -> str:
    return "\n".join([line.lstrip("\t") for line in s.lstrip("\n").split("\n")])


def write(fname: str, content: str, mode: int = 0o644) -> None:
    with open(fname, 'wt') as file:
        file.write(trim(content))
    os.chmod(fname, mode)


def setup(args: CommandLineArguments, workspace: str, mountpoint: str) -> None:
    run_workspace_command(args, os.path.dirname(mountpoint),
                          "useradd",
                          "--create-home",
                          "--comment", "testbench runner",
                          "--groups", "users",
                          "testbench")

    write(os.path.join(mountpoint, 'etc/sudoers.d/00-testbench'), """
        # SUDO_USERS HOSTS=(AS_USER) TAGS COMMANDS
        testbench ALL=(ALL) NOPASSWD: ALL
        """)

    write(os.path.join(mountpoint, 'etc/systemd/system/testbench-run.target'), """
        [Unit]
        Description=testbench-run target
        Requires=multi-user.target
        After=multi-user.target
        Conflicts=rescue.target
        AllowIsolate=yes
        """)
    os.symlink('testbench-run.target', os.path.join(mountpoint, 'etc/systemd/system/default.target'))

    write(os.path.join(mountpoint, 'etc/systemd/system/testbench-run.service'), """
        [Unit]
        Description=testbench-run service
        Wants=network-online.target
        After=network-online.target
        ConditionFileIsExecutable=/etc/testbench-run

        [Service]
        User=testbench
        WorkingDirectory=/home/testbench
        ExecStart=/etc/testbench-run
        StandardOutput=file:/var/log/testbench-run.tap
        ExecStopPost=+/bin/sh -c 'rm -f /etc/testbench-run; systemctl poweroff --no-block'

        [Install]
        WantedBy=testbench-run.target
        """)
    # systemctl enable tesbtench-run.service
    try:
        os.mkdir(os.path.join(mountpoint, 'etc/systemd/system/testbench-run.target.wants'), mode=0o755)
    except FileExistsError:
        pass
    os.symlink('../testbench-run.service', os.path.join(mountpoint, 'etc/systemd/system/testbench-run.target.wants/testbench-run.service'))

    write(os.path.join(mountpoint, 'etc/testbench-run'),
          "#!/bin/sh\n" + " ".join(shlex.quote(arg) for arg in args.cmdline)+"\n",
          mode=0o755)

    try:
        os.mkdir(os.path.join(mountpoint, 'home/testbench/.kube'), mode=0o755)
    except FileExistsError:
        pass
    shutil.copy(args.output[:-8]+".knaut",
                os.path.join(mountpoint, "home/testbench/.kube/config"))

    install_build_src(args, workspace, True, False)

def do_inner(args: CommandLineArguments) -> None:
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
