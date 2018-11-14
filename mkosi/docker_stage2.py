# SPDX-License-Identifier: LGPL-2.1+
#
# This file is based on sshuttle's assember.py

"""This file is NOT an ordinary Python module.  It never gets
imported.  Instead, it it gets used like:

    python3 -c "$(cat docker_inside.py)\nsomething_else()"

For the purposes of the program it might as well have a .txt file
extension.  The only reason it is named .py is so that my text editor
syntax-hightlights it, and flake8 lints it.

"""

import importlib
import pickle
import sys
from importlib import abc
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import BinaryIO, Dict, Optional, Sequence, Tuple, Union, cast


class StreamImporter(abc.MetaPathFinder, abc.Loader):
    sources: Dict[str, Tuple[bool, str]] = {}

    def __init__(self, reader: BinaryIO):
        self.origin = reader.name

        while True:
            # Read a module from the stream
            name = reader.readline().strip().decode('utf-8')
            if not name:
                return
            is_pkg = reader.readline().strip().decode('utf-8') == 'True'
            nbytes = int(reader.readline().strip().decode('utf-8'))
            body = reader.read(nbytes).decode('utf-8')

            self.sources[name] = (is_pkg, body)

    def find_spec(self, fullname: str, path: Optional[Sequence[Union[bytes, str]]], target: Optional[ModuleType]=None) -> Optional[ModuleSpec]:
        if fullname not in self.sources:
            return None
        is_package, source = self.sources[fullname]
        spec = ModuleSpec(name=fullname, loader=self, origin=self.origin, is_package=is_package)
        spec.has_location = False
        return spec

    def exec_module(self, module: ModuleType) -> None:
        is_package, source = self.sources[module.__name__]
        exec(compile(source, "{}:{}.py".format(self.origin, module.__name__), "exec"), module.__dict__)

def stage2(reader: BinaryIO) -> None:
    # Load modules
    sys.meta_path.insert(0, StreamImporter(reader))
    sys.stderr.flush()
    sys.stdout.flush()
    # Run command
    module = reader.readline().strip().decode('utf-8')
    name = reader.readline().strip().decode('utf-8')
    args = pickle.load(reader)
    importlib.import_module(module).__dict__[name](*args)

# cast() is because we count on stage1 having already switched
# sys.stdin from text-mode to binary-mode.
stage2(cast(BinaryIO, sys.stdin))
