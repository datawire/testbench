# SPDX-License-Identifier: LGPL-2.1+

import os.path
from typing import Optional

from ..rpm import (
    disable_kernel_install,
    invoke_dnf_or_yum,
    reenable_kernel_install,
)
from ..types import CommandLineArguments
from ..ui import complete_step

# We mount both the YUM and the DNF cache in this case, as YUM might
# just be redirected to DNF even if we invoke the former
PKG_CACHE = [
    'var/cache/yum',
    'var/cache/dnf',
]
DEFAULT_RELEASE = '7'
DEFAULT_MIRROR = None

@complete_step('Installing CentOS')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:

    masked = disable_kernel_install(args, workspace)

    gpg_key = "/etc/pki/rpm-gpg/RPM-GPG-KEY-CentOS-%s" % args.release
    if os.path.exists(gpg_key):
        gpg_key = "file://%s" % gpg_key
    else:
        gpg_key = "https://www.centos.org/keys/RPM-GPG-KEY-CentOS-%s" % args.release

    if args.mirror:
        release_url = "baseurl={args.mirror}/centos/{args.release}/os/x86_64".format(args=args)
        updates_url = "baseurl={args.mirror}/centos/{args.release}/updates/x86_64/".format(args=args)
    else:
        release_url = "mirrorlist=http://mirrorlist.centos.org/?release={args.release}&arch=x86_64&repo=os".format(args=args)
        updates_url = "mirrorlist=http://mirrorlist.centos.org/?release={args.release}&arch=x86_64&repo=updates".format(args=args)

    config_file = os.path.join(workspace, "yum.conf")
    with open(config_file, "w") as f:
        f.write("""\
[main]
gpgcheck=1

[base]
name=CentOS-{args.release} - Base
{release_url}
gpgkey={gpg_key}

[updates]
name=CentOS-{args.release} - Updates
{updates_url}
gpgkey={gpg_key}
""".format(args=args,
           gpg_key=gpg_key,
           release_url=release_url,
           updates_url=updates_url))

    invoke_dnf_or_yum(args, workspace,
                      args.repositories if args.repositories else ["base", "updates"],
                      ["systemd", "centos-release", "passwd"],
                      ["kernel", "systemd-udev", "binutils"],
                      config_file)

    reenable_kernel_install(args, workspace, masked)

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    pass
