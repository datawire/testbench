#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1+

import sys

from setuptools import find_packages, setup

if sys.version_info < (3, 5):
    sys.exit("Sorry, we need at least Python 3.5.")

setup(
    name="mkosi",
    version="4",
    description="Create OS images (Datawire fork)",
    url="https://github.com/datawire/testbench-mkosi",
    maintainer="Luke Shumaker of Datawire",
    maintainer_email="lukeshu@datawire.io",
    license="LGPLv2+",

    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'testbench-mkosi = mkosi:main',
        ],
    },
)
