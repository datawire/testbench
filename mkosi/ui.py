# SPDX-License-Identifier: LGPL-2.1+

import sys
from typing import Any, NoReturn


def die(message: str, status: int=1) -> NoReturn:
    assert status >= 1 and status < 128
    sys.stderr.write(message + "\n")
    sys.exit(status)

def warn(message: str, *args: Any, **kwargs: Any) -> None:
    sys.stderr.write('WARNING: ' + message.format(*args, **kwargs) + '\n')
