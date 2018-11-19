# SPDX-License-Identifier: LGPL-2.1+

import os
import platform
import shlex
import shutil
import sys
from typing import Iterable, List

from ..types import CommandLineArguments
from ..ui import die

NEEDS_ROOT = False
NEEDS_BUILD = True
HAS_ARGS = True
FORCE_UNLINKS = True

def print_running_cmd(cmdline: Iterable[str]) -> None:
    sys.stderr.write("‣ \033[0;1;39mRunning command:\033[0m\n")
    sys.stderr.write(" ".join(shlex.quote(x) for x in cmdline) + "\n")

def do(args: CommandLineArguments) -> None:

    # Look for the right qemu command line to use
    cmdlines: List[List[str]] = []
    ARCH_BINARIES = { 'x86_64' : 'qemu-system-x86_64',
                      'i386'   : 'qemu-system-i386'}
    arch_binary = ARCH_BINARIES.get(platform.machine(), None)
    if arch_binary is not None:
        cmdlines += [[arch_binary, '-machine', 'accel=kvm']]
    cmdlines += [
        ['qemu', '-machine', 'accel=kvm'],
        ['qemu-kvm'],
    ]
    for cmdline in cmdlines:
        if shutil.which(cmdline[0]) is not None:
            break
    else:
        die("Couldn't find QEMU/KVM binary")

    # UEFI firmware blobs are found in a variety of locations,
    # depending on distribution and package.
    FIRMWARE_LOCATIONS = []
    # First, we look in paths that contain the architecture –
    # if they exist, they’re almost certainly correct.
    if platform.machine() == 'x86_64':
        FIRMWARE_LOCATIONS.append('/usr/share/ovmf/ovmf_code_x64.bin')
        FIRMWARE_LOCATIONS.append('/usr/share/ovmf/x64/OVMF_CODE.fd')
    elif platform.machine() == 'i386':
        FIRMWARE_LOCATIONS.append('/usr/share/ovmf/ovmf_code_ia32.bin')
        FIRMWARE_LOCATIONS.append('/usr/share/edk2/ovmf-ia32/OVMF_CODE.fd')
    # After that, we try some generic paths and hope that if they exist,
    # they’ll correspond to the current architecture, thanks to the package manager.
    FIRMWARE_LOCATIONS.append('/usr/share/edk2/ovmf/OVMF_CODE.fd')
    FIRMWARE_LOCATIONS.append('/usr/share/qemu/OVMF_CODE.fd')

    for firmware in FIRMWARE_LOCATIONS:
        if os.path.exists(firmware):
            break
    else:
        die("Couldn't find OVMF UEFI firmware blob.")

    cmdline += [ "-smp", "2",
                 "-m", "1024",
                 "-drive", "if=pflash,format=raw,readonly,file=" + firmware,
                 "-drive", "format=" + ("qcow2" if args.qcow2 else "raw") + ",file=" + args.output,
                 *args.cmdline ]

    print_running_cmd(cmdline)

    os.execvp(cmdline[0], cmdline)
