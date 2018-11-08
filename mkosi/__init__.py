# SPDX-License-Identifier: LGPL-2.1+

import collections
import contextlib
import crypt
import ctypes
import ctypes.util
import errno
import fcntl
import hashlib
import os
import platform
import shlex
import shutil
import stat
import sys
import tempfile
import uuid
from subprocess import DEVNULL, PIPE, run
from typing import (
    BinaryIO,
    Iterable,
    Iterator,
    List,
    Optional,
    TextIO,
    Tuple,
    cast,
)

from . import distros
from .cli import CommandLineArguments, load_args
from .disk import ensured_partition
from .luks import (
    luks_close,
    luks_format_home,
    luks_format_root,
    luks_format_srv,
    luks_setup_all,
    luks_setup_root,
)
from .types import RAW_FORMATS, RAW_RW_FS_FORMATS, OutputFormat
from .ui import complete_step, die, print_step
from .utils import (
    mkdir_last,
    mount_bind,
    patch_file,
    run_build_script,
    run_workspace_command,
    umount,
)

if sys.version_info < (3, 5):
    sys.exit("Sorry, we need at least Python 3.5.")

# TODO
# - volatile images
# - work on device nodes
# - allow passing env vars

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
    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        die("Could not find libc")
    libc = ctypes.CDLL(libc_name, use_errno=True)

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

def print_running_cmd(cmdline: Iterable[str]) -> None:
    sys.stderr.write("‣ \033[0;1;39mRunning command:\033[0m\n")
    sys.stderr.write(" ".join(shlex.quote(x) for x in cmdline) + "\n")

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

def prepare_swap(args: CommandLineArguments, loopdev: Optional[str], cached: bool) -> None:
    if loopdev is None:
        return
    if cached:
        return
    if args.swap_partno is None:
        return

    with complete_step('Formatting swap partition'):
        run(["mkswap", "-Lswap", ensured_partition(loopdev, args.swap_partno)], check=True)

def prepare_esp(args: CommandLineArguments, loopdev: Optional[str], cached: bool) -> None:
    if loopdev is None:
        return
    if cached:
        return
    if args.esp_partno is None:
        return

    with complete_step('Formatting ESP partition'):
        run(["mkfs.fat", "-nEFI", "-F32", ensured_partition(loopdev, args.esp_partno)], check=True)

def mkfs_ext4(label: str, mount: str, dev: str) -> None:
    run(["mkfs.ext4", "-L", label, "-M", mount, dev], check=True)

def mkfs_btrfs(label: str, dev: str) -> None:
    run(["mkfs.btrfs", "-L", label, "-d", "single", "-m", "single", dev], check=True)

def mkfs_xfs(label: str, dev: str) -> None:
    run(["mkfs.xfs", "-n", "ftype=1", "-L", label, dev], check=True)

def prepare_root(args: CommandLineArguments, dev: Optional[str], cached: bool) -> None:
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

def prepare_home(args: CommandLineArguments, dev: Optional[str], cached: bool) -> None:
    if dev is None:
        return
    if cached:
        return

    with complete_step('Formatting home partition'):
        mkfs_ext4("home", "/home", dev)

def prepare_srv(args: CommandLineArguments, dev: Optional[str], cached: bool) -> None:
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

def mount_tmpfs(where: str) -> None:
    os.makedirs(where, 0o755, True)
    run(["mount", "tmpfs", "-t", "tmpfs", where], check=True)

@contextlib.contextmanager
def mount_image(args: CommandLineArguments, workspace: str, loopdev: Optional[str], root_dev: Optional[str], home_dev: Optional[str], srv_dev: Optional[str], root_read_only: bool=False) -> Iterator[None]:
    if loopdev is None or root_dev is None:
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
            mount_loop(args, ensured_partition(loopdev, args.esp_partno), os.path.join(root, "efi"))

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
def mount_cache(args: CommandLineArguments, workspace: str) -> Iterator[None]:

    if args.cache_path is None:
        yield
        return

    # We can't do this in mount_image() yet, as /var itself might have to be created as a subvolume first
    with complete_step('Mounting Package Cache'):
        cachedirs = distros.get_distro(args.distribution).PKG_CACHE
        if len(cachedirs) == 1:
            mount_bind(args.cache_path, os.path.join(workspace, "root", cachedirs[0]))
        else:
            for cachedir in cachedirs:
                mount_bind(os.path.join(args.cache_path, os.path.basename(cachedir)), os.path.join(workspace, "root", cachedir))
    try:
        yield
    finally:
        with complete_step('Unmounting Package Cache'):
            for d in distros.get_distro(args.distribution).PKG_CACHE:
                umount(os.path.join(workspace, "root", d))

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

