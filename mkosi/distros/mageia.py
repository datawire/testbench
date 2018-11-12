# SPDX-License-Identifier: LGPL-2.1+

import os
from typing import Optional

from ..rpm import disable_kernel_install, invoke_dnf, reenable_kernel_install
from ..types import CommandLineArguments
from ..ui import complete_step

PKG_CACHE = ['var/cache/dnf']
DEFAULT_RELEASE = '6'
DEFAULT_MIRROR = None

@complete_step('Installing Mageia')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:

    masked = disable_kernel_install(args, workspace)

    # Mageia does not (yet) have RPM GPG key on the web
    gpg_key = '/etc/pki/rpm-gpg/RPM-GPG-KEY-Mageia'
    if os.path.exists(gpg_key):
        gpg_key = "file://%s" % gpg_key
#    else:
#        gpg_key = "https://getfedora.org/static/%s.txt" % FEDORA_KEYS_MAP[args.release]

    if args.mirror:
        baseurl = "{args.mirror}/distrib/{args.release}/x86_64/media/core/".format(args=args)
        release_url = "baseurl=%s/release/" % baseurl
        updates_url = "baseurl=%s/updates/" % baseurl
    else:
        baseurl = "https://www.mageia.org/mirrorlist/?release={args.release}&arch=x86_64&section=core".format(args=args)
        release_url = "mirrorlist=%s&repo=release" % baseurl
        updates_url = "mirrorlist=%s&repo=updates" % baseurl

    config_file = os.path.join(workspace, "dnf.conf")
    with open(config_file, "w") as f:
        f.write("""\
[main]
gpgcheck=1

[mageia]
name=Mageia {args.release} Core Release
{release_url}
gpgkey={gpg_key}

[updates]
name=Mageia {args.release} Core Updates
{updates_url}
gpgkey={gpg_key}
""".format(args=args,
           gpg_key=gpg_key,
           release_url=release_url,
           updates_url=updates_url))

    invoke_dnf(args, workspace,
               args.repositories if args.repositories else ["mageia", "updates"],
               ["basesystem-minimal"],
               ["kernel-server-latest", "binutils"],
               config_file)

    reenable_kernel_install(args, workspace, masked)

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    pass
