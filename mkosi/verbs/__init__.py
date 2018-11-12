# SPDX-License-Identifier: LGPL-2.1+

import importlib
import pkgutil
from typing import List, cast

from ..types import CommandLineArguments


class Verb:  # Inherit from typing.Protocol, once it's available
    NEEDS_BUILD: bool
    NEEDS_ROOT: bool

    @staticmethod
    def do(args: CommandLineArguments) -> None:
        ...

def get_verb(verbname: str) -> Verb:
    try:
        return cast(Verb, importlib.import_module(__package__ + '.' + verbname))
    except ImportError:
        raise RuntimeError('Unknown verb "%s".' % verbname)

def list_verbs() -> List[str]:
    return [name for _, name, _ in pkgutil.iter_modules(__path__)]
