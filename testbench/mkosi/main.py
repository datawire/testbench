# SPDX-License-Identifier: LGPL-2.1+

import os
import sys
from typing import List

from . import verbs
from .cli import load_args
from .types import CommandLineArguments
from .ui import complete_step
from .utils import check_root, empty_directory, unlink_try_hard

if sys.version_info < (3, 6):
    sys.exit("Sorry, we need at least Python 3.6.")

# TODO
# - volatile images
# - work on device nodes
# - allow passing env vars

def unlink_output(args: CommandLineArguments) -> None:
    if not args.force and args.verb != "clean":
        return

    with complete_step('Removing output files'):
        unlink_try_hard(args.output)

        if args.checksum:
            unlink_try_hard(args.output_checksum)

        if args.verity:
            unlink_try_hard(args.output_root_hash_file)

        if args.sign:
            unlink_try_hard(args.output_signature)

        if args.bmap:
            unlink_try_hard(args.output_bmap)

        if args.nspawn_settings is not None:
            unlink_try_hard(args.output_nspawn_settings)

    # We remove any cached images if either the user used --force
    # twice, or he/she called "clean" with it passed once. Let's also
    # remove the downloaded package cache if the user specified one
    # additional "--force".

    if args.verb == "clean":
        remove_build_cache = args.force_count > 0
        remove_package_cache = args.force_count > 1
    else:
        remove_build_cache = args.force_count > 1
        remove_package_cache = args.force_count > 2

    if remove_build_cache:
        if args.cache_pre_dev is not None or args.cache_pre_inst is not None:
            with complete_step('Removing incremental cache files'):
                if args.cache_pre_dev is not None:
                    unlink_try_hard(args.cache_pre_dev)

                if args.cache_pre_inst is not None:
                    unlink_try_hard(args.cache_pre_inst)

        if args.build_dir is not None:
            with complete_step('Clearing out build directory'):
                empty_directory(args.build_dir)

    if remove_package_cache:
        if args.cache_path is not None:
            with complete_step('Clearing out package cache'):
                empty_directory(args.cache_path)

def prepend_to_environ_path(paths: List[str]) -> None:
    if not paths:
        return

    original_path = os.getenv("PATH", None)
    new_path = ":".join(paths)

    if original_path is None:
        os.environ["PATH"] = new_path
    else:
        os.environ["PATH"] = new_path + ":" + original_path

def main() -> None:
    args = load_args()
    verb = verbs.get_verb(args.verb)

    if verb.NEEDS_ROOT:
        check_root()
    if verb.FORCE_UNLINKS:
        unlink_output(args)

    prepend_to_environ_path(args.extra_search_paths)

    if not os.path.exists(args.output) and verb.NEEDS_BUILD:
        verbs.get_verb("build").do(args)

    verb.do(args)
