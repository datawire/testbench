# SPDX-License-Identifier: LGPL-2.1+

import os
from subprocess import run
from typing import List, Optional

from ..types import CommandLineArguments, OutputFormat
from ..ui import complete_step
from ..utils import run_workspace_command

PKG_CACHE = ['var/cache/apt/archives']
DEFAULT_RELEASE = 'unstable'
DEFAULT_MIRROR = "http://deb.debian.org/debian"

def debootstrap(args: CommandLineArguments, workspace: str, run_build_script: bool, mirror: str, repos: List[str], packages: List[str]) -> None:
    cmdline = ["debootstrap",
               "--verbose",
               "--merged-usr",
               "--variant=minbase",
               "--include=systemd-sysv",
               "--exclude=sysv-rc,initscripts,startpar,lsb-base,insserv",
               "--components=" + ','.join(repos),
               args.release,
               workspace + "/root",
               mirror]
    if args.bootable and args.output_format == OutputFormat.raw_btrfs:
        cmdline[4] += ",btrfs-tools"

    run(cmdline, check=True)

    # Work around debian bug #835628
    os.makedirs(os.path.join(workspace, "root/etc/dracut.conf.d"), exist_ok=True)
    with open(os.path.join(workspace, "root/etc/dracut.conf.d/99-generic.conf"), "w") as f:
        f.write("hostonly=no")

    # Debian policy is to start daemons by default.
    # The policy-rc.d script can be used choose which ones to start
    # Let's install one that denies all daemon startups
    # See https://people.debian.org/~hmh/invokerc.d-policyrc.d-specification.txt
    # Note: despite writing in /usr/sbin, this file is not shipped by the OS
    # and instead should be managed by the admin.
    policyrcd = os.path.join(workspace, "root/usr/sbin/policy-rc.d")
    with open(policyrcd, "w") as f:
        f.write("#!/bin/sh\n")
        f.write("exit 101")
    os.chmod(policyrcd, 0o755)
    dracut_bug_comment = [
        '# Work around "Failed to find module \'crc32c\'" dracut issue\n',
        '# See also:\n',
        '# - https://github.com/antonio-petricca/buddy-linux/issues/2#issuecomment-404505527\n',
        '# - https://bugs.launchpad.net/ubuntu/+source/dracut/+bug/1781143\n',
    ]
    dracut_bug_conf = os.path.join(workspace, "root/etc/dpkg/dpkg.cfg.d/01_no_dracut_10-debian")
    with open(dracut_bug_conf, "w") as f:
        f.writelines(dracut_bug_comment + ['path-exclude /etc/dracut.conf.d/10-debian.conf\n'])

    doc_paths = [
        '/usr/share/locale',
        '/usr/share/doc',
        '/usr/share/man',
        '/usr/share/groff',
        '/usr/share/info',
        '/usr/share/lintian',
        '/usr/share/linda',
    ]
    if not args.with_docs:
        # Remove documentation installed by debootstrap
        cmdline = ["/bin/rm", "-rf"] + doc_paths
        run_workspace_command(args, workspace, *cmdline)
        # Create dpkg.cfg to ignore documentation on new packages
        dpkg_conf = os.path.join(workspace, "root/etc/dpkg/dpkg.cfg.d/01_nodoc")
        with open(dpkg_conf, "w") as f:
            f.writelines(["path-exclude %s/*\n" % d for d in doc_paths])

    cmdline = ["/usr/bin/apt-get", "--assume-yes", "--no-install-recommends", "install"] + packages
    run_workspace_command(args, workspace, network=True, env={'DEBIAN_FRONTEND': 'noninteractive', 'DEBCONF_NONINTERACTIVE_SEEN': 'true'}, *cmdline)
    os.unlink(policyrcd)

@complete_step('Installing Debian')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    repos = args.repositories if args.repositories else ["main"]
    # Debootstrap is not smart enough to deal correctly with alternative dependencies
    # Installing libpam-systemd via debootstrap results in systemd-shim being installed
    # Therefore, prefer to install via apt from inside the container
    packages = [ 'dbus', 'libpam-systemd']
    packages.extend(args.packages)
    if run_build_script:
        packages.extend(args.build_packages)
    if args.bootable:
        packages += ["dracut", "linux-image-amd64"]
    debootstrap(args, workspace, run_build_script, args.mirror, repos, packages)

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    kernel_version = next(filter(lambda x: x[0].isdigit(), os.listdir(os.path.join(workspace, "root", "lib/modules"))))

    run_workspace_command(args, workspace,
                          "/usr/bin/kernel-install", "add", kernel_version, "/boot/vmlinuz-" + kernel_version)
