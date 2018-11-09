from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    NamedTuple,
    Optional,
    TextIO,
    Tuple,
)


def peek_line(reader: TextIO) -> str:
    pos = reader.tell()
    line = reader.readline()
    reader.seek(pos)
    return line


def trim_prefix(s: str, prefix: str) -> str:
    if s.startswith(prefix):
        return s[len(prefix):]
    return s


class TestStatus(Enum):
    OK = 1
    NOT_OK = 2
    TODO_OK = 3
    TODO_NOT_OK = 4
    SKIP = 5
    MISSING = 6


class TestCase(NamedTuple):
    status: TestStatus
    n: int
    description: Optional[str] = None
    comment: Optional[str] = None
    yaml: Optional[Any] = None


def parse(reader: TextIO) -> Tuple[Dict[int, TestCase], List[str]]:
    errs: List[str] = []

    def error(msg: str) -> None:
        # Hard-code the line number as "1"
        errs.append("%s:1: Invalid TAP: %s" % (reader.name, msg))

    # Peek at the first line to decide the TAP version
    ver = 12
    first = peek_line(reader).rstrip("\n")
    if first.startswith("TAP version "):
        strver = trim_prefix(first, "TAP version ")
        if not strver.isdigit():
            error("Not an integer version: %s" % repr(strver))
            return ({}, errs)
        ver = int(strver)
        if ver < 13:
            error("It is illegal to specify a TAP version < 13, got: %d" % ver)
            return ({}, errs)

    # Call the appropriate parser for that version
    from . import tap12, tap13
    parsers: Dict[int, Callable[[TextIO], Tuple[Dict[int, TestCase], List[str]]]] = {
        12: tap12.parse,
        13: tap13.parse,
    }
    if ver not in parsers:
        error("I don't know how to parse TAP version %s" % ver)
        return ({}, errs)
    return parsers[ver](reader)
