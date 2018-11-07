# PYTHON_ARGCOMPLETE_OK
# SPDX-License-Identifier: LGPL-2.1+

import argparse
import collections
import configparser
import contextlib
import crypt
import ctypes
import ctypes.util
import errno
import fcntl
import getpass
import glob
import hashlib
import os
import platform
import re
import shlex
import shutil
import stat
import string
import sys
import tempfile
import urllib.request
import uuid
from enum import Enum
from subprocess import DEVNULL, PIPE, CompletedProcess, run
from typing import (
    Any,
    BinaryIO,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    NoReturn,
    Optional,
    Set,
    TextIO,
    Tuple,
    cast,
)

try:
    import argcomplete
except ImportError:
    pass


__version__ = '4'

if sys.version_info < (3, 5):
    sys.exit("Sorry, we need at least Python 3.5.")

# TODO
# - volatile images
# - work on device nodes
# - allow passing env vars

def die(message: str, status: int=1) -> NoReturn:
    assert status >= 1 and status < 128
    sys.stderr.write(message + "\n")
    sys.exit(status)

def warn(message: str, *args: Any, **kwargs: Any) -> None:
    sys.stderr.write('WARNING: ' + message.format(*args, **kwargs) + '\n')


class CommandLineArguments(argparse.Namespace):
    """Type-hinted storage for command line arguments."""

    swap_partno: Optional[int] = None
    esp_partno: Optional[int] = None


class OutputFormat(Enum):
    raw_ext4 = 1
    raw_gpt = 1  # Kept for backwards compatibility
    raw_btrfs = 2
    raw_squashfs = 3
    directory = 4
    subvolume = 5
    tar = 6
    raw_xfs = 7

RAW_RW_FS_FORMATS = (
    OutputFormat.raw_ext4,
    OutputFormat.raw_btrfs,
    OutputFormat.raw_xfs
)

RAW_FORMATS = (*RAW_RW_FS_FORMATS, OutputFormat.raw_squashfs)

class Distribution(Enum):
    fedora = 1
    debian = 2
    ubuntu = 3
    arch = 4
    opensuse = 5
    mageia = 6
    centos = 7
    clear = 8

GPT_ROOT_X86           = uuid.UUID("44479540f29741b29af7d131d5f0458a")
GPT_ROOT_X86_64        = uuid.UUID("4f68bce3e8cd4db196e7fbcaf984b709")
GPT_ROOT_ARM           = uuid.UUID("69dad7102ce44e3cb16c21a1d49abed3")
GPT_ROOT_ARM_64        = uuid.UUID("b921b0451df041c3af444c6f280d3fae")
GPT_ROOT_IA64          = uuid.UUID("993d8d3df80e4225855a9daf8ed7ea97")
GPT_ESP                = uuid.UUID("c12a7328f81f11d2ba4b00a0c93ec93b")
GPT_SWAP               = uuid.UUID("0657fd6da4ab43c484e50933c84b4f4f")
GPT_HOME               = uuid.UUID("933ac7e12eb44f13b8440e14e2aef915")
GPT_SRV                = uuid.UUID("3b8f842520e04f3b907f1a25a76f98e8")
GPT_ROOT_X86_VERITY    = uuid.UUID("d13c5d3bb5d1422ab29f9454fdc89d76")
GPT_ROOT_X86_64_VERITY = uuid.UUID("2c7357edebd246d9aec123d437ec2bf5")
GPT_ROOT_ARM_VERITY    = uuid.UUID("7386cdf2203c47a9a498f2ecce45a2d6")
GPT_ROOT_ARM_64_VERITY = uuid.UUID("df3300ced69f4c92978c9bfb0f38d820")
GPT_ROOT_IA64_VERITY   = uuid.UUID("86ed10d5b60745bb8957d350f23d0571")

CLONE_NEWNS = 0x00020000

FEDORA_KEYS_MAP = {
    '23': '34EC9CBA',
    '24': '81B46521',
    '25': 'FDB19C98',
    '26': '64DAB85D',
    '27': 'F5282EE4',
    '28': '9DB62FB1',
    '29': '429476B4',
    '30': 'CFC659B9',
}

# 1 MB at the beginning of the disk for the GPT disk label, and
# another MB at the end (this is actually more than needed.)
GPT_HEADER_SIZE = 1024*1024
GPT_FOOTER_SIZE = 1024*1024

GPTRootTypePair = collections.namedtuple('GPTRootTypePair', 'root verity')

def gpt_root_native() -> GPTRootTypePair:
    """The tag for the native GPT root partition

    Returns a tuple of two tags: for the root partition and for the
    matching verity partition.
    """
    if platform.machine() == "x86_64":
        return GPTRootTypePair(GPT_ROOT_X86_64, GPT_ROOT_X86_64_VERITY)
    elif platform.machine() == "aarch64":
        return GPTRootTypePair(GPT_ROOT_ARM_64, GPT_ROOT_ARM_64_VERITY)
    else:
        die("Unknown architecture {}.".format(platform.machine()))

def unshare(flags: int) -> None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

    if libc.unshare(ctypes.c_int(flags)) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))

def format_bytes(bytes: int) -> str:
    if bytes >= 1024*1024*1024:
        return "{:0.1f}G".format(bytes / 1024**3)
    if bytes >= 1024*1024:
        return "{:0.1f}M".format(bytes / 1024**2)
    if bytes >= 1024:
        return "{:0.1f}K".format(bytes / 1024)

    return "{}B".format(bytes)

def roundup512(x: int) -> int:
    return (x + 511) & ~511

def print_step(text: str) -> None:
    sys.stderr.write("‣ \033[0;1;39m" + text + "\033[0m\n")

def print_running_cmd(cmdline: Iterable[str]) -> None:
    sys.stderr.write("‣ \033[0;1;39mRunning command:\033[0m\n")
    sys.stderr.write(" ".join(shlex.quote(x) for x in cmdline) + "\n")

def mkdir_last(path: str, mode: int=0o777) -> str:
    """Create directory path

    Only the final component will be created, so this is different than mkdirs().
    """
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        if not os.path.isdir(path):
            raise
    return path

_IOC_NRBITS   = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS  = 2

_IOC_NRSHIFT   = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT  = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE  = 0
_IOC_WRITE = 1
_IOC_READ  = 2

def _IOC(dir: int, type: int, nr: int, argtype: str) -> int:
    size = {'int': 4, 'size_t': 8}[argtype]
    return dir<<_IOC_DIRSHIFT | type<<_IOC_TYPESHIFT | nr<<_IOC_NRSHIFT | size<<_IOC_SIZESHIFT


def _IOW(type: int, nr: int, size: str) -> int:
    return _IOC(_IOC_WRITE, type, nr, size)

FICLONE = _IOW(0x94, 9, 'int')

@contextlib.contextmanager
def open_close(path: str, flags: int, mode: int=0o664) -> Iterator[int]:
    fd = os.open(path, flags | os.O_CLOEXEC, mode)
    try:
        yield fd
    finally:
        os.close(fd)

def _reflink(oldfd: int, newfd: int) -> None:
    fcntl.ioctl(newfd, FICLONE, oldfd)

def copy_fd(oldfd: int, newfd: int) -> None:
    try:
        _reflink(oldfd, newfd)
    except OSError as e:
        if e.errno not in {errno.EXDEV, errno.EOPNOTSUPP}:
            raise
        shutil.copyfileobj(open(oldfd, 'rb', closefd=False),
                           open(newfd, 'wb', closefd=False))

def copy_file_object(oldobject: BinaryIO, newobject: BinaryIO) -> None:
    try:
        _reflink(oldobject.fileno(), newobject.fileno())
    except OSError as e:
        if e.errno not in {errno.EXDEV, errno.EOPNOTSUPP}:
            raise
        shutil.copyfileobj(oldobject, newobject)

def copy_symlink(oldpath: str, newpath: str) -> None:
    src = os.readlink(oldpath)
    os.symlink(src, newpath)

def copy_file(oldpath: str, newpath: str) -> None:
    if os.path.islink(oldpath):
        copy_symlink(oldpath, newpath)
        return

    with open_close(oldpath, os.O_RDONLY) as oldfd:
        st = os.stat(oldfd)

        try:
            with open_close(newpath, os.O_WRONLY|os.O_CREAT|os.O_EXCL, st.st_mode) as newfd:
                copy_fd(oldfd, newfd)
        except FileExistsError:
            os.unlink(newpath)
            with open_close(newpath, os.O_WRONLY|os.O_CREAT, st.st_mode) as newfd:
                copy_fd(oldfd, newfd)
    shutil.copystat(oldpath, newpath, follow_symlinks=False)

def symlink_f(target: str, path: str) -> None:
    try:
        os.symlink(target, path)
    except FileExistsError:
        os.unlink(path)
        os.symlink(target, path)

def copy(oldpath: str, newpath: str) -> None:
    try:
        mkdir_last(newpath)
    except FileExistsError:
        # something that is not a directory already exists
        os.unlink(newpath)
        mkdir_last(newpath)

    for entry in os.scandir(oldpath):
        newentry = os.path.join(newpath, entry.name)
        if entry.is_dir(follow_symlinks=False):
            copy(entry.path, newentry)
        elif entry.is_symlink():
            target = os.readlink(entry.path)
            symlink_f(target, newentry)
            shutil.copystat(entry.path, newentry, follow_symlinks=False)
        else:
            st = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(st.st_mode):
                copy_file(entry.path, newentry)
            else:
                print('Ignoring', entry.path)
                continue
    shutil.copystat(oldpath, newpath, follow_symlinks=True)

@contextlib.contextmanager
def complete_step(text: str, text2: Optional[str]=None) -> Iterator[List[Any]]:
    print_step(text + '...')
    args: List[Any] = []
    yield args
    if text2 is None:
        text2 = text + ' complete'
    print_step(text2.format(*args) + '.')

@complete_step('Detaching namespace')
def init_namespace(args: CommandLineArguments) -> None:
    args.original_umask = os.umask(0o000)
    unshare(CLONE_NEWNS)
    run(["mount", "--make-rslave", "/"], check=True)

def setup_workspace(args: CommandLineArguments) -> tempfile.TemporaryDirectory:
    print_step("Setting up temporary workspace.")
    if args.output_format in (OutputFormat.directory, OutputFormat.subvolume):
        d = tempfile.TemporaryDirectory(dir=os.path.dirname(args.output), prefix='.mkosi-')
    else:
        d = tempfile.TemporaryDirectory(dir='/var/tmp', prefix='mkosi-')

    print_step("Temporary workspace in " + d.name + " is now set up.")
    return d

def btrfs_subvol_create(path: str, mode: int=0o755) -> None:
    m = os.umask(~mode & 0o7777)
    run(["btrfs", "subvol", "create", path], check=True)
    os.umask(m)

