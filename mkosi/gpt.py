import platform
import string
import uuid
from subprocess import PIPE, run
from typing import Dict, List, NamedTuple, Optional, Tuple

from .ui import die

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

# 1 MB at the beginning of the disk for the GPT disk label, and
# another MB at the end (this is actually more than needed.)
GPT_HEADER_SIZE = 1024*1024
GPT_FOOTER_SIZE = 1024*1024

class GPTRootTypePair(NamedTuple):
    root: uuid.UUID
    verity: uuid.UUID

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


def sfdisk_quote_char(c: int) -> str:
    # Hex-escape non-(printable-ASCII) bytes, as well as (dquote,
    # backtick, backslash, dollar).  IDK why (backtick, dollar), but
    # that's what util-linux:carefulputc.h:fputs_quoted() does.
    if (c in (0x22, 0x5c, 0x60, 0x24)) or (c < 0x20 or c > 0x7e):
        return "\\x%02x" % c
    return chr(c)


def sfdisk_quote(s: str) -> str:
    b = s.encode("utf-8")
    chars = [sfdisk_quote_char(c) for c in b]
    return '"' + (''.join(chars)) + '"'


def sfdisk_unquote(s: str) -> str:
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        ret = ""
        rest = s[1:-1]
        while len(rest) > 0:
            if (
                    rest[0] == "\\" and len(rest) >= 4 and
                    rest[1] == "x" and
                    rest[2] in string.hexdigits and
                    rest[3] in string.hexdigits):
                ret += chr(int(rest[2:4], 16))
                rest = rest[4:]
            else:
                ret += rest[0]
                rest = rest[1:]
    return s


class Partition(NamedTuple):
    p_start: Optional[int] = None
    p_size: Optional[int] = None
    p_type: Optional[uuid.UUID] = None
    p_uuid: Optional[uuid.UUID] = None
    p_name: Optional[str] = None
    p_attrs: Optional[str] = None
    p_bootable: bool = False

    def __str__(self) -> str:
        fields: List[str] = []
        if self.p_start is not None:
            fields.append("start={}".format(self.p_start))
        if self.p_size is not None:
            fields.append("size={}".format(self.p_size))
        if self.p_type is not None:
            fields.append("type={}".format(self.p_type))
        if self.p_uuid is not None:
            fields.append("uuid={}".format(self.p_uuid))
        if self.p_name is not None:
            fields.append("name={}".format(sfdisk_quote(self.p_name)))
        if self.p_attrs is not None:
            fields.append("attrs={}".format(self.p_attrs))
        if self.p_bootable:
            fields.append("bootable")
        return ", ".join(fields)


def read_partition_table(devpath: str) -> Tuple[Dict[str, Partition], int]:
    """Return a dict of the partitions in the GTP volume at devpath, and
    the location of the last allocated sector"""

    table: Dict[str, Partition] = {}
    last_sector = 0

    c = run(["sfdisk", "--dump", devpath], stdout=PIPE, check=True)
    in_body = False
    for line in c.stdout.decode("utf-8").split('\n'):
        stripped = line.strip()

        if stripped == "":  # empty line is where the body begins
            in_body = True
            continue
        if not in_body:
            continue

        name, rest = stripped.split(":", 1)
        # BUG: this won't correctly handle a comma inside of a quoted name= field
        fields = rest.split(",")

        partition = Partition()
        for field in fields:
            f = field.strip()

            p_start: Optional[int] = None
            p_size: Optional[int] = None
            p_type: Optional[uuid.UUID] = None
            p_uuid: Optional[uuid.UUID] = None
            p_name: Optional[str] = None
            p_attrs: Optional[str] = None
            p_bootable: bool = False

            if f.startswith("start="):
                p_start = int(f[6:])
            if f.startswith("size="):
                p_size = int(f[5:])
            if f.startswith("type="):
                p_type = uuid.UUID(f[5:])
            if f.startswith("uuid="):
                p_uuid = uuid.UUID(f[5:])
            if f.startswith("name="):
                p_name = sfdisk_unquote(f[5:])
            if f.startswith("attrs="):
                p_attrs = f[6:]
            if f == "bootable":
                p_bootable = True

        table[name] = Partition(p_start=p_start,
                                p_size=p_size,
                                p_type=p_type,
                                p_uuid=p_uuid,
                                p_name=p_name,
                                p_attrs=p_attrs,
                                p_bootable=p_bootable)

        if partition.p_start is not None and partition.p_size is not None:
            end = partition.p_start + partition.p_size
            if end > last_sector:
                last_sector = end

    return table, last_sector * 512

def write_partition_table(devpath: str, table: Dict[str, Partition]) -> None:

    txt = "label: gpt\n"
    for partition in table.values():
        txt += str(partition) + "\n"

    run(["sfdisk", "--color=never", devpath], input=txt.encode("utf-8"), check=True)
    run(["sync"])
