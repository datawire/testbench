# SPDX-License-Identifier: LGPL-2.1+

from . import shell

NEEDS_ROOT = True
NEEDS_BUILD = True
HAS_ARGS = True
FORCE_UNLINKS = True

do = shell.do