def btrfs_subvol_delete(path: str) -> None:
    # Extract the path of the subvolume relative to the filesystem
    c = run(["btrfs", "subvol", "show", path],
            stdout=PIPE, stderr=DEVNULL, universal_newlines=True, check=True)
    subvol_path = c.stdout.splitlines()[0]
    # Make the subvolume RW again if it was set RO by btrfs_subvol_delete
    run(["btrfs", "property", "set", path, "ro", "false"], check=True)
    # Recursively delete the direct children of the subvolume
    c = run(["btrfs", "subvol", "list", "-o", path],
            stdout=PIPE, stderr=DEVNULL, universal_newlines=True, check=True)
    for line in c.stdout.splitlines():
        if not line:
            continue
        child_subvol_path = line.split(" ", 8)[-1]
        child_path = os.path.normpath(os.path.join(
            path,
            os.path.relpath(child_subvol_path, subvol_path)
        ))
        btrfs_subvol_delete(child_path)
    # Delete the subvolume now that all its descendants have been deleted
    run(["btrfs", "subvol", "delete", path], stdout=DEVNULL, stderr=DEVNULL, check=True)

def btrfs_subvol_make_ro(path: str, b: bool=True) -> None:
    run(["btrfs", "property", "set", path, "ro", "true" if b else "false"], check=True)

def image_size(args: CommandLineArguments) -> int:
    size = GPT_HEADER_SIZE + GPT_FOOTER_SIZE

    if args.root_size is not None:
        size += args.root_size
    if args.home_size is not None:
        size += args.home_size
    if args.srv_size is not None:
        size += args.srv_size
    if args.bootable:
        size += args.esp_size
    if args.swap_size is not None:
        size += args.swap_size
    if args.verity_size is not None:
        size += args.verity_size

    return size

def disable_cow(path: str) -> None:
    """Disable copy-on-write if applicable on filesystem"""

    run(["chattr", "+C", path], stdout=DEVNULL, stderr=DEVNULL, check=False)

def determine_partition_table(args: CommandLineArguments) -> Tuple[str, bool]:

    pn = 1
    table = "label: gpt\n"
    run_sfdisk = False

    if args.bootable:
        table += 'size={}, type={}, name="ESP System Partition"\n'.format(args.esp_size // 512, GPT_ESP)
        args.esp_partno = pn
        pn += 1
        run_sfdisk = True
    else:
        args.esp_partno = None

    if args.swap_size is not None:
        table += 'size={}, type={}, name="Swap Partition"\n'.format(args.swap_size // 512, GPT_SWAP)
        args.swap_partno = pn
        pn += 1
        run_sfdisk = True
    else:
        args.swap_partno = None

    args.home_partno = None
    args.srv_partno = None

    if args.output_format != OutputFormat.raw_btrfs:
        if args.home_size is not None:
            table += 'size={}, type={}, name="Home Partition"\n'.format(args.home_size // 512, GPT_HOME)
            args.home_partno = pn
            pn += 1
            run_sfdisk = True

        if args.srv_size is not None:
            table += 'size={}, type={}, name="Server Data Partition"\n'.format(args.srv_size // 512, GPT_SRV)
            args.srv_partno = pn
            pn += 1
            run_sfdisk = True

    if args.output_format != OutputFormat.raw_squashfs:
        table += 'type={}, attrs={}, name="Root Partition"\n'.format(
            gpt_root_native().root,
            "GUID:60" if args.read_only and args.output_format != OutputFormat.raw_btrfs else "")
        run_sfdisk = True

    args.root_partno = pn
    pn += 1

    if args.verity:
        args.verity_partno = pn
        pn += 1
    else:
        args.verity_partno = None

    return table, run_sfdisk


def create_image(args: CommandLineArguments, workspace: str, for_cache: bool) -> Optional[BinaryIO]:
    if args.output_format not in RAW_FORMATS:
        return None

    with complete_step('Creating partition table',
                       'Created partition table as {.name}') as output:

        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output), prefix='.mkosi-', delete=not for_cache))
        output.append(f)
        disable_cow(f.name)
        f.truncate(image_size(args))

        table, run_sfdisk = determine_partition_table(args)

        if run_sfdisk:
            run(["sfdisk", "--color=never", f.name], input=table.encode("utf-8"), check=True)
            run(["sync"])

        args.ran_sfdisk = run_sfdisk

    return f

def reuse_cache_image(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> Tuple[Optional[BinaryIO], bool]:
    if not args.incremental:
        return None, False
    if args.output_format not in RAW_RW_FS_FORMATS:
        return None, False

    fname = args.cache_pre_dev if run_build_script else args.cache_pre_inst
    if for_cache:
        if fname and os.path.exists(fname):
            # Cache already generated, skip generation, note that manually removing the exising cache images is
            # necessary if Packages or BuildPackages change
            return None, True
        else:
            return None, False

    if fname is None:
        return None, False

    with complete_step('Basing off cached image ' + fname,
                       'Copied cached image as {.name}') as output:

        try:
            source = open(fname, 'rb')
        except FileNotFoundError:
            return None, False

        with source:
            f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output), prefix='.mkosi-'))
            output.append(f)

            # So on one hand we want CoW off, since this stuff will
            # have a lot of random write accesses. On the other we
            # want the copy to be snappy, hence we do want CoW. Let's
            # ask for both, and let the kernel figure things out:
            # let's turn off CoW on the file, but start with a CoW
            # copy. On btrfs that works: the initial copy is made as
            # CoW but later changes do not result in CoW anymore.

            disable_cow(f.name)
            copy_file_object(source, f)

        table, run_sfdisk = determine_partition_table(args)
        args.ran_sfdisk = run_sfdisk

    return f, True

@contextlib.contextmanager
def attach_image_loopback(args: CommandLineArguments, raw: Optional[BinaryIO]) -> Iterator[Optional[str]]:
    if raw is None:
        yield None
        return

    with complete_step('Attaching image file',
                       'Attached image file as {}') as output:
        c = run(["losetup", "--find", "--show", "--partscan", raw.name],
                stdout=PIPE, check=True)
        loopdev = c.stdout.decode("utf-8").strip()
        output.append(loopdev)

    try:
        yield loopdev
    finally:
        with complete_step('Detaching image file'):
            run(["losetup", "--detach", loopdev], check=True)

def partition(loopdev: str, partno: int) -> str:
    return loopdev + "p" + str(partno)

def prepare_swap(args: CommandLineArguments, loopdev: Optional[str], cached: bool) -> None:
    if loopdev is None:
        return
    if cached:
        return
    if args.swap_partno is None:
        return

    with complete_step('Formatting swap partition'):
        run(["mkswap", "-Lswap", partition(loopdev, args.swap_partno)], check=True)

def prepare_esp(args: CommandLineArguments, loopdev: Optional[str], cached: bool) -> None:
    if loopdev is None:
        return
    if cached:
        return
    if args.esp_partno is None:
        return

    with complete_step('Formatting ESP partition'):
        run(["mkfs.fat", "-nEFI", "-F32", partition(loopdev, args.esp_partno)], check=True)

def mkfs_ext4(label: str, mount: str, dev: str) -> None:
    run(["mkfs.ext4", "-L", label, "-M", mount, dev], check=True)

def mkfs_btrfs(label: str, dev: str) -> None:
    run(["mkfs.btrfs", "-L", label, "-d", "single", "-m", "single", dev], check=True)

def mkfs_xfs(label: str, dev: str) -> None:
    run(["mkfs.xfs", "-n", "ftype=1", "-L", label, dev], check=True)

def luks_format(dev: str, passphrase: Dict[str, str]) -> None:

    if passphrase['type'] == 'stdin':
        passphrase = (passphrase['content'] + "\n").encode("utf-8")
        run(["cryptsetup", "luksFormat", "--batch-mode", dev], input=passphrase, check=True)
    else:
        assert passphrase['type'] == 'file'
        run(["cryptsetup", "luksFormat", "--batch-mode", dev, passphrase['content']], check=True)

def luks_open(dev: str, passphrase: Dict[str, str]) -> str:

    name = str(uuid.uuid4())

    if passphrase['type'] == 'stdin':
        passphrase = (passphrase['content'] + "\n").encode("utf-8")
        run(["cryptsetup", "open", "--type", "luks", dev, name], input=passphrase, check=True)
    else:
        assert passphrase['type'] == 'file'
        run(["cryptsetup", "--key-file", passphrase['content'], "open", "--type", "luks", dev, name], check=True)

    return os.path.join("/dev/mapper", name)

def luks_close(dev: Optional[str], text: str) -> None:
    if dev is None:
        return

    with complete_step(text):
        run(["cryptsetup", "close", dev], check=True)

def luks_format_root(args: CommandLineArguments, loopdev: str, run_build_script: bool, cached: bool, inserting_squashfs: bool=False) -> None:

    if args.encrypt != "all":
        return
    if args.root_partno is None:
        return
    if args.output_format == OutputFormat.raw_squashfs and not inserting_squashfs:
        return
    if run_build_script:
        return
    if cached:
        return

    with complete_step("LUKS formatting root partition"):
        luks_format(partition(loopdev, args.root_partno), args.passphrase)

def luks_format_home(args: CommandLineArguments, loopdev: str, run_build_script: bool, cached: bool) -> None:

    if args.encrypt is None:
        return
    if args.home_partno is None:
        return
    if run_build_script:
        return
    if cached:
        return

    with complete_step("LUKS formatting home partition"):
        luks_format(partition(loopdev, args.home_partno), args.passphrase)

def luks_format_srv(args: CommandLineArguments, loopdev: str, run_build_script: bool, cached: bool) -> None:

    if args.encrypt is None:
        return
    if args.srv_partno is None:
        return
    if run_build_script:
        return
    if cached:
        return

    with complete_step("LUKS formatting server data partition"):
        luks_format(partition(loopdev, args.srv_partno), args.passphrase)

def luks_setup_root(args: CommandLineArguments, loopdev: str, run_build_script: bool, inserting_squashfs: bool=False) -> Optional[str]:

    if args.encrypt != "all":
        return None
    if args.root_partno is None:
        return None
    if args.output_format == OutputFormat.raw_squashfs and not inserting_squashfs:
        return None
    if run_build_script:
        return None

    with complete_step("Opening LUKS root partition"):
        return luks_open(partition(loopdev, args.root_partno), args.passphrase)

def luks_setup_home(args: CommandLineArguments, loopdev: str, run_build_script: bool) -> Optional[str]:

    if args.encrypt is None:
        return None
    if args.home_partno is None:
        return None
    if run_build_script:
        return None

    with complete_step("Opening LUKS home partition"):
        return luks_open(partition(loopdev, args.home_partno), args.passphrase)

def luks_setup_srv(args: CommandLineArguments, loopdev: str, run_build_script: bool) -> Optional[str]:

    if args.encrypt is None:
        return None
    if args.srv_partno is None:
        return None
    if run_build_script:
        return None

    with complete_step("Opening LUKS server data partition"):
        return luks_open(partition(loopdev, args.srv_partno), args.passphrase)

