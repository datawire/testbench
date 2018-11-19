# SPDX-License-Identifier: LGPL-2.1+

import os

from ..types import CommandLineArguments, OutputFormat

NEEDS_ROOT = True
NEEDS_BUILD = True
HAS_ARGS = True
FORCE_UNLINKS = True

def do(args: CommandLineArguments) -> None:
    target = "--directory=" + args.output if args.output_format in (OutputFormat.directory, OutputFormat.subvolume) else "--image=" + args.output

    cmdline = ["systemd-nspawn",
               target]

    if args.verb == "boot":
        cmdline += ('--boot',)

    if args.cmdline:
        cmdline += ('--', *args.cmdline)

    os.execvp(cmdline[0], cmdline)
