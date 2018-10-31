#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1+

import sys

if sys.version_info < (3, 5):
    sys.exit("Sorry, we need at least Python 3.5.")

from setuptools import setup, find_packages

setup(
    name="mkosi",
    version="4",
    description="Create legacy-free OS images",
    url="https://github.com/systemd/mkosi",
    maintainer="mkosi contributors",
    maintainer_email="systemd-devel@lists.freedesktop.org",
    license="LGPLv2+",

    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'mkosi = mkosi:main',
        ],
    },
)
