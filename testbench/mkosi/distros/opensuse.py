# SPDX-License-Identifier: LGPL-2.1+

import os
from typing import List, Optional

from ..types import CommandLineArguments, OutputFormat
from ..ui import complete_step, run_visible
from .debian import install_boot_loader as install_boot_loader_debian

PKG_CACHE = ['var/cache/zypp/packages']
DEFAULT_RELEASE = 'tumbleweed'
DEFAULT_MIRROR = "http://download.opensuse.org"

@complete_step('Installing openSUSE')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:

    root = os.path.join(workspace, "root")
    release = args.release.strip('"')

    #
    # If the release looks like a timestamp, it's Tumbleweed.
    # 13.x is legacy (14.x won't ever appear). For anything else,
    # let's default to Leap.
    #
    if release.isdigit() or release == "tumbleweed":
        release_url = "{}/tumbleweed/repo/oss/".format(args.mirror)
        updates_url = "{}/update/tumbleweed/".format(args.mirror)
    elif release.startswith("13."):
        release_url = "{}/distribution/{}/repo/oss/".format(args.mirror, release)
        updates_url = "{}/update/{}/".format(args.mirror, release)
    else:
        release_url = "{}/distribution/leap/{}/repo/oss/".format(args.mirror, release)
        updates_url = "{}/update/leap/{}/oss/".format(args.mirror, release)

    #
    # Configure the repositories: we need to enable packages caching
    # here to make sure that the package cache stays populated after
    # "zypper install".
    #
    run_visible(["zypper", "--root", root, "addrepo", "-ck", release_url, "Main"], check=True)
    run_visible(["zypper", "--root", root, "addrepo", "-ck", updates_url, "Updates"], check=True)

    if not args.with_docs:
        with open(os.path.join(root, "etc/zypp/zypp.conf"), "w") as f:
            f.write("rpm.install.excludedocs = yes\n")

    # The common part of the install comand.
    cmdline = ["zypper", "--root", root, "--gpg-auto-import-keys",
               "install", "-y", "--no-recommends"]
    #
    # Install the "minimal" package set.
    #
    run_visible(cmdline + ["patterns-base-minimal_base"], check=True)

    #
    # Now install the additional packages if necessary.
    #
    extra_packages: List[str] = []

    if args.bootable:
        extra_packages += ["kernel-default"]

    if args.encrypt:
        extra_packages += ["device-mapper"]

    if args.output_format in (OutputFormat.subvolume, OutputFormat.raw_btrfs):
        extra_packages += ["btrfsprogs"]

    extra_packages.extend(args.packages)

    if run_build_script:
        extra_packages.extend(args.build_packages)

    if extra_packages:
        run_visible(cmdline + extra_packages, check=True)

    #
    # Disable packages caching in the image that was enabled
    # previously to populate the package cache.
    #
    run_visible(["zypper", "--root", root, "modifyrepo", "-K", "Main"], check=True)
    run_visible(["zypper", "--root", root, "modifyrepo", "-K", "Updates"], check=True)

    #
    # Tune dracut confs: openSUSE uses an old version of dracut that's
    # probably explain why we need to do those hacks.
    #
    if args.bootable:
        os.makedirs(os.path.join(root, "etc/dracut.conf.d"), exist_ok=True)

        with open(os.path.join(root, "etc/dracut.conf.d/99-mkosi.conf"), "w") as f:
            f.write("hostonly=no\n")

        # dracut from openSUSE is missing upstream commit 016613c774baf.
        with open(os.path.join(root, "etc/kernel/cmdline"), "w") as cmdlinefile:
            cmdlinefile.write(args.kernel_commandline + " root=/dev/gpt-auto-root\n")

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    install_boot_loader_debian(args, workspace, loopdev)