def install_distribution(args: CommandLineArguments, workspace: str, run_build_script: bool, cached: bool) -> None:

    if cached:
        return

    distros.get_distro(args.distribution).install(args, workspace, run_build_script)

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

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str], cached: bool) -> None:
    if not args.bootable:
        return

    if cached:
        return

    with complete_step("Installing boot loader"):
        shutil.copyfile(os.path.join(workspace, "root", "usr/lib/systemd/boot/efi/systemd-bootx64.efi"),
                        os.path.join(workspace, "root", "boot/efi/EFI/systemd/systemd-bootx64.efi"))

        shutil.copyfile(os.path.join(workspace, "root", "usr/lib/systemd/boot/efi/systemd-bootx64.efi"),
                        os.path.join(workspace, "root", "boot/efi/EFI/BOOT/bootx64.efi"))

        distros.get_distro(args.distribution).install_boot_loader(args, workspace, loopdev)

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

def make_squashfs(args: CommandLineArguments, workspace: str) -> BinaryIO:
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
        run(["dd", "if=" + blob.name, "of=" + (dev if dev is not None else ensured_partition(loopdev, partno))], check=True)
    finally:
        luks_close(dev, "Closing LUKS root partition")

    args.ran_sfdisk = True

    return blob_size

def insert_squashfs(args: CommandLineArguments, raw: BinaryIO, loopdev: str, squashfs: BinaryIO) -> None:
    with complete_step('Inserting squashfs root partition'):
        args.root_size = insert_partition(args, raw, loopdev, args.root_partno, squashfs,
                                          "Root Partition", gpt_root_native().root)

def make_verity(args: CommandLineArguments, dev: str) -> Tuple[BinaryIO, str]:

    with complete_step('Generating verity hashes'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(dir=os.path.dirname(args.output), prefix=".mkosi-"))
        c = run(["veritysetup", "format", dev, f.name], stdout=PIPE, check=True)

        for line in c.stdout.decode("utf-8").split('\n'):
            if line.startswith("Root hash:"):
                root_hash = line[10:].strip()
                return f, root_hash

        raise ValueError('Root hash not found')

def insert_verity(args: CommandLineArguments, raw: BinaryIO, loopdev: str, verity: BinaryIO, root_hash: str) -> None:

    # Use the final 128 bit of the root hash as partition UUID of the verity partition
    u = uuid.UUID(root_hash[-32:])

    with complete_step('Inserting verity partition'):
        insert_partition(args, raw, loopdev, args.verity_partno, verity,
                         "Verity Partition", gpt_root_native().verity, u)

def patch_root_uuid(args: CommandLineArguments, loopdev: str, root_hash: str) -> None:

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

    if args.distribution not in ('fedora', 'mageia'):  # FIXME: don't hard-code distro-specific details
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

def xz_output(args: CommandLineArguments, raw: Optional[BinaryIO]) -> Optional[BinaryIO]:
    if args.output_format not in RAW_FORMATS:
        return raw

    if not args.xz:
        return raw

    assert raw is not None

    xz_binary = "pxz" if shutil.which("pxz") else "xz"

    with complete_step('Compressing image file'):
        f: BinaryIO = cast(BinaryIO, tempfile.NamedTemporaryFile(prefix=".mkosi-", dir=os.path.dirname(args.output)))
        run([xz_binary, "-c", raw.name], stdout=f, check=True)

    return f

def qcow2_output(args: CommandLineArguments, raw: Optional[BinaryIO]) -> Optional[BinaryIO]:
    if args.output_format not in RAW_FORMATS:
        return raw

    if not args.qcow2:
        return raw

    assert raw is not None

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

def calculate_sha256sum(args: CommandLineArguments, raw: Optional[BinaryIO], tar: Optional[BinaryIO], root_hash_file: Optional[BinaryIO], nspawn_settings: Optional[BinaryIO]) -> Optional[TextIO]:
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

def calculate_bmap(args: CommandLineArguments, raw: Optional[BinaryIO]) -> Optional[TextIO]:
    if not args.bmap:
        return None

    if args.output_format not in RAW_RW_FS_FORMATS:
        return None

    assert raw is not None

    with complete_step('Creating BMAP file'):
        f: TextIO = cast(TextIO, tempfile.NamedTemporaryFile(mode="w+", prefix=".mkosi-", encoding="utf-8",
                                                             dir=os.path.dirname(args.output_bmap)))

        cmdline = ["bmaptool", "create", raw.name]
        run(cmdline, stdout=f, check=True)

    return f

def save_cache(args: CommandLineArguments, workspace: str, raw: Optional[str], cache_path: str) -> None:

    if cache_path is None or raw is None:
        return

    with complete_step('Installing cache copy ',
                       'Successfully installed cache copy ' + cache_path):

        if args.output_format in RAW_RW_FS_FORMATS:
            os.chmod(raw, 0o666 & ~args.original_umask)
            shutil.move(raw, cache_path)
        else:
            shutil.move(os.path.join(workspace, "root"), cache_path)

