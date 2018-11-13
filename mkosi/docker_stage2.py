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

# Note that (unlike docker.py) this can use 'imp', because we never
# interact with a "real" module loader.  That means the interpreter
# will spit out:
#
#     DeprecationWarning: the imp module is deprecated in favour of importlib; see the module's documentation for alternative uses
#
# But that's OK for now.

import imp
import importlib
import pickle
import sys
from typing import BinaryIO, cast


def deserialize_modules(reader: BinaryIO) -> None:
    """deserialize_modules() is the complement to
    serialize_module()/serialize_end() in docker.py

    """
    while True:
        # Read a module from the stream
        name = reader.readline().strip().decode('utf-8')
        if not name:
            return
        nbytes = int(reader.readline())
        body = reader.read(nbytes).decode('utf-8')

        # And hydrate that module in to the runtime
        print("loading module %s" % repr(name))
        module = imp.new_module(name)
        parents = name.rsplit(".", 1)
        if len(parents) == 2:
            parent, parent_name = parents
            setattr(sys.modules[parent], parent_name, module)
        code = compile(body, name, "exec")
        exec(code, module.__dict__)
        sys.modules[name] = module

def stage2(reader: BinaryIO) -> None:
    # Load modules
    deserialize_modules(reader)
    sys.stderr.flush()
    sys.stdout.flush()
    # Run command
    module = reader.readline().strip().decode('utf-8')
    name = reader.readline().strip().decode('utf-8')
    args = pickle.load(reader)
    importlib.import_module(module).__dict__[name](*args)

# cast() is because we count on stage1 having already switched sys.stdin from
# text-mode to binary-mode.
stage2(cast(BinaryIO, sys.stdin))