@contextlib.contextmanager
def luks_setup_all(args: CommandLineArguments, loopdev: str, run_build_script: bool) -> Iterator[Tuple[Optional[str], Optional[str], Optional[str]]]:

    if args.output_format in (OutputFormat.directory, OutputFormat.subvolume, OutputFormat.tar):
        yield (None, None, None)
        return

    try:
        root = luks_setup_root(args, loopdev, run_build_script)
        try:
            home = luks_setup_home(args, loopdev, run_build_script)
            try:
                srv = luks_setup_srv(args, loopdev, run_build_script)

                yield (partition(loopdev, args.root_partno) if root is None else root,
                       partition(loopdev, args.home_partno) if home is None else home,
                       partition(loopdev, args.srv_partno) if srv is None else srv)
            finally:
                luks_close(srv, "Closing LUKS server data partition")
        finally:
            luks_close(home, "Closing LUKS home partition")
    finally:
        luks_close(root, "Closing LUKS root partition")

def prepare_root(args: CommandLineArguments, dev: str, cached: bool) -> None:
    if dev is None:
        return
    if args.output_format == OutputFormat.raw_squashfs:
        return
    if cached:
        return

    with complete_step('Formatting root partition'):
        if args.output_format == OutputFormat.raw_btrfs:
            mkfs_btrfs("root", dev)
        elif args.output_format == OutputFormat.raw_xfs:
            mkfs_xfs("root", dev)
        else:
            mkfs_ext4("root", "/", dev)

def prepare_home(args: CommandLineArguments, dev: str, cached: bool) -> None:
    if dev is None:
        return
    if cached:
        return

    with complete_step('Formatting home partition'):
        mkfs_ext4("home", "/home", dev)

def prepare_srv(args: CommandLineArguments, dev: str, cached: bool) -> None:
    if dev is None:
        return
    if cached:
        return

    with complete_step('Formatting server data partition'):
        mkfs_ext4("srv", "/srv", dev)

def mount_loop(args: CommandLineArguments, dev: str, where: str, read_only: bool=False) -> None:
    os.makedirs(where, 0o755, True)

    options = "-odiscard"

    if args.compress and args.output_format == OutputFormat.raw_btrfs:
        options += ",compress"

    if read_only:
        options += ",ro"

    run(["mount", "-n", dev, where, options], check=True)

def mount_bind(what: str, where: str) -> None:
    os.makedirs(what, 0o755, True)
    os.makedirs(where, 0o755, True)
    run(["mount", "--bind", what, where], check=True)

def mount_tmpfs(where: str) -> None:
    os.makedirs(where, 0o755, True)
    run(["mount", "tmpfs", "-t", "tmpfs", where], check=True)

@contextlib.contextmanager
def mount_image(args: CommandLineArguments, workspace: str, loopdev: str, root_dev: str, home_dev: str, srv_dev: str, root_read_only: bool=False) -> Iterator[None]:
    if loopdev is None:
        yield None
        return

    with complete_step('Mounting image'):
        root = os.path.join(workspace, "root")

        if args.output_format != OutputFormat.raw_squashfs:
            mount_loop(args, root_dev, root, root_read_only)

        if home_dev is not None:
            mount_loop(args, home_dev, os.path.join(root, "home"))

        if srv_dev is not None:
            mount_loop(args, srv_dev, os.path.join(root, "srv"))

        if args.esp_partno is not None:
            mount_loop(args, partition(loopdev, args.esp_partno), os.path.join(root, "efi"))

        # Make sure /tmp and /run are not part of the image
        mount_tmpfs(os.path.join(root, "run"))
        mount_tmpfs(os.path.join(root, "tmp"))

    try:
        yield
    finally:
        with complete_step('Unmounting image'):
            umount(root)

@complete_step("Assigning hostname")
def install_etc_hostname(args: CommandLineArguments, workspace: str) -> None:
    etc_hostname = os.path.join(workspace, "root", "etc/hostname")

    # Always unlink first, so that we don't get in trouble due to a
    # symlink or suchlike. Also if no hostname is configured we really
    # don't want the file to exist, so that systemd's implicit
    # hostname logic can take effect.
    try:
        os.unlink(etc_hostname)
    except FileNotFoundError:
        pass

    if args.hostname:
        open(etc_hostname, "w").write(args.hostname + "\n")

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

@contextlib.contextmanager
def mount_cache(args: CommandLineArguments, workspace: str) -> Iterator[None]:

    if args.cache_path is None:
        yield
        return

    # We can't do this in mount_image() yet, as /var itself might have to be created as a subvolume first
    with complete_step('Mounting Package Cache'):
        if args.distribution in (Distribution.fedora, Distribution.mageia):
            mount_bind(args.cache_path, os.path.join(workspace, "root", "var/cache/dnf"))
        elif args.distribution == Distribution.centos:
            # We mount both the YUM and the DNF cache in this case, as YUM might just be redirected to DNF even if we invoke the former
            mount_bind(os.path.join(args.cache_path, "yum"), os.path.join(workspace, "root", "var/cache/yum"))
            mount_bind(os.path.join(args.cache_path, "dnf"), os.path.join(workspace, "root", "var/cache/dnf"))
        elif args.distribution in (Distribution.debian, Distribution.ubuntu):
            mount_bind(args.cache_path, os.path.join(workspace, "root", "var/cache/apt/archives"))
        elif args.distribution == Distribution.arch:
            mount_bind(args.cache_path, os.path.join(workspace, "root", "var/cache/pacman/pkg"))
        elif args.distribution == Distribution.opensuse:
            mount_bind(args.cache_path, os.path.join(workspace, "root", "var/cache/zypp/packages"))
    try:
        yield
    finally:
        with complete_step('Unmounting Package Cache'):
            for d in ("var/cache/dnf", "var/cache/yum", "var/cache/apt/archives", "var/cache/pacman/pkg", "var/cache/zypp/packages"):
                umount(os.path.join(workspace, "root", d))

def umount(where: str) -> None:
    # Ignore failures and error messages
    run(["umount", "--recursive", "-n", where], stdout=DEVNULL, stderr=DEVNULL)

@complete_step('Setting up basic OS tree')
def prepare_tree(args: CommandLineArguments, workspace: str, run_build_script: bool, cached: bool) -> None:

    if args.output_format == OutputFormat.subvolume:
        btrfs_subvol_create(os.path.join(workspace, "root"))
    else:
        mkdir_last(os.path.join(workspace, "root"))

    if args.output_format in (OutputFormat.subvolume, OutputFormat.raw_btrfs):

        if cached and args.output_format is OutputFormat.raw_btrfs:
            return

        btrfs_subvol_create(os.path.join(workspace, "root", "home"))
        btrfs_subvol_create(os.path.join(workspace, "root", "srv"))
        btrfs_subvol_create(os.path.join(workspace, "root", "var"))
        btrfs_subvol_create(os.path.join(workspace, "root", "var/tmp"), 0o1777)
        os.mkdir(os.path.join(workspace, "root", "var/lib"))
        btrfs_subvol_create(os.path.join(workspace, "root", "var/lib/machines"), 0o700)

    if cached:
        return

    if args.bootable:
        # We need an initialized machine ID for the boot logic to work
        os.mkdir(os.path.join(workspace, "root", "etc"), 0o755)
        with open(os.path.join(workspace, "root", "etc/machine-id"), "w") as f:
            f.write(args.machine_id)
            f.write("\n")

        os.mkdir(os.path.join(workspace, "root", "efi/EFI"), 0o700)
        os.mkdir(os.path.join(workspace, "root", "efi/EFI/BOOT"), 0o700)
        os.mkdir(os.path.join(workspace, "root", "efi/EFI/Linux"), 0o700)
        os.mkdir(os.path.join(workspace, "root", "efi/EFI/systemd"), 0o700)
        os.mkdir(os.path.join(workspace, "root", "efi/loader"), 0o700)
        os.mkdir(os.path.join(workspace, "root", "efi/loader/entries"), 0o700)
        os.mkdir(os.path.join(workspace, "root", "efi", args.machine_id), 0o700)

        os.mkdir(os.path.join(workspace, "root", "boot"), 0o700)
        os.symlink("../efi", os.path.join(workspace, "root", "boot/efi"))
        os.symlink("efi/loader", os.path.join(workspace, "root", "boot/loader"))
        os.symlink("efi/" + args.machine_id, os.path.join(workspace, "root", "boot", args.machine_id))

        os.mkdir(os.path.join(workspace, "root", "etc/kernel"), 0o755)

        with open(os.path.join(workspace, "root", "etc/kernel/cmdline"), "w") as cmdline:
            cmdline.write(args.kernel_commandline)
            cmdline.write("\n")

    if run_build_script:
        os.mkdir(os.path.join(workspace, "root", "root"), 0o750)
        os.mkdir(os.path.join(workspace, "root", "root/dest"), 0o755)

        if args.build_dir is not None:
            os.mkdir(os.path.join(workspace, "root", "root/build"), 0o755)

def patch_file(filepath: str, line_rewriter: Callable[[str], str]) -> None:
    temp_new_filepath = filepath + ".tmp.new"

    with open(filepath, "r") as old:
        with open(temp_new_filepath, "w") as new:
            for line in old:
                new.write(line_rewriter(line))

    shutil.copystat(filepath, temp_new_filepath)
    os.remove(filepath)
    shutil.move(temp_new_filepath, filepath)

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

def run_workspace_command(args: CommandLineArguments, workspace: str, *cmd: str, network: bool=False, env: Dict[str, str]={}, nspawn_params: List[str]=[]) -> None:

    cmdline = ["systemd-nspawn",
               '--quiet',
               "--directory=" + os.path.join(workspace, "root"),
               "--uuid=" + args.machine_id,
               "--machine=mkosi-" + uuid.uuid4().hex,
               "--as-pid2",
               "--register=no",
               "--bind=" + var_tmp(workspace) + ":/var/tmp",
               "--setenv=SYSTEMD_OFFLINE=1" ]

    if network:
        # If we're using the host network namespace, use the same resolver
        cmdline += ["--bind-ro=/etc/resolv.conf"]
    else:
        cmdline += ["--private-network"]

    cmdline += [ "--setenv={}={}".format(k, v) for k, v in env.items() ]

    if nspawn_params:
        cmdline += nspawn_params

    cmdline += ['--', *cmd]
    run(cmdline, check=True)

def check_if_url_exists(url: str) -> Optional[bool]:
    req = urllib.request.Request(url, method="HEAD")
    try:
        if urllib.request.urlopen(req):
            return True
    except:
        return False

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

def invoke_dnf(args: CommandLineArguments, workspace: str, repositories: List[str], base_packages: List[str], boot_packages: List[str], config_file: str) -> None:

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

