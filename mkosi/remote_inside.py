# SPDX-License-Identifier: LGPL-2.1+
#
# This file is based on sshuttle's assember.py and ssh.py

"""This file is NOT an ordinary Python module.  It never gets
imported.  Instead, it it gets used like:

    python3 -c "$(cat remote_inside.py)\nsomething_else()"

For the purposes of the program it might as well have a .txt file
extension.  The only reason it is named .py is so that my text editor
syntax-hightlights it, and flake8 lints it.

"""

import imp
import os
import sys
from typing import BinaryIO


def deserialize_all(reader: BinaryIO) -> None:
    """deserialize_all() is the complement to
    serialize_module()/serialize_end() in remote.py"""
    while True:
        # Read a module from the stream
        name = reader.readline().strip().decode('utf-8')
        if not name:
            return
        nbytes = int(reader.readline())
        body = reader.read(nbytes).decode('utf-8')

        # And hydrate that module in to the runtime
        module = imp.new_module(name)
        parents = name.rsplit(".", 1)
        if len(parents) == 2:
            parent, parent_name = parents
            setattr(sys.modules[parent], parent_name, module)
        code = compile(body, name, "exec")
        exec(code, module.__dict__)
        sys.modules[name] = module

if __name__ == "__main__":
    deserialize_all(os.fdopen(0, "rb"))  # sys.stdin is in text-mode, we need binary-mode
    sys.stderr.flush()
    sys.stdout.flush()
