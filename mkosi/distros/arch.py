# SPDX-License-Identifier: LGPL-2.1+

import glob
import os
import os.path
import platform
import sys
from subprocess import PIPE, CompletedProcess, run
from typing import List, Optional, Set

from ..cli import CommandLineArguments
from ..types import OutputFormat
from ..ui import complete_step, warn
from ..utils import patch_file, run_workspace_command

PKG_CACHE = ['var/cache/pacman/pkg']
DEFAULT_RELEASE = None
DEFAULT_MIRROR = "https://mirrors.kernel.org/archlinux"
if platform.machine() == "aarch64":
    DEFAILT_MIRROR = "http://mirror.archlinuxarm.org"

def enable_networkd(workspace: str) -> None:
    run(["systemctl",
         "--root", os.path.join(workspace, "root"),
         "enable", "systemd-networkd", "systemd-resolved"],
        check=True)

    os.remove(os.path.join(workspace, "root", "etc/resolv.conf"))
    os.symlink("../run/systemd/resolve/stub-resolv.conf", os.path.join(workspace, "root", "etc/resolv.conf"))

    with open(os.path.join(workspace, "root", "etc/systemd/network/all-ethernet.network"), "w") as f:
        f.write("""\
[Match]
Type=ether

[Network]
DHCP=yes
""")

def enable_networkmanager(workspace: str) -> None:
    run(["systemctl",
         "--root", os.path.join(workspace, "root"),
         "enable", "NetworkManager"],
        check=True)

@complete_step('Installing Arch Linux')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    if args.release is not None:
        sys.stderr.write("Distribution release specification is not supported for Arch Linux, ignoring.\n")

    if platform.machine() == "aarch64":
        server = "Server = {}/$arch/$repo".format(args.mirror)
    else:
        server = "Server = {}/$repo/os/$arch".format(args.mirror)

    root = os.path.join(workspace, "root")
    # Create base layout for pacman and pacman-key
    os.makedirs(os.path.join(root, "var/lib/pacman"), 0o755, exist_ok=True)
    os.makedirs(os.path.join(root, "etc/pacman.d/gnupg"), 0o755, exist_ok=True)

    pacman_conf = os.path.join(workspace, "pacman.conf")
    with open(pacman_conf, "w") as f:
        f.write("""\
[options]
RootDir     = {root}
LogFile     = /dev/null
CacheDir    = {root}/var/cache/pacman/pkg/
GPGDir      = {root}/etc/pacman.d/gnupg/
HookDir     = {root}/etc/pacman.d/hooks/
HoldPkg     = pacman glibc
Architecture = auto
UseSyslog
Color
CheckSpace
SigLevel    = Required DatabaseOptional

[core]
{server}

[extra]
{server}

[community]
{server}
""".format(server=server, root=root))

    def run_pacman(args: List[str], **kwargs) -> CompletedProcess:
        cmdline = [
            "pacman",
            "--noconfirm",
            "--color", "never",
            "--config", pacman_conf,
        ]
        return run(cmdline + args, **kwargs, check=True)

    def run_pacman_key(args: List[str]) -> CompletedProcess:
        cmdline = [
            "pacman-key",
            "--nocolor",
            "--config", pacman_conf,
        ]
        return run(cmdline + args, check=True)

    def run_pacstrap(packages: Set[str]) -> None:
        cmdline = ["pacstrap", "-C", pacman_conf, "-dGM", root]
        run(cmdline + list(packages), check=True)

    keyring = "archlinux"
    if platform.machine() == "aarch64":
        keyring += "arm"
    run_pacman_key(["--init"])
    run_pacman_key(["--populate", keyring])

    run_pacman(["-Sy"])
    # determine base packages list from base group
    c = run_pacman(["-Sqg", "base"], stdout=PIPE, universal_newlines=True)
    packages = set(c.stdout.split())
    packages -= {
        "cryptsetup",
        "device-mapper",
        "dhcpcd",
        "e2fsprogs",
        "jfsutils",
        "linux",
        "lvm2",
        "man-db",
        "man-pages",
        "mdadm",
        "netctl",
        "reiserfsprogs",
        "xfsprogs",
    }

    official_kernel_packages = {
        "linux",
        "linux-lts",
        "linux-hardened",
        "linux-zen",
    }
    kernel_packages = official_kernel_packages.intersection(args.packages)
    packages |= kernel_packages
    if len(kernel_packages) > 1:
        warn('More than one kernel will be installed: {}', ' '.join(kernel_packages))

    if args.bootable:
        if args.output_format == OutputFormat.raw_ext4:
            packages.add("e2fsprogs")
        elif args.output_format == OutputFormat.raw_btrfs:
            packages.add("btrfs-progs")
        elif args.output_format == OutputFormat.raw_xfs:
            packages.add("xfsprogs")
        if args.encrypt:
            packages.add("cryptsetup")
            packages.add("device-mapper")
        if not kernel_packages:
            # No user-specified kernel
            packages.add("linux")

    # Set up system with packages from the base group
    run_pacstrap(packages)

    # Install the user-specified packages
    packages = set(args.packages)
    if run_build_script:
        packages.update(args.build_packages)
    # Remove already installed packages
    c = run_pacman(['-Qq'], stdout=PIPE, universal_newlines=True)
    packages.difference_update(c.stdout.split())
    if packages:
        run_pacstrap(packages)

    # Kill the gpg-agent used by pacman and pacman-key
    run(['gpg-connect-agent', '--homedir', os.path.join(root, 'etc/pacman.d/gnupg'), 'KILLAGENT', '/bye'])

    if "networkmanager" in args.packages:
        enable_networkmanager(workspace)
    else:
        enable_networkd(workspace)

    with open(os.path.join(workspace, 'root', 'etc/locale.gen'), 'w') as f:
        f.write('en_US.UTF-8 UTF-8\n')

    run_workspace_command(args, workspace, '/usr/bin/locale-gen')

    with open(os.path.join(workspace, 'root', 'etc/locale.conf'), 'w') as f:
        f.write('LANG=en_US.UTF-8\n')

    # At this point, no process should be left running, kill then
    run(["fuser", "-c", root, "--kill"])