def link_output(args: CommandLineArguments, workspace: str, raw: Optional[str], tar: Optional[str]) -> None:
    with complete_step('Linking image file',
                       'Successfully linked ' + args.output):
        if args.output_format in (OutputFormat.directory, OutputFormat.subvolume):
            os.rename(os.path.join(workspace, "root"), args.output)
        elif args.output_format in RAW_FORMATS:
            assert raw is not None
            os.chmod(raw, 0o666 & ~args.original_umask)
            os.link(raw, args.output)
        else:
            assert tar is not None
            os.chmod(tar, 0o666 & ~args.original_umask)
            os.link(tar, args.output)

def link_output_nspawn_settings(args: CommandLineArguments, path: Optional[str]) -> None:
    if path is None:
        return

    with complete_step('Linking nspawn settings file',
                       'Successfully linked ' + args.output_nspawn_settings):
        os.chmod(path, 0o666 & ~args.original_umask)
        os.link(path, args.output_nspawn_settings)

def link_output_checksum(args: CommandLineArguments, checksum: Optional[str]) -> None:
    if checksum is None:
        return

    with complete_step('Linking SHA256SUMS file',
                       'Successfully linked ' + args.output_checksum):
        os.chmod(checksum, 0o666 & ~args.original_umask)
        os.link(checksum, args.output_checksum)

def link_output_root_hash_file(args: CommandLineArguments, root_hash_file: Optional[str]) -> None:
    if root_hash_file is None:
        return

    with complete_step('Linking .roothash file',
                       'Successfully linked ' + args.output_root_hash_file):
        os.chmod(root_hash_file, 0o666 & ~args.original_umask)
        os.link(root_hash_file, args.output_root_hash_file)

def link_output_signature(args: CommandLineArguments, signature: Optional[str]) -> None:
    if signature is None:
        return

    with complete_step('Linking SHA256SUMS.gpg file',
                       'Successfully linked ' + args.output_signature):
        os.chmod(signature, 0o666 & ~args.original_umask)
        os.link(signature, args.output_signature)

def link_output_bmap(args: CommandLineArguments, bmap: Optional[str]) -> None:
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
    d: Optional[tempfile.TemporaryDirectory] = None
    with complete_step('Setting up package cache',
                       'Setting up package cache {} complete') as output:
        if args.cache_path is None:
            d = tempfile.TemporaryDirectory(dir=os.path.dirname(args.output), prefix=".mkosi-")
            args.cache_path = d.name
        else:
            os.makedirs(args.cache_path, 0o755, exist_ok=True)
        output.append(args.cache_path)

    return d

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

        if loopdev is not None:
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

            root_hash: Optional[str] = None
            if not for_cache:
                if args.output_format == OutputFormat.raw_squashfs and not for_cache:
                    # args.output_format == OutputFormat.raw_*
                    # -> implies: raw is not None
                    # -> implies: loopdev is not None
                    assert raw is not None
                    assert loopdev is not None
                    insert_squashfs(args, raw, loopdev, make_squashfs(args, workspace.name))

                if args.verity and not run_build_script:
                    # ???
                    # -> implies: raw is not None
                    # -> implies: loopdev is not None
                    # -> implies: encrypted_root is not None
                    assert raw is not None
                    assert loopdev is not None
                    assert encrypted_root is not None
                    verity, root_hash = make_verity(args, encrypted_root)
                    patch_root_uuid(args, loopdev, root_hash)
                    insert_verity(args, raw, loopdev, verity, root_hash)

            # This time we mount read-only, as we already generated
            # the verity data, and hence really shouldn't modify the
            # image anymore.
            with mount_image(args, workspace.name, loopdev, encrypted_root, encrypted_home, encrypted_srv, root_read_only=True):
                install_unified_kernel(args, workspace.name, run_build_script, for_cache, root_hash)
                secure_boot_sign(args, workspace.name, run_build_script, for_cache)

    tar = make_tar(args, workspace.name, run_build_script, for_cache)

    return raw, tar, root_hash

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

def run_withmount(args: CommandLineArguments) -> None:
    determine_partition_table(args)
    workspace = setup_workspace(args)
    with open(args.output, 'rb') as raw:
        with attach_image_loopback(args, raw) as loopdev:
            with luks_setup_all(args, loopdev, False) as (encrypted_root, encrypted_home, encrypted_srv):
                with mount_image(args, workspace.name, loopdev, encrypted_root, encrypted_home, encrypted_srv):
                    run(args.cmdline, cwd=os.path.join(workspace.name, "root"), check=True)

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

    if args.verb not in ("help", "summary"):
        check_root()
        unlink_output(args)

    if args.verb == "build":
        check_output(args)

    needs_build = args.verb == "build" or (not os.path.exists(args.output) and args.verb in ("shell", "boot", "qemu", "withmount"))

    if args.verb == "summary" or needs_build:
        print_summary(args)

    prepend_to_environ_path(args.extra_search_paths)

    if needs_build or args.verb == "withmount":
        check_root()
        init_namespace(args)
    if needs_build:
        build_stuff(args)
        print_output_size(args)

    if args.verb in ("shell", "boot"):
        run_shell(args)

    if args.verb == "qemu":
        run_qemu(args)

    if args.verb == "withmount":
        run_withmount(args)

if __name__ == "__main__":
    main()
