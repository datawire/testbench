#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1+

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
        'testbench_tap.matrix': ['*.html'],
    },
    scripts=['testbench'],
    entry_points={
        'console_scripts': [
            'testbench-mkosi      = mkosi.main:main',
            'testbench-tap-matrix = testbench_tap.matrix:main',
            'testbench-tap-run    = testbench_tap.run:main',
        ],
    },
)
