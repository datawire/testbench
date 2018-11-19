# PYTHON_ARGCOMPLETE_OK
# SPDX-License-Identifier: LGPL-2.1+

import argparse
import configparser
import getpass
import os
import re
import string
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union, cast

from . import distros, verbs
from .types import RAW_FORMATS, CommandLineArguments, OutputFormat
from .ui import die, warn

try:
    import argcomplete  # type: ignore # type hints for argcomplete don't exist yet
except ImportError:
    pass

__version__ = '4'

class ListAction(argparse.Action):
    delimiter: str

    def __call__(self,  # These typehints are copied from argparse.pyi
                 parser: argparse.ArgumentParser,
                 namespace: argparse.Namespace,
                 values: Union[str, Sequence[Any], None],
                 option_string: Optional[str]=None) -> None:
        assert isinstance(values, str)
        ary = getattr(namespace, self.dest)
        if ary is None:
            ary = []
        ary.extend(values.split(self.delimiter))
        setattr(namespace, self.dest, ary)

class CommaDelimitedListAction(ListAction):
    delimiter = ","

class ColonDelimitedListAction(ListAction):
    delimiter = ":"

def has_args_list() -> str:
    ary = [verb for verb in verbs.list_verbs() if verbs.get_verb(verb).HAS_ARGS]
    ary.sort()
    return ', '.join(["'{}'".format(verb) for verb in ary])

