# SPDX-License-Identifier: LGPL-2.1+

import os
import shutil
from typing import List, Optional

from ..gpt import ensured_partition
from ..types import CommandLineArguments
from ..ui import complete_step, run_visible
from ..utils import run_workspace_command

PKG_CACHE: List[str] = []
DEFAULT_RELEASE = 'latest'
DEFAULT_MIRROR = None

@complete_step('Installing Clear Linux')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    if args.release == "latest":
        release = "clear"
    else:
        release = "clear/"+args.release

    root = os.path.join(workspace, "root")

    packages = ['os-core'] + args.packages
    if run_build_script:
        packages.extend(args.build_packages)
    if args.bootable:
        packages += ['kernel-native']

    swupd_extract = shutil.which("swupd-extract")

    if swupd_extract is None:
        print("""
Couldn't find swupd-extract program, download (or update it) it using:

  go get -u github.com/clearlinux/mixer-tools/swupd-extract

and it will be installed by default in ~/go/bin/swupd-extract. Also
ensure that you have openssl program in your system.
""")
        raise FileNotFoundError("Couldn't find swupd-extract")

    print("Using {}".format(swupd_extract))

    run_visible([swupd_extract,
                 '-output', root,
                 '-state', args.cache_path,
                 release,
                 *packages],
                check=True)

    os.symlink("../run/systemd/resolve/resolv.conf", os.path.join(root, "etc/resolv.conf"))

    # Clear Linux doesn't have a /etc/shadow at install time, it gets
    # created when the root first login. To set the password via
    # mkosi, create one.
    if not run_build_script and args.password is not None:
        shadow_file = os.path.join(root, "etc/shadow")
        with open(shadow_file, "w") as f:
            f.write('root::::::::')
        os.chmod(shadow_file, 0o400)
        # Password is already empty for root, so no need to reset it later.
        if args.password == "":
            args.password = None

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    nspawn_params = [
        # clr-boot-manager uses blkid in the device backing "/" to
        # figure out uuid and related parameters.
        "--bind-ro=/dev",

        # clr-boot-manager compiled in Clear Linux will assume EFI
        # partition is mounted in "/boot".
        "--bind=" + os.path.join(workspace, "root/efi") + ":/boot",
    ]
    if loopdev is not None:
        nspawn_params += ["--property=DeviceAllow=" + loopdev]
        if args.esp_partno is not None:
            nspawn_params += ["--property=DeviceAllow=" + ensured_partition(loopdev, args.esp_partno)]
        if args.root_partno is not None:
            nspawn_params += ["--property=DeviceAllow=" + ensured_partition(loopdev, args.root_partno)]

    run_workspace_command(args, workspace, "/usr/bin/clr-boot-manager", "update", "-i", nspawn_params=nspawn_params)
