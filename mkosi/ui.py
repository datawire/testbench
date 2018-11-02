# SPDX-License-Identifier: LGPL-2.1+

import contextlib
import sys
from typing import Any, Iterator, List, NoReturn, Optional


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
