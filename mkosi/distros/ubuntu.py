# SPDX-License-Identifier: LGPL-2.1+

import platform
from typing import Optional

from ..cli import CommandLineArguments
from ..ui import complete_step
from .debian import debootstrap
from .debian import install_boot_loader as install_boot_loader_debian

DEFAULT_RELEASE = 'artful'
DEFAULT_MIRROR = "http://archive.ubuntu.com/ubuntu"
if platform.machine() == "aarch64":
    DEFAULT_MIRROR = "http://ports.ubuntu.com/"

PKG_CACHE = ['var/cache/apt/archives']

@complete_step('Installing Ubuntu')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    repos = args.repositories if args.repositories else ["main"]
    if args.bootable and 'universe' not in repos:
        repos.append('universe')
    # Debootstrap is not smart enough to deal correctly with alternative dependencies
    # Installing libpam-systemd via debootstrap results in systemd-shim being installed
    # Therefore, prefer to install via apt from inside the container
    packages = [ 'dbus', 'libpam-systemd']
    packages.extend(args.packages)
    if run_build_script:
        packages.extend(args.build_packages)
    if args.bootable:
        packages += ["dracut", "linux-generic"]
    debootstrap(args, workspace, run_build_script, args.mirror, repos, packages)

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    install_boot_loader_debian(args, workspace, loopdev)