def parse_args() -> CommandLineArguments:
    parser = argparse.ArgumentParser(description='Build Legacy-Free OS Images', add_help=False)

    group = parser.add_argument_group("Commands")
    group.add_argument("verb", choices=verbs.list_verbs()+['help'], nargs='?', default="build", help='Operation to execute')
    group.add_argument("cmdline", nargs=argparse.REMAINDER, help="The command line to use for {}".format(has_args_list()))
    group.add_argument('-h', '--help', action='help', help="Show this help")
    group.add_argument('--version', action='version', version='%(prog)s ' + __version__)

    group = parser.add_argument_group("Distribution")
    group.add_argument('-d', "--distribution", choices=distros.list_distros(), help='Distribution to install')
    group.add_argument('-r', "--release", help='Distribution release to install')
    group.add_argument('-m', "--mirror", help='Distribution mirror to use')
    group.add_argument("--repositories", action=CommaDelimitedListAction, dest='repositories', help='Repositories to use', metavar='REPOS')

    group = parser.add_argument_group("Output")
    group.add_argument('-t', "--format", dest='output_format', choices=OutputFormat.__members__, help='Output Format')
    group.add_argument('-o', "--output", help='Output image path', metavar='PATH')
    group.add_argument('-O', "--output-dir", help='Output root directory', metavar='DIR')
    group.add_argument('-f', "--force", action='count', dest='force_count', default=0, help='Remove existing image file before operation')
    group.add_argument('-b', "--bootable", type=parse_boolean, nargs='?', const=True,
                       help='Make image bootable on EFI (only raw_ext4, raw_btrfs, raw_squashfs, raw_xfs)')
    group.add_argument("--secure-boot", action='store_true', help='Sign the resulting kernel/initrd image for UEFI SecureBoot')
    group.add_argument("--secure-boot-key", help="UEFI SecureBoot private key in PEM format", metavar='PATH')
    group.add_argument("--secure-boot-certificate", help="UEFI SecureBoot certificate in X509 format", metavar='PATH')
    group.add_argument("--read-only", action='store_true', help='Make root volume read-only (only raw_ext4, raw_btrfs, subvolume, implied on raw_squashs)')
    group.add_argument("--encrypt", choices=("all", "data"), help='Encrypt everything except: ESP ("all") or ESP and root ("data")')
    group.add_argument("--verity", action='store_true', help='Add integrity partition (implies --read-only)')
    group.add_argument("--compress", action='store_true', help='Enable compression in file system (only raw_btrfs, subvolume)')
    group.add_argument("--xz", action='store_true', help='Compress resulting image with xz (only raw_ext4, raw_btrfs, raw_squashfs, raw_xfs, implied on tar)')
    group.add_argument("--qcow2", action='store_true', help='Convert resulting image to qcow2 (only raw_ext4, raw_btrfs, raw_squashfs, raw_xfs)')
    group.add_argument('-i', "--incremental", action='store_true', help='Make use of and generate intermediary cache images')

    group = parser.add_argument_group("Packages")
    group.add_argument('-p', "--package", action=CommaDelimitedListAction, dest='packages', default=[], help='Add an additional package to the OS image', metavar='PACKAGE')
    group.add_argument("--with-docs", action='store_true', help='Install documentation (only Fedora, CentOS and Mageia)')
    group.add_argument('-T', "--without-tests", action='store_false', dest='with_tests', default=True, help='Do not run tests as part of build script, if supported')
    group.add_argument("--cache", dest='cache_path', help='Package cache path', metavar='PATH')
    group.add_argument("--extra-tree", action='append', dest='extra_trees', default=[], help='Copy an extra tree on top of image', metavar='PATH')
    group.add_argument("--skeleton-tree", action='append', dest='skeleton_trees', default=[], help='Use a skeleton tree to bootstrap the image before installing anything', metavar='PATH')
    group.add_argument("--build-script", help='Build script to run inside image', metavar='PATH')
    group.add_argument("--build-sources", help='Path for sources to build', metavar='PATH')
    group.add_argument("--build-dir", help='Path to use as persistent build directory', metavar='PATH')
    group.add_argument("--build-package", action=CommaDelimitedListAction, dest='build_packages', default=[], help='Additional packages needed for build script', metavar='PACKAGE')
    group.add_argument("--postinst-script", help='Postinstall script to run inside image', metavar='PATH')
    group.add_argument('--use-git-files', type=parse_boolean,
                       help='Ignore any files that git itself ignores (default: guess)')
    group.add_argument('--git-files', choices=('cached', 'others'),
                       help='Whether to include untracked files (default: others)')
    group.add_argument("--with-network", action='store_true', help='Run build and postinst scripts with network access (instead of private network)')
    group.add_argument("--settings", dest='nspawn_settings', help='Add in .spawn settings file', metavar='PATH')

    group = parser.add_argument_group("Partitions")
    group.add_argument("--root-size", help='Set size of root partition (only raw_ext4, raw_btrfs, raw_xfs)', metavar='BYTES')
    group.add_argument("--esp-size", help='Set size of EFI system partition (only raw_ext4, raw_btrfs, raw_squashfs, raw_xfs)', metavar='BYTES')
    group.add_argument("--swap-size", help='Set size of swap partition (only raw_ext4, raw_btrfs, raw_squashfs, raw_xfs)', metavar='BYTES')
    group.add_argument("--home-size", help='Set size of /home partition (only raw_ext4, raw_squashfs, raw_xfs)', metavar='BYTES')
    group.add_argument("--srv-size", help='Set size of /srv partition (only raw_ext4, raw_squashfs, raw_xfs)', metavar='BYTES')

    group = parser.add_argument_group("Validation (only raw_ext4, raw_btrfs, raw_squashfs, raw_xfs, tar)")
    group.add_argument("--checksum", action='store_true', help='Write SHA256SUMS file')
    group.add_argument("--sign", action='store_true', help='Write and sign SHA256SUMS file')
    group.add_argument("--key", help='GPG key to use for signing')
    group.add_argument("--bmap", action='store_true', help='Write block map file (.bmap) for bmaptool usage (only raw_ext4, raw_btrfs)')
    group.add_argument("--password", help='Set the root password')

    group = parser.add_argument_group("Host configuration")
    group.add_argument("--extra-search-paths", action=ColonDelimitedListAction, default=[], help="List of colon-separated paths to look for programs before looking in PATH")

    group = parser.add_argument_group("Additional Configuration")
    group.add_argument('-C', "--directory", help='Change to specified directory before doing anything', metavar='PATH')
    group.add_argument("--default", dest='default_path', help='Read configuration data from file', metavar='PATH')
    group.add_argument("--kernel-commandline", help='Set the kernel command line (only bootable images)')
    group.add_argument("--hostname", help="Set hostname")

    try:
        argcomplete.autocomplete(parser)
    except NameError:
        pass

    args = cast(CommandLineArguments, parser.parse_args(namespace=CommandLineArguments()))

    if args.verb == "help":
        parser.print_help()
        sys.exit(0)

    return args

