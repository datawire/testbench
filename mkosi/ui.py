# SPDX-License-Identifier: LGPL-2.1+

import contextlib
import sys
from subprocess import CompletedProcess, run
from typing import Any, Iterator, List, NoReturn, Optional, Sequence, Union


def die(message: str, status: int=1) -> NoReturn:
    assert status >= 1 and status < 128
    sys.stderr.write(message + "\n")
    sys.exit(status)

def warn(message: str, *args: Any, **kwargs: Any) -> None:
    sys.stderr.write('WARNING: ' + message.format(*args, **kwargs) + '\n')

def print_step(text: str) -> None:
    sys.stderr.write("‣ \033[0;1;39m" + text + "\033[0m\n")

@contextlib.contextmanager
def complete_step(text: str, text2: Optional[str]=None) -> Iterator[List[Any]]:
    print_step(text + '...')
    args: List[Any] = []
    yield args
    if text2 is None:
        text2 = text + ' complete'
    print_step(text2.format(*args) + '.')

def format_bytes(bytes: int) -> str:
    if bytes >= 1024*1024*1024:
        return "{:0.1f}G".format(bytes / 1024**3)
    if bytes >= 1024*1024:
        return "{:0.1f}M".format(bytes / 1024**2)
    if bytes >= 1024:
        return "{:0.1f}K".format(bytes / 1024)

    return "{}B".format(bytes)

def run_visible(args: Sequence[Union[bytes, str]], **kwargs) -> CompletedProcess:
    sys.stderr.flush()
    sys.stdout.flush()
    return run(args, **kwargs)
