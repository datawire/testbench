# SPDX-License-Identifier: LGPL-2.1+

import os
import os.path
import shutil
import urllib.request
import uuid
from subprocess import DEVNULL, run
from typing import BinaryIO, Callable, Dict, List, Optional

from .cli import CommandLineArguments
from .ui import complete_step


def mount_bind(what: str, where: str) -> None:
    os.makedirs(what, 0o755, True)
    os.makedirs(where, 0o755, True)
    run(["mount", "--bind", what, where], check=True)

def umount(where: str) -> None:
    # Ignore failures and error messages
    run(["umount", "--recursive", "-n", where], stdout=DEVNULL, stderr=DEVNULL)

def patch_file(filepath: str, line_rewriter: Callable[[str], str]) -> None:
    temp_new_filepath = filepath + ".tmp.new"

    with open(filepath, "r") as old:
        with open(temp_new_filepath, "w") as new:
            for line in old:
                new.write(line_rewriter(line))

    shutil.copystat(filepath, temp_new_filepath)
    os.remove(filepath)
    shutil.move(temp_new_filepath, filepath)

def run_workspace_command(args: CommandLineArguments, workspace: str, *cmd: str, network: bool=False, env: Dict[str, str]={}, nspawn_params: List[str]=[]) -> None:

    cmdline = ["systemd-nspawn",
               '--quiet',
               "--directory=" + os.path.join(workspace, "root"),
               "--uuid=" + args.machine_id,
               "--machine=mkosi-" + uuid.uuid4().hex,
               "--as-pid2",
               "--register=no",
               "--bind=" + var_tmp(workspace) + ":/var/tmp",
               "--setenv=SYSTEMD_OFFLINE=1" ]

    if network:
        # If we're using the host network namespace, use the same resolver
        cmdline += ["--bind-ro=/etc/resolv.conf"]
    else:
        cmdline += ["--private-network"]

    cmdline += [ "--setenv={}={}".format(k, v) for k, v in env.items() ]

    if nspawn_params:
        cmdline += nspawn_params

    cmdline += ['--', *cmd]
    run(cmdline, check=True)

def check_if_url_exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    try:
        if urllib.request.urlopen(req):
            return True
        return False
    except:
        return False

def mkdir_last(path: str, mode: int=0o777) -> str:
    """Create directory path

    Only the final component will be created, so this is different than mkdirs().
    """
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        if not os.path.isdir(path):
            raise
    return path

def var_tmp(workspace: str) -> str:
    return mkdir_last(os.path.join(workspace, "var-tmp"))

def run_build_script(args: CommandLineArguments, workspace: str, raw: Optional[BinaryIO]) -> None:
    if args.build_script is None:
        return

    with complete_step('Running build script'):
        dest = os.path.join(workspace, "dest")
        os.mkdir(dest, 0o755)

        target = "--directory=" + os.path.join(workspace, "root") if raw is None else "--image=" + raw.name

        cmdline = ["systemd-nspawn",
                   '--quiet',
                   target,
                   "--uuid=" + args.machine_id,
                   "--machine=mkosi-" + uuid.uuid4().hex,
                   "--as-pid2",
                   "--register=no",
                   "--bind", dest + ":/root/dest",
                   "--bind=" + var_tmp(workspace) + ":/var/tmp",
                   "--setenv=WITH_DOCS=" + ("1" if args.with_docs else "0"),
                   "--setenv=WITH_TESTS=" + ("1" if args.with_tests else "0"),
                   "--setenv=DESTDIR=/root/dest"]

        if args.build_sources is not None:
            cmdline.append("--setenv=SRCDIR=/root/src")
            cmdline.append("--chdir=/root/src")

            if args.read_only:
                cmdline.append("--overlay=+/root/src::/root/src")
        else:
            cmdline.append("--chdir=/root")

        if args.build_dir is not None:
            cmdline.append("--setenv=BUILDDIR=/root/build")
            cmdline.append("--bind=" + args.build_dir + ":/root/build")

        if args.with_network:
            # If we're using the host network namespace, use the same resolver
            cmdline.append("--bind-ro=/etc/resolv.conf")
        else:
            cmdline.append("--private-network")

        cmdline.append("/root/" + os.path.basename(args.build_script))
        run(cmdline, check=True)