def parse_bytes(bytes: Optional[str]) -> Optional[int]:
    if bytes is None:
        return bytes

    if bytes.endswith('G'):
        factor = 1024**3
    elif bytes.endswith('M'):
        factor = 1024**2
    elif bytes.endswith('K'):
        factor = 1024
    else:
        factor = 1

    if factor > 1:
        bytes = bytes[:-1]

    result = int(bytes) * factor
    if result <= 0:
        raise ValueError("Size out of range")

    if result % 512 != 0:
        raise ValueError("Size not a multiple of 512")

    return result

def detect_distribution() -> Tuple[Optional[str], Optional[str]]:
    try:
        f = open("/etc/os-release")
    except IOError:
        try:
            f = open("/usr/lib/os-release")
        except IOError:
            return None, None

    id = None
    version_id = None
    version_codename = None
    extracted_codename = None

    for ln in f:
        if ln.startswith("ID="):
            id = ln[3:].strip()
        if ln.startswith("VERSION_ID="):
            version_id = ln[11:].strip()
        if ln.startswith("VERSION_CODENAME="):
            version_codename = ln[17:].strip()
        if ln.startswith("VERSION="):
            # extract Debian release codename
            version_str = ln[8:].strip()
            debian_codename_re = r'\((.*?)\)'

            codename_list = re.findall(debian_codename_re, version_str)
            if len(codename_list) == 1:
                extracted_codename = codename_list[0]

    if id == "clear-linux-os":  # FIXME: don't hard-code distro-specific details
        id = "clear"

    d: Optional[str] = None
    if id is not None and id in distros.list_distros():
        d = id

    if d == 'debian' and (version_codename or extracted_codename):  # FIXME: don't hard-code distro-specific details
        # debootstrap needs release codenames, not version numbers
        if version_codename:
            version_id = version_codename
        else:
            version_id = extracted_codename

    return d, version_id

def parse_boolean(s: str) -> bool:
    "Parse 1/true/yes as true and 0/false/no as false"
    if s in {"1", "true", "yes"}:
        return True

    if s in {"0", "false", "no"}:
        return False

    raise ValueError("Invalid literal for bool(): {!r}".format(s))

