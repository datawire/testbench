# SPDX-License-Identifier: LGPL-2.1+
#
# This file is based on sshuttle's ssh.py

# Note that this module strictly uses modern 'importlib' (not 'imp')
# because it needs to work as a zipapp or in any other packaging
# mechanism.

import importlib
import pickle
import pkgutil
from io import BytesIO
from subprocess import run
from types import ModuleType
from typing import Any, BinaryIO, Callable, List, Optional

# The complement to serialize_module()/serialize_end() is the parser
# in StreamImporter() in docker_stage2.py.

def serialize_module(writer: BinaryIO, module_name: str) -> None:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        raise RuntimeError('Unknown module "%s".' % module_name)
    assert isinstance(spec.loader, importlib.abc.InspectLoader)

    body = spec.loader.get_source(module_name)
    assert body is not None
    encoded_body = body.encode('utf-8')

    writer.write(b'%s\n%s\n%d\n%s' % (
        module_name.encode('utf-8'),  # name
        (b'False' if spec.submodule_search_locations is None else b'True'),  # is_package
        len(encoded_body),  # len(body)
        encoded_body))  # body

def serialize_end(writer: BinaryIO) -> None:
    writer.write(b'\n')

def walk_package(package: ModuleType) -> List[str]:
    # Assert that it is a package
    assert hasattr(package, '__path__')

    # List the children of the package
    members = [modinfo.name for modinfo in pkgutil.walk_packages(package.__path__, package.__name__+'.') if "docker_stage" not in modinfo.name]

    return [package.__name__] + members

def run_in_docker(fn: Callable[..., None], args: List[Any]=[], docker_args: List[str]=[]) -> None:

    # We do this in multiple stages because: The first stage (whether
    # or not there are more after it) is sent over argv, which means
    # that it is likely to appear in-its-entirety in debug messages
    # and such, so we really want it to be short.

    # Stage 2:
    #  1. Read a series of serialized modules from stdin
    #  2. Read the function's __module__/__name__/arguments from stdin
    #  3. `from __module__ import __name__` and call it
    _stage2: Optional[bytes] = pkgutil.get_data(__package__, 'docker_stage2.py')
    assert _stage2 is not None
    stage2: bytes = _stage2

    # Stage 1: But stage2 is kinda big, so we don't want to send it over argv:
    #  1. Read stage2 from stdin and execute it
    stage1: bytes = ("import os, sys; sys.stdin = os.fdopen(0, 'rb'); exec(compile(sys.stdin.read(%d), 'docker_stage2.py', 'exec'))" % len(stage2)).encode("utf-8")

    stdin = BytesIO()
    # Send stage2 for stage1 to read
    stdin.write(stage2)
    # Send modules for stage2 to read
    for module_name in walk_package(importlib.import_module(__package__)):
        serialize_module(stdin, module_name)
    serialize_end(stdin)
    # Send function's __module__/__name__/arguments for stage2 to read
    stdin.write(("%s\n%s\n" % (fn.__module__, fn.__name__)).encode("utf-8"))
    pickle.dump(args, stdin)

    run(["docker", "run", "--interactive"] + docker_args + ["gcr.io/datawireio/testbench-mkosi", "python3", "-c", stage1],
        input=stdin.getvalue(), check=True)
