# SPDX-License-Identifier: LGPL-2.1+

import contextlib
import os
import os.path
import shutil
from subprocess import run
from typing import Iterator, List

from .cli import CommandLineArguments
from .types import OutputFormat
from .ui import complete_step
from .utils import mkdir_last, mount_bind, umount


@contextlib.contextmanager
def mount_api_vfs(args: CommandLineArguments, workspace: str) -> Iterator[None]:
    paths = ('/proc', '/dev', '/sys')
    root = os.path.join(workspace, "root")

    with complete_step('Mounting API VFS'):
        for d in paths:
            mount_bind(d, root + d)
    try:
        yield
    finally:
        with complete_step('Unmounting API VFS'):
            for d in paths:
                umount(root + d)

def invoke_dnf(args: CommandLineArguments, workspace: str, repositories: List[str], base_packages: List[str], boot_packages: List[str], config_file: str, run_build_script: bool=True) -> None:

    repos = ["--enablerepo=" + repo for repo in repositories]

    root = os.path.join(workspace, "root")
    cmdline = ["dnf",
               "-y",
               "--config=" + config_file,
               "--best",
               "--allowerasing",
               "--releasever=" + args.release,
               "--installroot=" + root,
               "--disablerepo=*",
               *repos,
               "--setopt=keepcache=1",
               "--setopt=install_weak_deps=0"]

    # Turn off docs, but not during the development build, as dnf currently has problems with that
    if not args.with_docs and not run_build_script:
        cmdline.append("--setopt=tsflags=nodocs")

    cmdline.extend([
        "install",
        *base_packages
    ])

    cmdline.extend(args.packages)

    if run_build_script:
        cmdline.extend(args.build_packages)

    if args.bootable:
        cmdline.extend(boot_packages)

        # Temporary hack: dracut only adds crypto support to the initrd, if the cryptsetup binary is installed
        if args.encrypt or args.verity:
            cmdline.append("cryptsetup")

        if args.output_format == OutputFormat.raw_ext4:
            cmdline.append("e2fsprogs")

        if args.output_format == OutputFormat.raw_xfs:
            cmdline.append("xfsprogs")

        if args.output_format == OutputFormat.raw_btrfs:
            cmdline.append("btrfs-progs")

    with mount_api_vfs(args, workspace):
        run(cmdline, check=True)

def invoke_yum(args: CommandLineArguments, workspace: str, repositories: List[str], base_packages: List[str], boot_packages: List[str], config_file: str, run_build_script: bool=True) -> None:

    repos = ["--enablerepo=" + repo for repo in repositories]

    root = os.path.join(workspace, "root")
    cmdline = ["yum",
               "-y",
               "--config=" + config_file,
               "--releasever=" + args.release,
               "--installroot=" + root,
               "--disablerepo=*",
               *repos,
               "--setopt=keepcache=1"]

    # Turn off docs, but not during the development build, as dnf currently has problems with that
    if not args.with_docs and not run_build_script:
        cmdline.append("--setopt=tsflags=nodocs")

    cmdline.extend([
        "install",
        *base_packages
    ])

    cmdline.extend(args.packages)

    if run_build_script:
        cmdline.extend(args.build_packages)

    if args.bootable:
        cmdline.extend(boot_packages)

        # Temporary hack: dracut only adds crypto support to the initrd, if the cryptsetup binary is installed
        if args.encrypt or args.verity:
            cmdline.append("cryptsetup")

        if args.output_format == OutputFormat.raw_ext4:
            cmdline.append("e2fsprogs")

        if args.output_format == OutputFormat.raw_btrfs:
            cmdline.append("btrfs-progs")

    with mount_api_vfs(args, workspace):
        run(cmdline, check=True)

def invoke_dnf_or_yum(args: CommandLineArguments, workspace: str, repositories: List[str], base_packages: List[str], boot_packages: List[str], config_file: str) -> None:

    if shutil.which("dnf") is None:
        invoke_yum(args, workspace, repositories, base_packages, boot_packages, config_file)
    else:
        invoke_dnf(args, workspace, repositories, base_packages, boot_packages, config_file)

def disable_kernel_install(args: CommandLineArguments, workspace: str) -> List[str]:
    # Let's disable the automatic kernel installation done by the
    # kernel RPMs. After all, we want to built our own unified kernels
    # that include the root hash in the kernel command line and can be
    # signed as a single EFI executable. Since the root hash is only
    # known when the root file system is finalized we turn off any
    # kernel installation beforehand.

    if not args.bootable:
        return []

    for d in ("etc", "etc/kernel", "etc/kernel/install.d"):
        mkdir_last(os.path.join(workspace, "root", d), 0o755)

    masked: List[str] = []

    for f in ("50-dracut.install", "51-dracut-rescue.install", "90-loaderentry.install"):
        path = os.path.join(workspace, "root", "etc/kernel/install.d", f)
        os.symlink("/dev/null", path)
        masked += [path]

    return masked

def reenable_kernel_install(args: CommandLineArguments, workspace: str, masked: List[str]) -> None:
    # Undo disable_kernel_install() so the final image can be used
    # with scripts installing a kernel following the Bootloader Spec

    if not args.bootable:
        return

    for f in masked:
        os.unlink(f)
