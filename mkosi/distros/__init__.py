# SPDX-License-Identifier: LGPL-2.1+

import importlib
import pkgutil
from typing import List, Optional, cast

from ..cli import CommandLineArguments


class Distribution:  # Inherit from typing.Protocol, once it's available
    PKG_CACHE: List[str]
    DEFAULT_RELEASE: Optional[str]
    DEFAULT_MIRROR: Optional[str]

    @staticmethod
    def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
        ...

    @staticmethod
    def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
        ...

def get_distro(distroname: str) -> Distribution:
    try:
        return cast(Distribution, importlib.import_module(__package__ + '.' + distroname))
    except ImportError:
        raise RuntimeError('Unknown distro "%s".' % distroname)

def list_distros() -> List[str]:
    return [name for _, name, _ in pkgutil.iter_modules(__path__)]
