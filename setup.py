#!/usr/bin/env python3

import sys

from setuptools import find_packages, setup

if sys.version_info < (3, 5):
    sys.exit("Sorry, we need at least Python 3.5.")

setup(
    name="testbench",
    version="0.1",
    description="Run tests in many environments",
    url="https://github.com/datawire/testbench",
    maintainer="Luke Shumaker of Datawire",
    maintainer_email="lukeshu@datawire.io",
    license=["LGPLv2+", "proprietary"],

    packages=find_packages(),
    package_data={
        'testbench.tap.matrix': ['*.html'],
    },
    scripts=[
        'bin/testbench',
        'bin/testbench-mkosi',
        'bin/testbench-tap-matrix',
        'bin/testbench-tap-run',
    ],
)