@complete_step('Installing Clear Linux')
def install_clear(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
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

    run([swupd_extract,
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

@complete_step('Installing Fedora')
def install_fedora(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    if args.release == 'rawhide':
        last = sorted(FEDORA_KEYS_MAP)[-1]
        warn('Assuming rawhide is version {} -- '.format(last) +
             'You may specify otherwise with --release=rawhide-<version>')
        args.releasever = last
    elif args.release.startswith('rawhide-'):
        args.release, args.releasever = args.release.split('-')
        sys.stderr.write('Fedora rawhide - release version: %s\n' % args.releasever)
    else:
        args.releasever = args.release

    masked = disable_kernel_install(args, workspace)

    gpg_key = "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-%s-x86_64" % args.releasever
    if os.path.exists(gpg_key):
        gpg_key = "file://%s" % gpg_key
    else:
        gpg_key = "https://getfedora.org/static/%s.txt" % FEDORA_KEYS_MAP[args.releasever]

    if args.mirror:
        baseurl = "{args.mirror}/releases/{args.release}/Everything/x86_64/os/".format(args=args)
        if not check_if_url_exists("%s/media.repo" % baseurl):
            baseurl = "{args.mirror}/development/{args.release}/Everything/x86_64/os/".format(args=args)

        release_url = "baseurl=%s" % baseurl
        updates_url = "baseurl={args.mirror}/updates/{args.release}/x86_64/".format(args=args)
    else:
        release_url = ("metalink=https://mirrors.fedoraproject.org/metalink?" +
                       "repo=fedora-{args.release}&arch=x86_64".format(args=args))
        updates_url = ("metalink=https://mirrors.fedoraproject.org/metalink?" +
                       "repo=updates-released-f{args.release}&arch=x86_64".format(args=args))

    config_file = os.path.join(workspace, "dnf.conf")
    with open(config_file, "w") as f:
        f.write("""\
[main]
gpgcheck=1

[fedora]
name=Fedora {args.release} - base
{release_url}
gpgkey={gpg_key}

[updates]
name=Fedora {args.release} - updates
{updates_url}
gpgkey={gpg_key}
""".format(args=args,
           gpg_key=gpg_key,
           release_url=release_url,
           updates_url=updates_url))

    invoke_dnf(args, workspace,
               args.repositories if args.repositories else ["fedora", "updates"],
               ["systemd", "fedora-release", "passwd", "glibc-minimal-langpack"],
               ["kernel-core", "systemd-udev", "binutils"],
               config_file)

    with open(os.path.join(workspace, 'root', 'etc/locale.conf'), 'w') as f:
        f.write('LANG=C.UTF-8\n')

    reenable_kernel_install(args, workspace, masked)

@complete_step('Installing Mageia')
def install_mageia(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:

    masked = disable_kernel_install(args, workspace)

    # Mageia does not (yet) have RPM GPG key on the web
    gpg_key = '/etc/pki/rpm-gpg/RPM-GPG-KEY-Mageia'
    if os.path.exists(gpg_key):
        gpg_key = "file://%s" % gpg_key
#    else:
#        gpg_key = "https://getfedora.org/static/%s.txt" % FEDORA_KEYS_MAP[args.release]

    if args.mirror:
        baseurl = "{args.mirror}/distrib/{args.release}/x86_64/media/core/".format(args=args)
        release_url = "baseurl=%s/release/" % baseurl
        updates_url = "baseurl=%s/updates/" % baseurl
    else:
        baseurl = "https://www.mageia.org/mirrorlist/?release={args.release}&arch=x86_64&section=core".format(args=args)
        release_url = "mirrorlist=%s&repo=release" % baseurl
        updates_url = "mirrorlist=%s&repo=updates" % baseurl

    config_file = os.path.join(workspace, "dnf.conf")
    with open(config_file, "w") as f:
        f.write("""\
[main]
gpgcheck=1

[mageia]
name=Mageia {args.release} Core Release
{release_url}
gpgkey={gpg_key}

[updates]
name=Mageia {args.release} Core Updates
{updates_url}
gpgkey={gpg_key}
""".format(args=args,
           gpg_key=gpg_key,
           release_url=release_url,
           updates_url=updates_url))

    invoke_dnf(args, workspace,
               args.repositories if args.repositories else ["mageia", "updates"],
               ["basesystem-minimal"],
               ["kernel-server-latest", "binutils"],
               config_file)

    reenable_kernel_install(args, workspace, masked)

def invoke_yum(args: CommandLineArguments, workspace: str, repositories: List[str], base_packages: List[str], boot_packages: List[str], config_file: str) -> None:

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

@complete_step('Installing CentOS')
def install_centos(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:

    masked = disable_kernel_install(args, workspace)

    gpg_key = "/etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-%s" % args.release
    if os.path.exists(gpg_key):
        gpg_key = "file://%s" % gpg_key
    else:
        gpg_key = "https://www.centos.org/keys/RPM-GPG-KEY-CentOS-%s" % args.release

    if args.mirror:
        release_url = "baseurl={args.mirror}/centos/{args.release}/os/x86_64".format(args=args)
        updates_url = "baseurl={args.mirror}/centos/{args.release}/updates/x86_64/".format(args=args)
    else:
        release_url = "mirrorlist=http://mirrorlist.centos.org/?release={args.release}&arch=x86_64&repo=os".format(args=args)
        updates_url = "mirrorlist=http://mirrorlist.centos.org/?release={args.release}&arch=x86_64&repo=updates".format(args=args)

    config_file = os.path.join(workspace, "yum.conf")
    with open(config_file, "w") as f:
        f.write("""\
[main]
gpgcheck=1

[base]
name=CentOS-{args.release} - Base
{release_url}
gpgkey={gpg_key}

[updates]
name=CentOS-{args.release} - Updates
{updates_url}
gpgkey={gpg_key}
""".format(args=args,
           gpg_key=gpg_key,
           release_url=release_url,
           updates_url=updates_url))

    invoke_dnf_or_yum(args, workspace,
                      args.repositories if args.repositories else ["base", "updates"],
                      ["systemd", "centos-release", "passwd"],
                      ["kernel", "systemd-udev", "binutils"],
                      config_file)

    reenable_kernel_install(args, workspace, masked)

def install_debian_or_ubuntu(args: CommandLineArguments, workspace: str, run_build_script: bool, mirror: str) -> None:
    repos = args.repositories if args.repositories else ["main"]
    # Ubuntu needs the 'universe' repo to install 'dracut'
    if args.distribution == Distribution.ubuntu and args.bootable and 'universe' not in repos:
        repos.append('universe')
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

    # Debootstrap is not smart enough to deal correctly with alternative dependencies
    # Installing libpam-systemd via debootstrap results in systemd-shim being installed
    # Therefore, prefer to install via apt from inside the container
    extra_packages = [ 'dbus', 'libpam-systemd']

    # Also install extra packages via the secondary APT run, because it is smarter and
    # can deal better with any conflicts
    extra_packages.extend(args.packages)

    if run_build_script:
        extra_packages.extend(args.build_packages)

    # Work around debian bug #835628
    os.makedirs(os.path.join(workspace, "root/etc/dracut.conf.d"), exist_ok=True)
    with open(os.path.join(workspace, "root/etc/dracut.conf.d/99-generic.conf"), "w") as f:
        f.write("hostonly=no")

    if args.bootable:
        extra_packages += ["dracut"]
        if args.distribution == Distribution.ubuntu:
            extra_packages += ["linux-generic"]
        else:
            extra_packages += ["linux-image-amd64"]

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

    cmdline = ["/usr/bin/apt-get", "--assume-yes", "--no-install-recommends", "install"] + extra_packages
    run_workspace_command(args, workspace, network=True, env={'DEBIAN_FRONTEND': 'noninteractive', 'DEBCONF_NONINTERACTIVE_SEEN': 'true'}, *cmdline)
    os.unlink(policyrcd)

@complete_step('Installing Debian')
def install_debian(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    install_debian_or_ubuntu(args, workspace, run_build_script, args.mirror)

@complete_step('Installing Ubuntu')
def install_ubuntu(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    install_debian_or_ubuntu(args, workspace, run_build_script, args.mirror)

@complete_step('Installing Arch Linux')
def install_arch(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
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


@complete_step('Installing openSUSE')
def install_opensuse(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:

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
    run(["zypper", "--root", root, "addrepo", "-ck", release_url, "Main"], check=True)
    run(["zypper", "--root", root, "addrepo", "-ck", updates_url, "Updates"], check=True)

    if not args.with_docs:
        with open(os.path.join(root, "etc/zypp/zypp.conf"), "w") as f:
            f.write("rpm.install.excludedocs = yes\n")

    # The common part of the install comand.
    cmdline = ["zypper", "--root", root, "--gpg-auto-import-keys",
               "install", "-y", "--no-recommends"]
    #
    # Install the "minimal" package set.
    #
    run(cmdline + ["patterns-base-minimal_base"], check=True)

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
        run(cmdline + extra_packages, check=True)

    #
    # Disable packages caching in the image that was enabled
    # previously to populate the package cache.
    #
    run(["zypper", "--root", root, "modifyrepo", "-K", "Main"], check=True)
    run(["zypper", "--root", root, "modifyrepo", "-K", "Updates"], check=True)

    #
    # Tune dracut confs: openSUSE uses an old version of dracut that's
    # probably explain why we need to do those hacks.
    #
    if args.bootable:
        os.makedirs(os.path.join(root, "etc/dracut.conf.d"), exist_ok=True)

        with open(os.path.join(root, "etc/dracut.conf.d/99-mkosi.conf"), "w") as f:
            f.write("hostonly=no\n")

        # dracut from openSUSE is missing upstream commit 016613c774baf.
        with open(os.path.join(root, "etc/kernel/cmdline"), "w") as cmdline:
            cmdline.write(args.kernel_commandline + " root=/dev/gpt-auto-root\n")

def install_distribution(args: CommandLineArguments, workspace: str, run_build_script: bool, cached: bool) -> None:

    if cached:
        return

    install = {
        Distribution.fedora : install_fedora,
        Distribution.centos : install_centos,
        Distribution.mageia : install_mageia,
        Distribution.debian : install_debian,
        Distribution.ubuntu : install_ubuntu,
        Distribution.arch : install_arch,
        Distribution.opensuse : install_opensuse,
        Distribution.clear : install_clear,
    }

    install[args.distribution](args, workspace, run_build_script)

def reset_machine_id(workspace: str, run_build_script: bool, for_cache: bool) -> None:
    """Make /etc/machine-id an empty file.

    This way, on the next boot is either initialized and committed (if /etc is
    writable) or the image runs with a transient machine ID, that changes on
    each boot (if the image is read-only).
    """

    if run_build_script:
        return
    if for_cache:
        return

    with complete_step('Resetting machine ID'):
        machine_id = os.path.join(workspace, 'root', 'etc/machine-id')
        try:
            os.unlink(machine_id)
        except FileNotFoundError:
            pass
        open(machine_id, "w+b").close()
        dbus_machine_id = os.path.join(workspace, 'root', 'var/lib/dbus/machine-id')
        try:
            os.unlink(dbus_machine_id)
        except FileNotFoundError:
            pass
        else:
            os.symlink('../../../etc/machine-id', dbus_machine_id)

def reset_random_seed(workspace: str) -> None:
    """Remove random seed file, so that it is initialized on first boot"""

    with complete_step('Removing random seed'):
        random_seed = os.path.join(workspace, 'root', 'var/lib/systemd/random-seed')
        try:
            os.unlink(random_seed)
        except FileNotFoundError:
            pass

def set_root_password(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> None:
    "Set the root account password, or just delete it so it's easy to log in"

    if run_build_script:
        return
    if for_cache:
        return

    if args.password == '':
        with complete_step("Deleting root password"):
            jj = lambda line: (':'.join(['root', ''] + line.split(':')[2:])
                               if line.startswith('root:') else line)
            patch_file(os.path.join(workspace, 'root', 'etc/passwd'), jj)
    elif args.password:
        with complete_step("Setting root password"):
            password = crypt.crypt(args.password, crypt.mksalt(crypt.METHOD_SHA512))
            jj = lambda line: (':'.join(['root', password] + line.split(':')[2:])
                               if line.startswith('root:') else line)
            patch_file(os.path.join(workspace, 'root', 'etc/shadow'), jj)

def run_postinst_script(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> None:

    if args.postinst_script is None:
        return
    if for_cache:
        return

    with complete_step('Running postinstall script'):

        # We copy the postinst script into the build tree. We'd prefer
        # mounting it into the tree, but for that we'd need a good
        # place to mount it to. But if we create that we might as well
        # just copy the file anyway.

        shutil.copy2(args.postinst_script,
                     os.path.join(workspace, "root", "root/postinst"))

        run_workspace_command(args, workspace, "/root/postinst", "build" if run_build_script else "final", network=args.with_network)
        os.unlink(os.path.join(workspace, "root", "root/postinst"))

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
        return
    if len(kernel_files) > 1:
        warn('More than one kernel file found, will use {}', kernel_file)
    return kernel_file

def install_boot_loader_arch(args: CommandLineArguments, workspace: str) -> None:
    patch_file(os.path.join(workspace, "root", "etc/mkinitcpio.conf"),
               lambda line: "HOOKS=\"systemd modconf block sd-encrypt filesystems keyboard fsck\"\n" if line.startswith("HOOKS=") and args.encrypt == "all" else
                            "HOOKS=\"systemd modconf block filesystems fsck\"\n"                     if line.startswith("HOOKS=") else
                            line)

    workspace_root = os.path.join(workspace, "root")
    kernel_version = next(filter(lambda x: x[0].isdigit(), os.listdir(os.path.join(workspace_root, "lib/modules"))))
    run_workspace_command(args, workspace, "/usr/bin/kernel-install", "add", kernel_version, find_kernel_file(workspace_root, "/boot/vmlinuz-*"))

def install_boot_loader_debian(args: CommandLineArguments, workspace: str) -> None:
    kernel_version = next(filter(lambda x: x[0].isdigit(), os.listdir(os.path.join(workspace, "root", "lib/modules"))))

    run_workspace_command(args, workspace,
                          "/usr/bin/kernel-install", "add", kernel_version, "/boot/vmlinuz-" + kernel_version)

def install_boot_loader_ubuntu(args: CommandLineArguments, workspace: str) -> None:
    install_boot_loader_debian(args, workspace)

def install_boot_loader_opensuse(args: CommandLineArguments, workspace: str) -> None:
    install_boot_loader_debian(args, workspace)

def install_boot_loader_clear(args: CommandLineArguments, workspace: str, loopdev: str) -> None:
    nspawn_params = [
        # clr-boot-manager uses blkid in the device backing "/" to
        # figure out uuid and related parameters.
        "--bind-ro=/dev",
        "--property=DeviceAllow=" + loopdev,
        "--property=DeviceAllow=" + partition(loopdev, args.esp_partno),
        "--property=DeviceAllow=" + partition(loopdev, args.root_partno),

        # clr-boot-manager compiled in Clear Linux will assume EFI
        # partition is mounted in "/boot".
        "--bind=" + os.path.join(workspace, "root/efi") + ":/boot",
    ]
    run_workspace_command(args, workspace, "/usr/bin/clr-boot-manager", "update", "-i", nspawn_params=nspawn_params)

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: str, cached: bool) -> None:
    if not args.bootable:
        return

    if cached:
        return

    with complete_step("Installing boot loader"):
        shutil.copyfile(os.path.join(workspace, "root", "usr/lib/systemd/boot/efi/systemd-bootx64.efi"),
                        os.path.join(workspace, "root", "boot/efi/EFI/systemd/systemd-bootx64.efi"))

        shutil.copyfile(os.path.join(workspace, "root", "usr/lib/systemd/boot/efi/systemd-bootx64.efi"),
                        os.path.join(workspace, "root", "boot/efi/EFI/BOOT/bootx64.efi"))

        if args.distribution == Distribution.arch:
            install_boot_loader_arch(args, workspace)

        if args.distribution == Distribution.debian:
            install_boot_loader_debian(args, workspace)

        if args.distribution == Distribution.ubuntu:
            install_boot_loader_ubuntu(args, workspace)

        if args.distribution == Distribution.opensuse:
            install_boot_loader_opensuse(args, workspace)

        if args.distribution == Distribution.clear:
            install_boot_loader_clear(args, workspace, loopdev)

def install_extra_trees(args: CommandLineArguments, workspace: str, for_cache: bool) -> None:
    if not args.extra_trees:
        return

    if for_cache:
        return

    with complete_step('Copying in extra file trees'):
        for d in args.extra_trees:
            if os.path.isdir(d):
                copy(d, os.path.join(workspace, "root"))
            else:
                shutil.unpack_archive(d, os.path.join(workspace, "root"))

def install_skeleton_trees(args: CommandLineArguments, workspace: str, for_cache: bool) -> None:
    if not args.skeleton_trees:
        return

    with complete_step('Copying in skeleton file trees'):
        for d in args.skeleton_trees:
            if os.path.isdir(d):
                copy(d, os.path.join(workspace, "root"))
            else:
                shutil.unpack_archive(d, os.path.join(workspace, "root"))

def copy_git_files(src: str, dest: str, *, git_files: str) -> None:
    what_files = ['--exclude-standard', '--cached']
    if git_files == 'others':
        what_files += ['--others', '--exclude=.mkosi-*']

    c = run(['git', '-C', src, 'ls-files', '-z'] + what_files,
            stdout=PIPE,
            universal_newlines=False,
            check=True)
    files = {x.decode("utf-8") for x in c.stdout.rstrip(b'\0').split(b'\0')}

    # Get submodule files
    c = run(['git', '-C', src, 'submodule', 'status', '--recursive'],
            stdout=PIPE,
            universal_newlines=True,
            check=True)
    submodules = {x.split()[1] for x in c.stdout.splitlines()}

    # workaround for git-ls-files returning the path of submodules that we will
    # still parse
    files -= submodules

    for sm in submodules:
        c = run(['git', '-C', os.path.join(src, sm), 'ls-files', '-z'] + what_files,
                stdout=PIPE,
                universal_newlines=False,
                check=True)
        files |= {os.path.join(sm, x.decode("utf-8"))for x in c.stdout.rstrip(b'\0').split(b'\0')}
        files -= submodules

    del c

    for path in files:
        src_path = os.path.join(src, path)
        dest_path = os.path.join(dest, path)

        directory = os.path.dirname(dest_path)
        os.makedirs(directory, exist_ok=True)

        copy_file(src_path, dest_path)

def install_build_src(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> None:
    if not run_build_script:
        return
    if for_cache:
        return

    if args.build_script is None:
        return

    with complete_step('Copying in build script and sources'):
        copy_file(args.build_script,
                  os.path.join(workspace, "root", "root", os.path.basename(args.build_script)))

        if args.build_sources is not None:
            target = os.path.join(workspace, "root", "root/src")
            use_git = args.use_git_files
            if use_git is None:
                use_git = os.path.exists('.git') or os.path.exists(os.path.join(args.build_sources, '.git'))

            if use_git:
                copy_git_files(args.build_sources, target, git_files=args.git_files)
            else:
                ignore = shutil.ignore_patterns('.git',
                                                '.mkosi-*',
                                                '*.cache-pre-dev',
                                                '*.cache-pre-inst',
                                                os.path.basename(args.output_dir)+"/" if args.output_dir else "mkosi.output/",
                                                os.path.basename(args.cache_path)+"/" if args.cache_path else "mkosi.cache/",
                                                os.path.basename(args.build_dir)+"/" if args.build_dir else "mkosi.builddir/")
                shutil.copytree(args.build_sources, target, symlinks=True, ignore=ignore)

def install_build_dest(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> None:
    if run_build_script:
        return
    if for_cache:
        return

    if args.build_script is None:
        return

    with complete_step('Copying in build tree'):
        copy(os.path.join(workspace, "dest"), os.path.join(workspace, "root"))

def make_read_only(args: CommandLineArguments, workspace: str, for_cache: bool) -> None:
    if not args.read_only:
        return
    if for_cache:
        return

    if args.output_format not in (OutputFormat.raw_btrfs, OutputFormat.subvolume):
        return

    with complete_step('Marking root subvolume read-only'):
        btrfs_subvol_make_ro(os.path.join(workspace, "root"))

def make_tar(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> Optional[BinaryIO]:

    if run_build_script:
        return None
    if args.output_format != OutputFormat.tar:
        return None
    if for_cache:
        return None

    with complete_step('Creating archive'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output), prefix=".mkosi-"))
        run(["tar", "-C", os.path.join(workspace, "root"),
             "-c", "-J", "--xattrs", "--xattrs-include=*", "."],
            stdout=f, check=True)

    return f

def make_squashfs(args: CommandLineArguments, workspace: str, for_cache: bool) -> Optional[BinaryIO]:
    if args.output_format != OutputFormat.raw_squashfs:
        return None
    if for_cache:
        return None

    with complete_step('Creating squashfs file system'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output), prefix=".mkosi-squashfs"))
        run(["mksquashfs", os.path.join(workspace, "root"), f.name, "-comp", "lz4", "-noappend"],
            check=True)

    return f

def read_partition_table(loopdev: str) -> Tuple[List[str], int]:

    table = []
    last_sector = 0

    c = run(["sfdisk", "--dump", loopdev], stdout=PIPE, check=True)

    in_body = False
    for line in c.stdout.decode("utf-8").split('\n'):
        stripped = line.strip()

        if stripped == "":  # empty line is where the body begins
            in_body = True
            continue
        if not in_body:
            continue

        table.append(stripped)

        name, rest = stripped.split(":", 1)
        fields = rest.split(",")

        start = None
        size = None

        for field in fields:
            f = field.strip()

            if f.startswith("start="):
                start = int(f[6:])
            if f.startswith("size="):
                size = int(f[5:])

        if start is not None and size is not None:
            end = start + size
            if end > last_sector:
                last_sector = end

    return table, last_sector * 512

def insert_partition(args: CommandLineArguments, raw: BinaryIO, loopdev: str, partno: int, blob: BinaryIO, name: str, type_uuid: str, uuid: Optional[uuid.UUID]=None) -> int:

    if args.ran_sfdisk:
        old_table, last_partition_sector = read_partition_table(loopdev)
    else:
        # No partition table yet? Then let's fake one...
        old_table = []
        last_partition_sector = GPT_HEADER_SIZE

    blob_size = roundup512(os.stat(blob.name).st_size)
    luks_extra = 2*1024*1024 if args.encrypt == "all" else 0  # 2MB else 0
    new_size = last_partition_sector + blob_size + luks_extra + GPT_FOOTER_SIZE

    print_step("Resizing disk image to {}...".format(format_bytes(new_size)))

    os.truncate(raw.name, new_size)
    run(["losetup", "--set-capacity", loopdev], check=True)

    print_step("Inserting partition of {}...".format(format_bytes(blob_size)))

    table = "label: gpt\n"

    for t in old_table:
        table += t + "\n"

    if uuid is not None:
        table += "uuid=" + str(uuid) + ", "

    table += 'size={}, type={}, attrs=GUID:60, name="{}"\n'.format((blob_size + luks_extra) // 512, type_uuid, name)

    print(table)

    run(["sfdisk", "--color=never", loopdev], input=table.encode("utf-8"), check=True)
    run(["sync"])

    print_step("Writing partition...")

    if args.root_partno == partno:
        luks_format_root(args, loopdev, False, True)
        dev = luks_setup_root(args, loopdev, False, True)
    else:
        dev = None

    try:
        run(["dd", "if=" + blob.name, "of=" + (dev if dev is not None else partition(loopdev, partno))], check=True)
    finally:
        luks_close(dev, "Closing LUKS root partition")

    args.ran_sfdisk = True

    return blob_size

def insert_squashfs(args: CommandLineArguments, raw: BinaryIO, loopdev: str, squashfs: BinaryIO, for_cache: bool) -> None:
    if args.output_format != OutputFormat.raw_squashfs:
        return
    if for_cache:
        return

    with complete_step('Inserting squashfs root partition'):
        args.root_size = insert_partition(args, raw, loopdev, args.root_partno, squashfs,
                                          "Root Partition", gpt_root_native().root)

def make_verity(args: CommandLineArguments, dev: str, run_build_script: bool, for_cache: bool) -> Tuple[Optional[BinaryIO], Optional[str]]:

    if run_build_script or not args.verity:
        return None, None
    if for_cache:
        return None, None

    with complete_step('Generating verity hashes'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output), prefix=".mkosi-"))
        c = run(["veritysetup", "format", dev, f.name], stdout=PIPE, check=True)

        for line in c.stdout.decode("utf-8").split('\n'):
            if line.startswith("Root hash:"):
                root_hash = line[10:].strip()
                return f, root_hash

        raise ValueError('Root hash not found')

def insert_verity(args: CommandLineArguments, raw: BinaryIO, loopdev: str, verity: BinaryIO, root_hash: str, for_cache: bool) -> None:

    if verity is None:
        return
    if for_cache:
        return

    # Use the final 128 bit of the root hash as partition UUID of the verity partition
    u = uuid.UUID(root_hash[-32:])

    with complete_step('Inserting verity partition'):
        insert_partition(args, raw, loopdev, args.verity_partno, verity,
                         "Verity Partition", gpt_root_native().verity, u)

def patch_root_uuid(args: CommandLineArguments, loopdev: str, root_hash: Optional[str], for_cache: bool) -> None:

    if root_hash is None:
        return
    if for_cache:
        return

    # Use the first 128bit of the root hash as partition UUID of the root partition
    u = uuid.UUID(root_hash[:32])

    with complete_step('Patching root partition UUID'):
        run(["sfdisk", "--part-uuid", loopdev, str(args.root_partno), str(u)],
            check=True)

def install_unified_kernel(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool, root_hash: Optional[str]) -> None:

    # Iterates through all kernel versions included in the image and
    # generates a combined kernel+initrd+cmdline+osrelease EFI file
    # from it and places it in the /EFI/Linux directory of the
    # ESP. sd-boot iterates through them and shows them in the
    # menu. These "unified" single-file images have the benefit that
    # they can be signed like normal EFI binaries, and can encode
    # everything necessary to boot a specific root device, including
    # the root hash.

    if not args.bootable:
        return
    if for_cache:
        return

    # Don't bother running dracut if this is a development
    # build. Strictly speaking it would probably be a good idea to run
    # it, so that the development environment differs as little as
    # possible from the final build, but then again the initrd should
    # not be relevant for building, and dracut is simply very slow,
    # hence let's avoid it invoking it needlessly, given that we never
    # actually invoke the boot loader on the development image.
    if run_build_script:
        return

    if args.distribution not in (Distribution.fedora, Distribution.mageia):
        return

    with complete_step("Generating combined kernel + initrd boot file"):

        cmdline = args.kernel_commandline
        if root_hash is not None:
            cmdline += " roothash=" + root_hash

        for kver in os.scandir(os.path.join(workspace, "root", "usr/lib/modules")):
            if not kver.is_dir():
                continue

            boot_binary = "/efi/EFI/Linux/linux-" + kver.name
            if root_hash is not None:
                boot_binary += "-" + root_hash
            boot_binary += ".efi"

            dracut = ["/usr/bin/dracut",
                      "-v",
                      "--no-hostonly",
                      "--uefi",
                      "--kver", kver.name,
                      "--kernel-cmdline", cmdline]

            # Temporary fix until dracut includes these in the image anyway
            dracut += ("-i",) + ("/usr/lib/systemd/system/systemd-volatile-root.service",)*2 + \
                      ("-i",) + ("/usr/lib/systemd/systemd-volatile-root",)*2 + \
                      ("-i",) + ("/usr/lib/systemd/systemd-veritysetup",)*2 + \
                      ("-i",) + ("/usr/lib/systemd/system-generators/systemd-veritysetup-generator",)*2

            if args.output_format == OutputFormat.raw_squashfs:
                dracut += [ '--add-drivers', 'squashfs' ]

            dracut += [ '--add', 'qemu' ]

            dracut += [ boot_binary ]

            run_workspace_command(args, workspace, *dracut)

def secure_boot_sign(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool) -> None:

    if run_build_script:
        return
    if not args.bootable:
        return
    if not args.secure_boot:
        return
    if for_cache:
        return

    for path, dirnames, filenames in os.walk(os.path.join(workspace, "root", "efi")):
        for i in filenames:
            if not i.endswith(".efi") and not i.endswith(".EFI"):
                continue

            with complete_step("Signing EFI binary {} in ESP".format(i)):
                p = os.path.join(path, i)

                run(["sbsign",
                     "--key", args.secure_boot_key,
                     "--cert", args.secure_boot_certificate,
                     "--output", p + ".signed",
                     p],
                    check=True)

                os.rename(p + ".signed", p)

def xz_output(args: CommandLineArguments, raw: BinaryIO) -> BinaryIO:
    if args.output_format not in RAW_FORMATS:
        return raw

    if not args.xz:
        return raw

    xz_binary = "pxz" if shutil.which("pxz") else "xz"

    with complete_step('Compressing image file'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(prefix=".mkosi-", dir=os.path.dirname(args.output)))
        run([xz_binary, "-c", raw.name], stdout=f, check=True)

    return f

def qcow2_output(args: CommandLineArguments, raw: BinaryIO) -> BinaryIO:
    if args.output_format not in RAW_FORMATS:
        return raw

    if not args.qcow2:
        return raw

    with complete_step('Converting image file to qcow2'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(prefix=".mkosi-", dir=os.path.dirname(args.output)))
        run(["qemu-img", "convert", "-fraw", "-Oqcow2", raw.name, f.name], check=True)

    return f

def write_root_hash_file(args: CommandLineArguments, root_hash: Optional[str]) -> Optional[BinaryIO]:
    if root_hash is None:
        return None

    with complete_step('Writing .roothash file'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(mode='w+b', prefix='.mkosi',
                                                                 dir=os.path.dirname(args.output_root_hash_file)))
        f.write((root_hash + "\n").encode())

    return f

def copy_nspawn_settings(args: CommandLineArguments) -> Optional[BinaryIO]:
    if args.nspawn_settings is None:
        return None

    with complete_step('Copying nspawn settings file'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(mode="w+b", prefix=".mkosi-",
                                                                 dir=os.path.dirname(args.output_nspawn_settings)))

        with open(args.nspawn_settings, "rb") as c:
            f.write(c.read())

    return f

def hash_file(of: TextIO, sf: BinaryIO, fname: str) -> None:
    bs = 16*1024**2
    h = hashlib.sha256()

    sf.seek(0)
    buf = sf.read(bs)
    while len(buf) > 0:
        h.update(buf)
        buf = sf.read(bs)

    of.write(h.hexdigest() + " *" + fname + "\n")

def calculate_sha256sum(args: CommandLineArguments, raw: Optional[BinaryIO], tar: Optional[BinaryIO], root_hash_file: str, nspawn_settings: str) -> Optional[TextIO]:
    if args.output_format in (OutputFormat.directory, OutputFormat.subvolume):
        return None

    if not args.checksum:
        return None

    with complete_step('Calculating SHA256SUMS'):
        f: TextIO = cast(TextIO, tempfile.NamedTemporaryFile(mode="w+", prefix=".mkosi-", encoding="utf-8",
                                                             dir=os.path.dirname(args.output_checksum)))

        if raw is not None:
            hash_file(f, raw, os.path.basename(args.output))
        if tar is not None:
            hash_file(f, tar, os.path.basename(args.output))
        if root_hash_file is not None:
            hash_file(f, root_hash_file, os.path.basename(args.output_root_hash_file))
        if nspawn_settings is not None:
            hash_file(f, nspawn_settings, os.path.basename(args.output_nspawn_settings))

    return f

def calculate_signature(args: CommandLineArguments, checksum: Optional[TextIO]) -> Optional[BinaryIO]:
    if not args.sign:
        return None

    if checksum is None:
        return None

    with complete_step('Signing SHA256SUMS'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(mode="wb", prefix=".mkosi-",
                                                                 dir=os.path.dirname(args.output_signature)))

        cmdline = ["gpg", "--detach-sign"]

        if args.key is not None:
            cmdline += ["--default-key", args.key]

        checksum.seek(0)
        run(cmdline, stdin=checksum, stdout=f, check=True)

    return f

def calculate_bmap(args: CommandLineArguments, raw: BinaryIO) -> Optional[TextIO]:
    if not args.bmap:
        return None

    if args.output_format not in RAW_RW_FS_FORMATS:
        return None

    with complete_step('Creating BMAP file'):
        f: TextIO = cast(TextIO, tempfile.NamedTemporaryFile(mode="w+", prefix=".mkosi-", encoding="utf-8",
                                                             dir=os.path.dirname(args.output_bmap)))

        cmdline = ["bmaptool", "create", raw.name]
        run(cmdline, stdout=f, check=True)

    return f

def save_cache(args: CommandLineArguments, workspace: str, raw: str, cache_path: str) -> None:

    if cache_path is None or raw is None:
        return

    with complete_step('Installing cache copy ',
                       'Successfully installed cache copy ' + cache_path):

        if args.output_format in RAW_RW_FS_FORMATS:
            os.chmod(raw, 0o666 & ~args.original_umask)
            shutil.move(raw, cache_path)
        else:
            shutil.move(os.path.join(workspace, "root"), cache_path)

def link_output(args: CommandLineArguments, workspace: str, raw: str, tar: str) -> None:
    with complete_step('Linking image file',
                       'Successfully linked ' + args.output):
        if args.output_format in (OutputFormat.directory, OutputFormat.subvolume):
            os.rename(os.path.join(workspace, "root"), args.output)
        elif args.output_format in RAW_FORMATS:
            os.chmod(raw, 0o666 & ~args.original_umask)
            os.link(raw, args.output)
        else:
            os.chmod(tar, 0o666 & ~args.original_umask)
            os.link(tar, args.output)

def link_output_nspawn_settings(args: CommandLineArguments, path: str) -> None:
    if path is None:
        return

    with complete_step('Linking nspawn settings file',
                       'Successfully linked ' + args.output_nspawn_settings):
        os.chmod(path, 0o666 & ~args.original_umask)
        os.link(path, args.output_nspawn_settings)

def link_output_checksum(args: CommandLineArguments, checksum: str) -> None:
    if checksum is None:
        return

    with complete_step('Linking SHA256SUMS file',
                       'Successfully linked ' + args.output_checksum):
        os.chmod(checksum, 0o666 & ~args.original_umask)
        os.link(checksum, args.output_checksum)

def link_output_root_hash_file(args: CommandLineArguments, root_hash_file: str) -> None:
    if root_hash_file is None:
        return

    with complete_step('Linking .roothash file',
                       'Successfully linked ' + args.output_root_hash_file):
        os.chmod(root_hash_file, 0o666 & ~args.original_umask)
        os.link(root_hash_file, args.output_root_hash_file)

def link_output_signature(args: CommandLineArguments, signature: str) -> None:
    if signature is None:
        return

    with complete_step('Linking SHA256SUMS.gpg file',
                       'Successfully linked ' + args.output_signature):
        os.chmod(signature, 0o666 & ~args.original_umask)
        os.link(signature, args.output_signature)

def link_output_bmap(args: CommandLineArguments, bmap: str) -> None:
    if bmap is None:
        return

    with complete_step('Linking .bmap file',
                       'Successfully linked ' + args.output_bmap):
        os.chmod(bmap, 0o666 & ~args.original_umask)
        os.link(bmap, args.output_bmap)

def dir_size(path: str) -> int:
    sum = 0
    for entry in os.scandir(path):
        if entry.is_symlink():
            # We can ignore symlinks because they either point into our tree,
            # in which case we'll include the size of target directory anyway,
            # or outside, in which case we don't need to.
            continue
        elif entry.is_file():
            sum += entry.stat().st_blocks * 512
        elif entry.is_dir():
            sum += dir_size(entry.path)
    return sum

def print_output_size(args: CommandLineArguments) -> None:
    if args.output_format in (OutputFormat.directory, OutputFormat.subvolume):
        print_step("Resulting image size is " + format_bytes(dir_size(args.output)) + ".")
    else:
        st = os.stat(args.output)
        print_step("Resulting image size is " + format_bytes(st.st_size) + ", consumes " + format_bytes(st.st_blocks * 512) + ".")

def setup_package_cache(args: CommandLineArguments) -> Optional[tempfile.TemporaryDirectory]:
    with complete_step('Setting up package cache',
                       'Setting up package cache {} complete') as output:
        if args.cache_path is None:
            d = tempfile.TemporaryDirectory(dir=os.path.dirname(args.output), prefix=".mkosi-")
            args.cache_path = d.name
        else:
            os.makedirs(args.cache_path, 0o755, exist_ok=True)
            d = None
        output.append(args.cache_path)

    return d

class ListAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        l = getattr(namespace, self.dest)
        if l is None:
            l = []
        l.extend(values.split(self.delimiter))
        setattr(namespace, self.dest, l)

class CommaDelimitedListAction(ListAction):
    delimiter = ","

class ColonDelimitedListAction(ListAction):
    delimiter = ":"

def parse_args() -> CommandLineArguments:
    parser = argparse.ArgumentParser(description='Build Legacy-Free OS Images', add_help=False)

    group = parser.add_argument_group("Commands")
    group.add_argument("verb", choices=("build", "clean", "help", "summary", "shell", "boot", "qemu"), nargs='?', default="build", help='Operation to execute')
    group.add_argument("cmdline", nargs=argparse.REMAINDER, help="The command line to use for 'shell', 'boot', 'qemu'")
    group.add_argument('-h', '--help', action='help', help="Show this help")
    group.add_argument('--version', action='version', version='%(prog)s ' + __version__)

    group = parser.add_argument_group("Distribution")
    group.add_argument('-d', "--distribution", choices=Distribution.__members__, help='Distribution to install')
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

    args = parser.parse_args(namespace=CommandLineArguments())

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

def detect_distribution() -> Tuple[Optional[Distribution], Optional[str]]:
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

    if id == "clear-linux-os":
        id = "clear"

    d = Distribution.__members__.get(id, None)

    if d == Distribution.debian and (version_codename or extracted_codename):
        # debootstrap needs release codenames, not version numbers
        if version_codename:
            version_id = version_codename
        else:
            version_id = extracted_codename

    return d, version_id

def unlink_try_hard(path: str) -> None:
    try:
        os.unlink(path)
    except:
        pass

    try:
        btrfs_subvol_delete(path)
    except:
        pass

    try:
        shutil.rmtree(path)
    except:
        pass

def empty_directory(path: str) -> None:

    try:
        for f in os.listdir(path):
            unlink_try_hard(os.path.join(path, f))
    except FileNotFoundError:
        pass

def unlink_output(args: CommandLineArguments) -> None:
    if not args.force and args.verb != "clean":
        return

    with complete_step('Removing output files'):
        unlink_try_hard(args.output)

        if args.checksum:
            unlink_try_hard(args.output_checksum)

        if args.verity:
            unlink_try_hard(args.output_root_hash_file)

        if args.sign:
            unlink_try_hard(args.output_signature)

        if args.bmap:
            unlink_try_hard(args.output_bmap)

        if args.nspawn_settings is not None:
            unlink_try_hard(args.output_nspawn_settings)

    # We remove any cached images if either the user used --force
    # twice, or he/she called "clean" with it passed once. Let's also
    # remove the downloaded package cache if the user specified one
    # additional "--force".

    if args.verb == "clean":
        remove_build_cache = args.force_count > 0
        remove_package_cache = args.force_count > 1
    else:
        remove_build_cache = args.force_count > 1
        remove_package_cache = args.force_count > 2

    if remove_build_cache:
        if args.cache_pre_dev is not None or args.cache_pre_inst is not None:
            with complete_step('Removing incremental cache files'):
                if args.cache_pre_dev is not None:
                    unlink_try_hard(args.cache_pre_dev)

                if args.cache_pre_inst is not None:
                    unlink_try_hard(args.cache_pre_inst)

        if args.build_dir is not None:
            with complete_step('Clearing out build directory'):
                empty_directory(args.build_dir)

    if remove_package_cache:
        if args.cache_path is not None:
            with complete_step('Clearing out package cache'):
                empty_directory(args.cache_path)

def parse_boolean(s: str) -> bool:
    "Parse 1/true/yes as true and 0/false/no as false"
    if s in {"1", "true", "yes"}:
        return True

    if s in {"0", "false", "no"}:
        return False

    raise ValueError("Invalid literal for bool(): {!r}".format(s))

def process_setting(args: CommandLineArguments, section: str, key: str, value: Any) -> bool:
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
        return

    config = configparser.ConfigParser(delimiters='=')
    config.optionxform = str
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
        args.cache_path = "mkosi.cache/" + args.distribution.name

        # Clear has a release number that can be used, however the
        # cache is valid (and more efficient) across releases.
        if args.distribution != Distribution.clear and args.release is not None:
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

    if args.cmdline and args.verb not in ('shell', 'boot', 'qemu'):
        die("Additional parameters only accepted for 'shell', 'boot', 'qemu' invocations.")

    args.force = args.force_count > 0

    if args.output_format is None:
        args.output_format = OutputFormat.raw_ext4
    else:
        args.output_format = OutputFormat[args.output_format]

    if args.distribution is not None:
        args.distribution = Distribution[args.distribution]

    if args.distribution is None or args.release is None:
        d, r = detect_distribution()

        if args.distribution is None:
            args.distribution = d

        if args.distribution == d and d != Distribution.clear and args.release is None:
            args.release = r

    if args.distribution is None:
        die("Couldn't detect distribution.")

    if args.release is None:
        if args.distribution == Distribution.fedora:
            args.release = "29"
        elif args.distribution == Distribution.centos:
            args.release = "7"
        elif args.distribution == Distribution.mageia:
            args.release = "6"
        elif args.distribution == Distribution.debian:
            args.release = "unstable"
        elif args.distribution == Distribution.ubuntu:
            args.release = "artful"
        elif args.distribution == Distribution.opensuse:
            args.release = "tumbleweed"
        elif args.distribution == Distribution.clear:
            args.release = "latest"

    find_cache(args)

    if args.mirror is None:
        if args.distribution in (Distribution.fedora, Distribution.centos):
            args.mirror = None
        elif args.distribution == Distribution.debian:
            args.mirror = "http://deb.debian.org/debian"
        elif args.distribution == Distribution.ubuntu:
            args.mirror = "http://archive.ubuntu.com/ubuntu"
            if platform.machine() == "aarch64":
                args.mirror = "http://ports.ubuntu.com/"
        elif args.distribution == Distribution.arch:
            args.mirror = "https://mirrors.kernel.org/archlinux"
            if platform.machine() == "aarch64":
                args.mirror = "http://mirror.archlinuxarm.org"
        elif args.distribution == Distribution.opensuse:
            args.mirror = "http://download.opensuse.org"

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

def check_output(args: CommandLineArguments) -> None:
    for f in (args.output,
              args.output_checksum if args.checksum else None,
              args.output_signature if args.sign else None,
              args.output_bmap if args.bmap else None,
              args.output_nspawn_settings if args.nspawn_settings is not None else None,
              args.output_root_hash_file if args.verity else None):

        if f is None:
            continue

        if os.path.exists(f):
            die("Output file " + f + " exists already. (Consider invocation with --force.)")

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

def print_summary(args: CommandLineArguments) -> None:
    sys.stderr.write("DISTRIBUTION:\n")
    sys.stderr.write("          Distribution: " + args.distribution.name + "\n")
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

    if args.distribution in (Distribution.fedora, Distribution.centos, Distribution.mageia):
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

def reuse_cache_tree(args: CommandLineArguments, workspace: str, run_build_script: bool, for_cache: bool, cached: bool) -> bool:
    """If there's a cached version of this tree around, use it and
    initialize our new root directly from it. Returns a boolean indicating
    whether we are now operating on a cached version or not."""

    if cached:
        return True

    if not args.incremental:
        return False
    if for_cache:
        return False
    if args.output_format in RAW_RW_FS_FORMATS:
        return False

    fname = args.cache_pre_dev if run_build_script else args.cache_pre_inst
    if fname is None:
        return False

    with complete_step('Copying in cached tree ' + fname):
        try:
            copy(fname, os.path.join(workspace, "root"))
        except FileNotFoundError:
            return False

    return True

def make_output_dir(args: CommandLineArguments) -> None:
    """Create the output directory if set and not existing yet"""
    if args.output_dir is None:
        return

    mkdir_last(args.output_dir, 0o755)

def make_build_dir(args: CommandLineArguments) -> None:
    """Create the build directory if set and not existing yet"""
    if args.build_dir is None:
        return

    mkdir_last(args.build_dir, 0o755)

def build_image(args: CommandLineArguments, workspace: tempfile.TemporaryDirectory, run_build_script: bool, for_cache: bool=False) -> Tuple[Optional[BinaryIO], Optional[BinaryIO], Optional[str]]:

    # If there's no build script set, there's no point in executing
    # the build script iteration. Let's quit early.
    if args.build_script is None and run_build_script:
        return None, None, None

    make_build_dir(args)

    raw, cached = reuse_cache_image(args, workspace.name, run_build_script, for_cache)
    if for_cache and cached:
        # Found existing cache image, exiting build_image
        return None, None, None

    if not cached:
        raw = create_image(args, workspace.name, for_cache)

    with attach_image_loopback(args, raw) as loopdev:

        prepare_swap(args, loopdev, cached)
        prepare_esp(args, loopdev, cached)

        luks_format_root(args, loopdev, run_build_script, cached)
        luks_format_home(args, loopdev, run_build_script, cached)
        luks_format_srv(args, loopdev, run_build_script, cached)

        with luks_setup_all(args, loopdev, run_build_script) as (encrypted_root, encrypted_home, encrypted_srv):

            prepare_root(args, encrypted_root, cached)
            prepare_home(args, encrypted_home, cached)
            prepare_srv(args, encrypted_srv, cached)

            with mount_image(args, workspace.name, loopdev, encrypted_root, encrypted_home, encrypted_srv):
                prepare_tree(args, workspace.name, run_build_script, cached)

                with mount_cache(args, workspace.name):
                    cached = reuse_cache_tree(args, workspace.name, run_build_script, for_cache, cached)
                    install_skeleton_trees(args, workspace.name, for_cache)
                    install_distribution(args, workspace.name, run_build_script, cached)
                    install_etc_hostname(args, workspace.name)
                    install_boot_loader(args, workspace.name, loopdev, cached)
                    install_extra_trees(args, workspace.name, for_cache)
                    install_build_src(args, workspace.name, run_build_script, for_cache)
                    install_build_dest(args, workspace.name, run_build_script, for_cache)
                    set_root_password(args, workspace.name, run_build_script, for_cache)
                    run_postinst_script(args, workspace.name, run_build_script, for_cache)

                reset_machine_id(workspace.name, run_build_script, for_cache)
                reset_random_seed(workspace.name)
                make_read_only(args, workspace.name, for_cache)

            squashfs = make_squashfs(args, workspace.name, for_cache)
            insert_squashfs(args, raw, loopdev, squashfs, for_cache)

            verity, root_hash = make_verity(args, encrypted_root, run_build_script, for_cache)
            patch_root_uuid(args, loopdev, root_hash, for_cache)
            insert_verity(args, raw, loopdev, verity, root_hash, for_cache)

            # This time we mount read-only, as we already generated
            # the verity data, and hence really shouldn't modify the
            # image anymore.
            with mount_image(args, workspace.name, loopdev, encrypted_root, encrypted_home, encrypted_srv, root_read_only=True):
                install_unified_kernel(args, workspace.name, run_build_script, for_cache, root_hash)
                secure_boot_sign(args, workspace.name, run_build_script, for_cache)

    tar = make_tar(args, workspace.name, run_build_script, for_cache)

    return raw, tar, root_hash

def var_tmp(workspace: str) -> str:
    return mkdir_last(os.path.join(workspace, "var-tmp"))

def run_build_script(args: CommandLineArguments, workspace: str, raw: BinaryIO) -> None:
    if args.build_script is None:
        return

    with complete_step('Running build script'):
        dest = os.path.join(workspace, "dest")
        os.mkdir(dest, 0o755)

        target = "--directory=" + os.path.join(workspace, "root") if raw is None else "--image=" + raw.name

        cmdline = ["systemd-nspawn",
                   '--quiet',
                   target,
                   "--uuid=" + args.machine_id,
                   "--machine=mkosi-" + uuid.uuid4().hex,
                   "--as-pid2",
                   "--register=no",
                   "--bind", dest + ":/root/dest",
                   "--bind=" + var_tmp(workspace) + ":/var/tmp",
                   "--setenv=WITH_DOCS=" + ("1" if args.with_docs else "0"),
                   "--setenv=WITH_TESTS=" + ("1" if args.with_tests else "0"),
                   "--setenv=DESTDIR=/root/dest"]

        if args.build_sources is not None:
            cmdline.append("--setenv=SRCDIR=/root/src")
            cmdline.append("--chdir=/root/src")

            if args.read_only:
                cmdline.append("--overlay=+/root/src::/root/src")
        else:
            cmdline.append("--chdir=/root")

        if args.build_dir is not None:
            cmdline.append("--setenv=BUILDDIR=/root/build")
            cmdline.append("--bind=" + args.build_dir + ":/root/build")

        if args.with_network:
            # If we're using the host network namespace, use the same resolver
            cmdline.append("--bind-ro=/etc/resolv.conf")
        else:
            cmdline.append("--private-network")

        cmdline.append("/root/" + os.path.basename(args.build_script))
        run(cmdline, check=True)

def need_cache_images(args: CommandLineArguments) -> bool:

    if not args.incremental:
        return False

    if args.force_count > 1:
        return True

    return not os.path.exists(args.cache_pre_dev) or not os.path.exists(args.cache_pre_inst)

def remove_artifacts(args: CommandLineArguments, workspace: str, raw: Optional[BinaryIO], tar: Optional[BinaryIO], run_build_script: bool, for_cache: bool=False) -> None:

    if for_cache:
        what = "cache build"
    elif run_build_script:
        what = "development build"
    else:
        return

    if raw is not None:
        with complete_step("Removing disk image from " + what):
            del raw

    if tar is not None:
        with complete_step("Removing tar image from " + what):
            del tar

    with complete_step("Removing artifacts from " + what):
        unlink_try_hard(os.path.join(workspace, "root"))
        unlink_try_hard(os.path.join(workspace, "var-tmp"))

def build_stuff(args: CommandLineArguments) -> None:

    # Let's define a fixed machine ID for all our build-time
    # runs. We'll strip it off the final image, but some build-time
    # tools (dracut...) want a fixed one, hence provide one, and
    # always the same
    args.machine_id = uuid.uuid4().hex

    make_output_dir(args)
    setup_package_cache(args)
    workspace = setup_workspace(args)

    # If caching is requested, then make sure we have cache images around we can make use of
    if need_cache_images(args):

        # There is no point generating a pre-dev cache image if no build script is provided
        if args.build_script:
            # Generate the cache version of the build image, and store it as "cache-pre-dev"
            raw, tar, root_hash = build_image(args, workspace, run_build_script=True, for_cache=True)
            save_cache(args,
                       workspace.name,
                       raw.name if raw is not None else None,
                       args.cache_pre_dev)

            remove_artifacts(args, workspace.name, raw, tar, run_build_script=True)

        # Generate the cache version of the build image, and store it as "cache-pre-inst"
        raw, tar, root_hash = build_image(args, workspace, run_build_script=False, for_cache=True)
        if raw:
            save_cache(args,
                       workspace.name,
                       raw.name,
                       args.cache_pre_inst)
            remove_artifacts(args, workspace.name, raw, tar, run_build_script=False)

    if args.build_script:
        # Run the image builder for the first (develpoment) stage in preparation for the build script
        raw, tar, root_hash = build_image(args, workspace, run_build_script=True)

        run_build_script(args, workspace.name, raw)
        remove_artifacts(args, workspace.name, raw, tar, run_build_script=True)

    # Run the image builder for the second (final) stage
    raw, tar, root_hash = build_image(args, workspace, run_build_script=False)

    raw = qcow2_output(args, raw)
    raw = xz_output(args, raw)
    root_hash_file = write_root_hash_file(args, root_hash)
    settings = copy_nspawn_settings(args)
    checksum = calculate_sha256sum(args, raw, tar, root_hash_file, settings)
    signature = calculate_signature(args, checksum)
    bmap = calculate_bmap(args, raw)

    link_output(args,
                workspace.name,
                raw.name if raw is not None else None,
                tar.name if tar is not None else None)

    link_output_root_hash_file(args, root_hash_file.name if root_hash_file is not None else None)

    link_output_checksum(args,
                         checksum.name if checksum is not None else None)

    link_output_signature(args,
                          signature.name if signature is not None else None)

    link_output_bmap(args,
                     bmap.name if bmap is not None else None)

    link_output_nspawn_settings(args,
                                settings.name if settings is not None else None)

    if root_hash is not None:
        print_step("Root hash is {}.".format(root_hash))

def check_root() -> None:
    if os.getuid() != 0:
        die("Must be invoked as root.")

def run_shell(args: CommandLineArguments) -> None:
    target = "--directory=" + args.output if args.output_format in (OutputFormat.directory, OutputFormat.subvolume) else "--image=" + args.output

    cmdline = ["systemd-nspawn",
               target]

    if args.verb == "boot":
        cmdline += ('--boot',)

    if args.cmdline:
        cmdline += ('--', *args.cmdline)

    os.execvp(cmdline[0], cmdline)

def run_qemu(args: CommandLineArguments) -> None:

    # Look for the right qemu command line to use
    ARCH_BINARIES = { 'x86_64' : 'qemu-system-x86_64',
                      'i386'   : 'qemu-system-i386'}
    arch_binary = ARCH_BINARIES.get(platform.machine(), None)
    for cmdline in ([arch_binary, '-machine', 'accel=kvm'],
                    ['qemu', '-machine', 'accel=kvm'],
                    ['qemu-kvm']):

        if cmdline[0] and shutil.which(cmdline[0]):
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
        # FIRMWARE_LOCATIONS.append('/usr/share/ovmf/x64/OVMF_CODE.fd') # Arch `ovmf` package, but apparently broken
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

def prepend_to_environ_path(paths: List[str]) -> None:
    if not paths:
        return

    original_path = os.getenv("PATH", None)
    new_path = ":".join(paths)

    if original_path is None:
        os.environ["PATH"] = new_path
    else:
        os.environ["PATH"] = new_path + ":" + original_path

def main() -> None:
    args = load_args()

    if args.verb in ("build", "clean", "shell", "boot", "qemu"):
        check_root()
        unlink_output(args)

    if args.verb == "build":
        check_output(args)

    needs_build = args.verb == "build" or (not os.path.exists(args.output) and args.verb in ("shell", "boot", "qemu"))

    if args.verb == "summary" or needs_build:
        print_summary(args)

    prepend_to_environ_path(args.extra_search_paths)

    if needs_build:
        check_root()
        init_namespace(args)
        build_stuff(args)
        print_output_size(args)

    if args.verb in ("shell", "boot"):
        run_shell(args)

    if args.verb == "qemu":
        run_qemu(args)

if __name__ == "__main__":
    main()