def process_setting(args: CommandLineArguments, section: str, key: Optional[str], value: Any) -> bool:
    if section == "Distribution":
        if key == "Distribution":
            if args.distribution is None:
                args.distribution = value
        elif key == "Release":
            if args.release is None:
                args.release = value
        elif key == "Repositories":
            list_value = value if type(value) == list else value.split()
            if args.repositories is None:
                args.repositories = list_value
            else:
                args.repositories.extend(list_value)
        elif key == "Mirror":
            if args.mirror is None:
                args.mirror = value
        elif key is None:
            return True
        else:
            return False
    elif section == "Output":
        if key == "Format":
            if args.output_format is None:
                args.output_format = value
        elif key == "Output":
            if args.output is None:
                args.output = value
        elif key == "OutputDirectory":
            if args.output_dir is None:
                args.output_dir = value
        elif key == "Force":
            if not args.force:
                args.force = parse_boolean(value)
        elif key == "Bootable":
            if args.bootable is None:
                args.bootable = parse_boolean(value)
        elif key == "KernelCommandLine":
            if args.kernel_commandline is None:
                args.kernel_commandline = value
        elif key == "SecureBoot":
            if not args.secure_boot:
                args.secure_boot = parse_boolean(value)
        elif key == "SecureBootKey":
            if args.secure_boot_key is None:
                args.secure_boot_key = value
        elif key == "SecureBootCertificate":
            if args.secure_boot_certificate is None:
                args.secure_boot_certificate = value
        elif key == "ReadOnly":
            if not args.read_only:
                args.read_only = parse_boolean(value)
        elif key == "Encrypt":
            if args.encrypt is None:
                if value not in ("all", "data"):
                    raise ValueError("Invalid encryption setting: " + value)
                args.encrypt = value
        elif key == "Verity":
            if args.verity is None:
                args.verity = parse_boolean(value)
        elif key == "Compress":
            if args.compress is None:
                args.compress = parse_boolean(value)
        elif key == "XZ":
            if args.xz is None:
                args.xz = parse_boolean(value)
        elif key == "QCow2":
            if args.qcow2 is None:
                args.qcow2 = parse_boolean(value)
        elif key == "Hostname":
            if not args.hostname:
                args.hostname = value
        elif key is None:
            return True
        else:
            return False
    elif section == "Packages":
        if key == "Packages":
            list_value = value if type(value) == list else value.split()
            args.packages.extend(list_value)
        elif key == "WithDocs":
            if not args.with_docs:
                args.with_docs = parse_boolean(value)
        elif key == "WithTests":
            if not args.with_tests:
                args.with_tests = parse_boolean(value)
        elif key == "Cache":
            if args.cache_path is None:
                args.cache_path = value
        elif key == "ExtraTrees":
            list_value = value if type(value) == list else value.split()
            args.extra_trees.extend(list_value)
        elif key == "SkeletonTrees":
            list_value = value if type(value) == list else value.split()
            args.skeleton_trees.extend(list_value)
        elif key == "BuildScript":
            if args.build_script is None:
                args.build_script = value
        elif key == "BuildSources":
            if args.build_sources is None:
                args.build_sources = value
        elif key == "BuildDirectory":
            if args.build_dir is None:
                args.build_dir = value
        elif key == "BuildPackages":
            list_value = value if type(value) == list else value.split()
            args.build_packages.extend(list_value)
        elif key in {"PostinstallScript", "PostInstallationScript"}:
            if args.postinst_script is None:
                args.postinst_script = value
        elif key == "WithNetwork":
            if not args.with_network:
                args.with_network = parse_boolean(value)
        elif key == "NSpawnSettings":
            if args.nspawn_settings is None:
                args.nspawn_settings = value
        elif key is None:
            return True
        else:
            return False
    elif section == "Partitions":
        if key == "RootSize":
            if args.root_size is None:
                args.root_size = value
        elif key == "ESPSize":
            if args.esp_size is None:
                args.esp_size = value
        elif key == "SwapSize":
            if args.swap_size is None:
                args.swap_size = value
        elif key == "HomeSize":
            if args.home_size is None:
                args.home_size = value
        elif key == "SrvSize":
            if args.srv_size is None:
                args.srv_size = value
        elif key is None:
            return True
        else:
            return False
    elif section == "Validation":
        if key == "CheckSum":
            if not args.checksum:
                args.checksum = parse_boolean(value)
        elif key == "Sign":
            if not args.sign:
                args.sign = parse_boolean(value)
        elif key == "Key":
            if args.key is None:
                args.key = value
        elif key == "Bmap":
                args.bmap = parse_boolean(value)
        elif key == "Password":
            if args.password is None:
                args.password = value
        elif key is None:
            return True
        else:
            return False
    elif section == "Host":
        if key == "ExtraSearchPaths":
            list_value = value if type(value) == list else value.split()
            for v in list_value:
                args.extra_search_paths.extend(v.split(":"))
    else:
        return False

    return True

def load_defaults_file(fname: str, options: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Dict[str, Any]]]:
    try:
        f = open(fname)
    except FileNotFoundError:
        return None

    config = configparser.ConfigParser(delimiters='=')
    config.optionxform = str  # type: ignore # mypy 0.641 erroneously throws a fit for some reason
    config.read_file(f)

    # this is used only for validation
    args = parse_args()

    for section in config.sections():
        if not process_setting(args, section, None, None):
            sys.stderr.write("Unknown section in {}, ignoring: [{}]\n".format(fname, section))
            continue
        if section not in options:
            options[section] = {}
        for key in config[section]:
            if not process_setting(args, section, key, config[section][key]):
                sys.stderr.write("Unknown key in section [{}] in {}, ignoring: {}=\n".format(section, fname, key))
                continue
            if section == "Packages" and key in ["Packages", "ExtraTrees", "BuildPackages"]:
                if key in options[section]:
                    options[section][key].extend(config[section][key].split())
                else:
                    options[section][key] = config[section][key].split()
            else:
                options[section][key] = config[section][key]
    return options

