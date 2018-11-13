# SPDX-License-Identifier: LGPL-2.1+
#
# This file is based on sshuttle's ssh.py

import importlib
import pkgutil
from io import BytesIO
from subprocess import run
from typing import BinaryIO, Callable, List

# The complement to serialize_module()/serialize_end() is
# deserialize_all() in remote_inside.py.

def serialize_module(writer: BinaryIO, module_name: str) -> None:
    loader = importlib.find_loader(module_name)
    assert isinstance(loader, importlib.abc.InspectLoader)
    body = loader.get_source(module_name)
    assert body is not None
    writer.write(b'%s\n%d\n%s' % (
        module_name.encode('utf-8'),
        len(body.encode('utf-8')),
        body.encode('utf-8')))

def serialize_end(writer: BinaryIO) -> None:
    writer.write(b'\n')

def run_in_docker(fn: Callable[[], None], module_names: List[str]=[]) -> None:
    stdin = BytesIO()
    for module_name in module_names:
        serialize_module(stdin, module_name)
    serialize_end(stdin)

    pycmd = "%s\n%s.%s()" % (
        pkgutil.get_data(__package__, 'remote_inside.py'),
        fn.__module__,
        fn.__name__)

    run(["docker", "run", "-i", "fedora", "python3", "-c", pycmd],
        stdin=stdin, check=True)
