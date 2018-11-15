#!/usr/bin/env python3

from setuptools import find_packages, setup

setup(
    name="testbench",
    version="0.1",
    description="Run tests in many environments",
    url="https://github.com/datawire/testbench",
    maintainer="Luke Shumaker of Datawire",
    maintainer_email="lukeshu@datawire.io",

    packages=find_packages(),
    package_data={
        'testbench_tap.matrix': ['*.html'],
    },
    scripts=['testbench'],
    entry_points={
        'console_scripts': [
            'testbench-tap-matrix = testbench_tap.matrix:main',
            'testbench-tap-run    = testbench_tap.run:main',
        ],
    },
)
