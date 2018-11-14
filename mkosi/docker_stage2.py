# SPDX-License-Identifier: LGPL-2.1+
#
# This file is based on sshuttle's assember.py

"""This file is NOT an ordinary Python module.  It never gets
imported.  Instead, it it gets used more like:

    python3 -c "$(cat docker_inside.py)"

For the purposes of the program it might as well have a .txt file
extension.  The only reason it is named .py is so that my text editor
syntax-hightlights it, and flake8 lints it.

"""

import importlib
import pickle
import sys
from importlib import abc
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType
from typing import BinaryIO, Dict, Optional, Sequence, Tuple, Union, cast


class StreamImporter(abc.MetaPathFinder, abc.InspectLoader):
    # Gotchas:
    #
    # - Anything using __path__ will not work
    #   (e.g. `pkgutil.iter_modules()`). That's OK, because we only
    #   use that functionality for for
    #   distros.list_distros()/verbs.list_verbs(), which should happen
    #   outside of Docker.
    #
    # - Does not implement ResourceLoader (PEP 302 `.get_data()`).
    #   Note that ResourceLoader is deprecated in Python 3.7 anyway,
    #   in favor of ResourceReader (which we don't implement either).
    #
    # Other than that, ths should be pretty robust to any weird
    # scenarios you throw at it.

    sources: Dict[str, Tuple[bool, str]] = {}

    def __init__(self, reader: BinaryIO):
        self.origin = reader.name

        # The following parser is the complement to
        # serialize_module()/serialize_end() in docker.py.
        while True:
            # Read a module from the stream
            name = reader.readline().strip().decode('utf-8')
            if not name:
                return
            is_pkg = reader.readline().strip().decode('utf-8') == 'True'
            nbytes = int(reader.readline().strip().decode('utf-8'))
            body = reader.read(nbytes).decode('utf-8')
            # And save it to self.sources, for later evaluation at
            # import-time
            self.sources[name] = (is_pkg, body)

    def find_spec(self, fullname: str, path: Optional[Sequence[Union[bytes, str]]], target: Optional[ModuleType]=None) -> Optional[ModuleSpec]:
        if fullname not in self.sources:
            return None
        is_package, _ = self.sources[fullname]
        spec = ModuleSpec(name=fullname, loader=self, origin=self.origin, is_package=is_package)
        spec.has_location = False
        return spec

    def get_source(self, fullname: str) -> Optional[str]:
        if fullname not in self.sources:
            return None
        _, source = self.sources[fullname]
        return source

    def get_code(self, fullname: str) -> Optional[CodeType]:
        # Copied from the default InspectLoader.get_code(), but adds
        # the optional 2nd argument to .source_to_code().  Overriding
        # this isn't nescessary to function, but it's nice for
        # debugging.
        source = self.get_source(fullname)
        if source is None:
            return None
        return self.source_to_code(source, "{}:{}.py".format(self.origin, fullname))

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