def load_defaults(args: CommandLineArguments) -> None:
    fname = "mkosi.default" if args.default_path is None else args.default_path

    config: Dict[str, Dict[str, str]] = {}
    load_defaults_file(fname, config)

    defaults_dir = fname + '.d'
    if os.path.isdir(defaults_dir):
        for defaults_file in sorted(os.listdir(defaults_dir)):
            defaults_path = os.path.join(defaults_dir, defaults_file)
            if os.path.isfile(defaults_path):
                load_defaults_file(defaults_path, config)

    for section in config.keys():
        for key in config[section]:
            process_setting(args, section, key, config[section][key])

def find_nspawn_settings(args: CommandLineArguments) -> None:
    if args.nspawn_settings is not None:
        return

    if os.path.exists("mkosi.nspawn"):
        args.nspawn_settings = "mkosi.nspawn"

def find_extra(args: CommandLineArguments) -> None:
    if os.path.isdir("mkosi.extra"):
        args.extra_trees.append("mkosi.extra")
    if os.path.isfile("mkosi.extra.tar"):
        args.extra_trees.append("mkosi.extra.tar")

def find_skeleton(args: CommandLineArguments) -> None:
    if os.path.isdir("mkosi.skeleton"):
        args.skeleton_trees.append("mkosi.skeleton")
    if os.path.isfile("mkosi.skeleton.tar"):
        args.skeleton_trees.append("mkosi.skeleton.tar")

def find_cache(args: CommandLineArguments) -> None:

    if args.cache_path is not None:
        return

    if os.path.exists("mkosi.cache/"):
        args.cache_path = "mkosi.cache/" + args.distribution

        # Clear has a release number that can be used, however the
        # cache is valid (and more efficient) across releases.
        if args.distribution != 'clear' and args.release is not None:  # FIXME: don't hard-code distro-specific details
            args.cache_path += "~" + args.release

def find_build_script(args: CommandLineArguments) -> None:
    if args.build_script is not None:
        return

    if os.path.exists("mkosi.build"):
        args.build_script = "mkosi.build"

def find_build_sources(args: CommandLineArguments) -> None:
    if args.build_sources is not None:
        return

    args.build_sources = os.getcwd()

def find_build_dir(args: CommandLineArguments) -> None:
    if args.build_dir is not None:
        return

    if os.path.exists("mkosi.builddir/"):
        args.build_dir = "mkosi.builddir"

def find_postinst_script(args: CommandLineArguments) -> None:
    if args.postinst_script is not None:
        return

    if os.path.exists("mkosi.postinst"):
        args.postinst_script = "mkosi.postinst"

def find_output_dir(args: CommandLineArguments) -> None:
    if args.output_dir is not None:
        return

    if os.path.exists("mkosi.output/"):
        args.output_dir = "mkosi.output"

def require_private_file(name: str, description: str) -> None:
    mode = os.stat(name).st_mode & 0o777
    if mode & 0o007:
        warn("Permissions of '{}' of '{}' are too open.\n" +
             "When creating {} files use an access mode that restricts access to the owner only.",
             name, oct(mode), description)

def find_passphrase(args: CommandLineArguments) -> None:

    if args.encrypt is None:
        args.passphrase = None
        return

    try:
        require_private_file('mkosi.passphrase', 'passphrase')

        args.passphrase = { 'type': 'file', 'content': 'mkosi.passphrase' }

    except FileNotFoundError:
        while True:
            passphrase = getpass.getpass("Please enter passphrase: ")
            passphrase_confirmation = getpass.getpass("Passphrase confirmation: ")
            if passphrase == passphrase_confirmation:
                args.passphrase = { 'type': 'stdin', 'content': passphrase }
                break

            sys.stderr.write("Passphrase doesn't match confirmation. Please try again.\n")

def find_password(args: CommandLineArguments) -> None:

    if args.password is not None:
        return

    try:
        require_private_file('mkosi.rootpw', 'root password')

        with open('mkosi.rootpw') as f:
            args.password = f.read().strip()

    except FileNotFoundError:
        pass