def find_kernel_file(workspace_root: str, pattern: str) -> Optional[str]:
    # Look for the vmlinuz file in the workspace
    workspace_pattern = os.path.join(workspace_root, pattern.lstrip('/'))
    kernel_files = sorted(glob.glob(workspace_pattern))
    kernel_file = kernel_files[0]
    # The path the kernel-install script expects is within the workspace reference as it is run from within the container
    if kernel_file.startswith(workspace_root):
        kernel_file = kernel_file[len(workspace_root):]
    else:
        sys.stderr.write('Error, kernel file %s cannot be used as it is not in the workspace\n' % kernel_file)
        return None
    if len(kernel_files) > 1:
        warn('More than one kernel file found, will use {}', kernel_file)
    return kernel_file

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    def patch(line: str) -> str:
        if line.startswith("HOOKS=") and args.encrypt == "all":
            return"HOOKS=\"systemd modconf block sd-encrypt filesystems keyboard fsck\"\n"
        elif line.startswith("HOOKS="):
            return"HOOKS=\"systemd modconf block filesystems fsck\"\n"
        else:
            return line

    patch_file(os.path.join(workspace, "root", "etc/mkinitcpio.conf"), patch)

    workspace_root = os.path.join(workspace, "root")
    kernel_version = next(filter(lambda x: x[0].isdigit(), os.listdir(os.path.join(workspace_root, "lib/modules"))))
    kernel_file = find_kernel_file(workspace_root, "/boot/vmlinuz-*")
    if kernel_file is not None:
        run_workspace_command(args, workspace, "/usr/bin/kernel-install", "add", kernel_version, kernel_file)
