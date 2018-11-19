# SPDX-License-Identifier: LGPL-2.1+

import sys
from typing import List, Optional

from ..types import RAW_FORMATS, CommandLineArguments, OutputFormat
from ..ui import format_bytes

NEEDS_ROOT = False
NEEDS_BUILD = False
HAS_ARGS = False
FORCE_UNLINKS = False

def yes_no(b: bool) -> str:
    return "yes" if b else "no"

def format_bytes_or_disabled(sz: Optional[int]) -> str:
    if sz is None:
        return "(disabled)"

    return format_bytes(sz)

def format_bytes_or_auto(sz: Optional[int])-> str:
    if sz is None:
        return "(automatic)"

    return format_bytes(sz)

def none_to_na(s: Optional[str]) -> str:
    return "n/a" if s is None else s

def none_to_no(s: Optional[str]) -> str:
    return "no" if s is None else s

def none_to_none(s: Optional[str]) -> str:
    return "none" if s is None else s

def line_join_list(l: List[str]) -> str:

    if not l:
        return "none"

    return "\n                        ".join(l)

def do(args: CommandLineArguments) -> None:
    sys.stderr.write("DISTRIBUTION:\n")
    sys.stderr.write("          Distribution: " + args.distribution + "\n")
    sys.stderr.write("               Release: " + none_to_na(args.release) + "\n")
    if args.mirror is not None:
        sys.stderr.write("                Mirror: " + args.mirror + "\n")
    sys.stderr.write("\nOUTPUT:\n")
    if args.hostname:
        sys.stderr.write("              Hostname: " + args.hostname + "\n")
    sys.stderr.write("         Output Format: " + args.output_format.name + "\n")
    if args.output_dir:
        sys.stderr.write("      Output Directory: " + args.output_dir + "\n")
    sys.stderr.write("                Output: " + args.output + "\n")
    sys.stderr.write("       Output Checksum: " + none_to_na(args.output_checksum if args.checksum else None) + "\n")
    sys.stderr.write("      Output Signature: " + none_to_na(args.output_signature if args.sign else None) + "\n")
    sys.stderr.write("           Output Bmap: " + none_to_na(args.output_bmap if args.bmap else None) + "\n")
    sys.stderr.write("Output nspawn Settings: " + none_to_na(args.output_nspawn_settings if args.nspawn_settings is not None else None) + "\n")
    sys.stderr.write("           Incremental: " + yes_no(args.incremental) + "\n")

    if args.output_format in (*RAW_FORMATS, OutputFormat.subvolume):
        sys.stderr.write("             Read-only: " + yes_no(args.read_only) + "\n")
    if args.output_format in (*RAW_FORMATS, OutputFormat.subvolume):
        sys.stderr.write("        FS Compression: " + yes_no(args.compress) + "\n")

    if args.output_format in RAW_FORMATS + (OutputFormat.tar,):
        sys.stderr.write("        XZ Compression: " + yes_no(args.xz) + "\n")

    if args.output_format in RAW_FORMATS:
        sys.stderr.write("                 QCow2: " + yes_no(args.qcow2) + "\n")

    sys.stderr.write("            Encryption: " + none_to_no(args.encrypt) + "\n")
    sys.stderr.write("                Verity: " + yes_no(args.verity) + "\n")

    if args.output_format in RAW_FORMATS:
        sys.stderr.write("              Bootable: " + yes_no(args.bootable) + "\n")

        if args.bootable:
            sys.stderr.write("   Kernel Command Line: " + args.kernel_commandline + "\n")
            sys.stderr.write("       UEFI SecureBoot: " + yes_no(args.secure_boot) + "\n")

            if args.secure_boot:
                sys.stderr.write("   UEFI SecureBoot Key: " + args.secure_boot_key + "\n")
                sys.stderr.write(" UEFI SecureBoot Cert.: " + args.secure_boot_certificate + "\n")

    sys.stderr.write("\nPACKAGES:\n")
    sys.stderr.write("              Packages: " + line_join_list(args.packages) + "\n")
    sys.stderr.write("    With Documentation: " + yes_no(args.with_docs) + "\n")

    sys.stderr.write("         Package Cache: " + none_to_none(args.cache_path) + "\n")
    sys.stderr.write("           Extra Trees: " + line_join_list(args.extra_trees) + "\n")
    sys.stderr.write("        Skeleton Trees: " + line_join_list(args.skeleton_trees) + "\n")
    sys.stderr.write("          Build Script: " + none_to_none(args.build_script) + "\n")

    if args.build_script:
        sys.stderr.write("             Run tests: " + yes_no(args.with_tests) + "\n")

    sys.stderr.write("         Build Sources: " + none_to_none(args.build_sources) + "\n")
    sys.stderr.write("       Build Directory: " + none_to_none(args.build_dir) + "\n")
    sys.stderr.write("        Build Packages: " + line_join_list(args.build_packages) + "\n")
    sys.stderr.write("    Postinstall Script: " + none_to_none(args.postinst_script) + "\n")
    sys.stderr.write("  Scripts with network: " + yes_no(args.with_network) + "\n")
    sys.stderr.write("       nspawn Settings: " + none_to_none(args.nspawn_settings) + "\n")

    if args.output_format in RAW_FORMATS:
        sys.stderr.write("\nPARTITIONS:\n")
        sys.stderr.write("        Root Partition: " + format_bytes_or_auto(args.root_size) + "\n")
        sys.stderr.write("        Swap Partition: " + format_bytes_or_disabled(args.swap_size) + "\n")
        sys.stderr.write("                   ESP: " + format_bytes_or_disabled(args.esp_size) + "\n")
        sys.stderr.write("       /home Partition: " + format_bytes_or_disabled(args.home_size) + "\n")
        sys.stderr.write("        /srv Partition: " + format_bytes_or_disabled(args.srv_size) + "\n")

    if args.output_format in RAW_FORMATS:
        sys.stderr.write("\nVALIDATION:\n")
        sys.stderr.write("              Checksum: " + yes_no(args.checksum) + "\n")
        sys.stderr.write("                  Sign: " + yes_no(args.sign) + "\n")
        sys.stderr.write("               GPG Key: " + ("default" if args.key is None else args.key) + "\n")
        sys.stderr.write("              Password: " + ("default" if args.password is None else "set") + "\n")

    sys.stderr.write("\nHOST CONFIGURATION:\n")
    sys.stderr.write("    Extra search paths: " + line_join_list(args.extra_search_paths) + "\n")