def find_secure_boot(args: CommandLineArguments) -> None:
    if not args.secure_boot:
        return

    if args.secure_boot_key is None:
        if os.path.exists("mkosi.secure-boot.key"):
            args.secure_boot_key = "mkosi.secure-boot.key"

    if args.secure_boot_certificate is None:
        if os.path.exists("mkosi.secure-boot.crt"):
            args.secure_boot_certificate = "mkosi.secure-boot.crt"

def strip_suffixes(path: str) -> str:
    t = path
    while True:
        if t.endswith(".xz"):
            t = t[:-3]
        elif t.endswith(".raw"):
            t = t[:-4]
        elif t.endswith(".tar"):
            t = t[:-4]
        elif t.endswith(".qcow2"):
            t = t[:-6]
        else:
            break

    return t

def build_nspawn_settings_path(path: str) -> str:
    return strip_suffixes(path) + ".nspawn"

def build_root_hash_file_path(path: str) -> str:
    return strip_suffixes(path) + ".roothash"

def load_args() -> CommandLineArguments:
    args = parse_args()

    if args.directory is not None:
        os.chdir(args.directory)

    load_defaults(args)
    find_nspawn_settings(args)
    find_extra(args)
    find_skeleton(args)
    find_build_script(args)
    find_build_sources(args)
    find_build_dir(args)
    find_postinst_script(args)
    find_output_dir(args)
    find_password(args)
    find_passphrase(args)
    find_secure_boot(args)

    args.extra_search_paths = expand_paths(args.extra_search_paths)

    if args.cmdline and not verbs.get_verb(args.verb).HAS_ARGS:
        die("Additional parameters only accepted for {}.".format(has_args_list))

    args.force = args.force_count > 0

    if args.output_format is None:
        args.output_format = OutputFormat.raw_ext4
    else:
        args.output_format = OutputFormat[args.output_format]

    if args.distribution is None or args.release is None:
        d, r = detect_distribution()

        if args.distribution is None:
            args.distribution = d

        if args.distribution == d and d != 'clear' and args.release is None:  # FIXME: don't hard-code distro-specific details
            args.release = r

    if args.distribution is None:
        die("Couldn't detect distribution.")

    if args.release is None:
        args.release = distros.get_distro(args.distribution).DEFAULT_RELEASE

    find_cache(args)

    if args.mirror is None:
        args.mirror = distros.get_distro(args.distribution).DEFAULT_MIRROR

    if args.bootable:
        if args.output_format in (OutputFormat.directory, OutputFormat.subvolume, OutputFormat.tar):
            die("Directory, subvolume and tar images cannot be booted.")

    if args.encrypt is not None:
        if args.output_format not in RAW_FORMATS:
            die("Encryption is only supported for raw ext4, btrfs or squashfs images.")

        if args.encrypt == "data" and args.output_format == OutputFormat.raw_btrfs:
            die("'data' encryption mode not supported on btrfs, use 'all' instead.")

        if args.encrypt == "all" and args.verity:
            die("'all' encryption mode may not be combined with Verity.")

    if args.sign:
        args.checksum = True

    if args.output is None:
        if args.output_format in RAW_FORMATS:
            if args.qcow2:
                args.output = "image.qcow2"
            else:
                args.output = "image.raw"

            if args.xz:
                args.output += ".xz"
        elif args.output_format == OutputFormat.tar:
            args.output = "image.tar.xz"
        else:
            args.output = "image"

    if args.output_dir is not None:
        args.output_dir = os.path.abspath(args.output_dir)

        if "/" not in args.output:
            args.output = os.path.join(args.output_dir, args.output)
        else:
            warn('Ignoring configured output directory as output file is a qualified path.')

    if args.incremental or args.verb == "clean":
        args.cache_pre_dev = args.output + ".cache-pre-dev"
        args.cache_pre_inst = args.output + ".cache-pre-inst"
    else:
        args.cache_pre_dev = None
        args.cache_pre_inst = None

    args.output = os.path.abspath(args.output)

    if args.output_format == OutputFormat.tar:
        args.xz = True

    if args.output_format == OutputFormat.raw_squashfs:
        args.read_only = True
        args.compress = True
        args.root_size = None

    if args.verity:
        args.read_only = True
        args.output_root_hash_file = build_root_hash_file_path(args.output)

    if args.checksum:
        args.output_checksum = os.path.join(os.path.dirname(args.output), "SHA256SUMS")

    if args.sign:
        args.output_signature = os.path.join(os.path.dirname(args.output), "SHA256SUMS.gpg")

    if args.bmap:
        args.output_bmap = args.output + ".bmap"

    if args.nspawn_settings is not None:
        args.nspawn_settings = os.path.abspath(args.nspawn_settings)
        args.output_nspawn_settings = build_nspawn_settings_path(args.output)

    if args.build_script is not None:
        args.build_script = os.path.abspath(args.build_script)

    if args.build_sources is not None:
        args.build_sources = os.path.abspath(args.build_sources)

    if args.build_dir is not None:
        args.build_dir = os.path.abspath(args.build_dir)

    if args.postinst_script is not None:
        args.postinst_script = os.path.abspath(args.postinst_script)

    if args.cache_path is not None:
        args.cache_path = os.path.abspath(args.cache_path)

    if args.extra_trees:
        for i in range(len(args.extra_trees)):
            args.extra_trees[i] = os.path.abspath(args.extra_trees[i])

    if args.skeleton_trees is not None:
        for i in range(len(args.skeleton_trees)):
            args.skeleton_trees[i] = os.path.abspath(args.skeleton_trees[i])

    args.root_size = parse_bytes(args.root_size)
    args.home_size = parse_bytes(args.home_size)
    args.srv_size = parse_bytes(args.srv_size)
    args.esp_size = parse_bytes(args.esp_size)
    args.swap_size = parse_bytes(args.swap_size)

    if args.output_format in (OutputFormat.raw_ext4, OutputFormat.raw_btrfs) and args.root_size is None:
        args.root_size = 1024*1024*1024  # 1GiB

    if args.output_format == OutputFormat.raw_xfs and args.root_size is None:
        args.root_size = 1300*1024*1024  # 1.27GiB

    if args.bootable and args.esp_size is None:
        args.esp_size = 256*1024*1024  # 256MiB

    args.verity_size = None

    if args.bootable and args.kernel_commandline is None:
        args.kernel_commandline = "rhgb quiet selinux=0 audit=0 rw"

    if args.secure_boot_key is not None:
        args.secure_boot_key = os.path.abspath(args.secure_boot_key)

    if args.secure_boot_certificate is not None:
        args.secure_boot_certificate = os.path.abspath(args.secure_boot_certificate)

    if args.secure_boot:
        if args.secure_boot_key is None:
            die("UEFI SecureBoot enabled, but couldn't find private key. (Consider placing it in mkosi.secure-boot.key?)")

        if args.secure_boot_certificate is None:
            die("UEFI SecureBoot enabled, but couldn't find certificate. (Consider placing it in mkosi.secure-boot.crt?)")

    if args.verb in ("shell", "boot", "qemu"):
        if args.output_format == OutputFormat.tar:
            die("Sorry, can't acquire shell in or boot a tar archive.")
        if args.xz:
            die("Sorry, can't acquire shell in or boot an XZ compressed image.")

    if args.verb in ("shell", "boot"):
        if args.qcow2:
            die("Sorry, can't acquire shell in or boot a qcow2 image.")

    if args.verb == "qemu":
        if args.output_format not in RAW_FORMATS:
            die("Sorry, can't boot non-raw images with qemu.")

    return args

def expand_paths(paths: List[str]) -> List[str]:
    if not paths:
        return []

    environ = os.environ.copy()
    # Add a fake SUDO_HOME variable to allow non-root users specify
    # paths in their home when using mkosi via sudo.
    sudo_user = os.getenv("SUDO_USER")
    if sudo_user and "SUDO_HOME" not in environ:
        environ["SUDO_HOME"] = os.path.expanduser("~{}".format(sudo_user))

    # No os.path.expandvars because it treats unset variables as empty.
    expanded = []
    for path in paths:
        try:
            path = string.Template(path).substitute(environ)
            expanded.append(path)
        except KeyError:
            # Skip path if it uses a variable not defined.
            pass
    return expanded
