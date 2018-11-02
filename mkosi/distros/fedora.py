# SPDX-License-Identifier: LGPL-2.1+

import os
import sys
from typing import Optional

from ..cli import CommandLineArguments
from ..rpm import disable_kernel_install, invoke_dnf, reenable_kernel_install
from ..ui import complete_step, warn
from ..utils import check_if_url_exists

FEDORA_KEYS_MAP = {
    '23': '34EC9CBA',
    '24': '81B46521',
    '25': 'FDB19C98',
    '26': '64DAB85D',
    '27': 'F5282EE4',
    '28': '9DB62FB1',
    '29': '429476B4',
    '30': 'CFC659B9',
}

PKG_CACHE = ['var/cache/dnf']
DEFAULT_RELEASE = '29'
DEFAULT_MIRROR = None

@complete_step('Installing Fedora')
def install(args: CommandLineArguments, workspace: str, run_build_script: bool) -> None:
    if args.release == 'rawhide':
        last = sorted(FEDORA_KEYS_MAP)[-1]
        warn('Assuming rawhide is version {} -- '.format(last) +
             'You may specify otherwise with --release=rawhide-<version>')
        args.releasever = last
    elif args.release.startswith('rawhide-'):
        args.release, args.releasever = args.release.split('-')
        sys.stderr.write('Fedora rawhide - release version: %s\n' % args.releasever)
    else:
        args.releasever = args.release

    masked = disable_kernel_install(args, workspace)

    gpg_key = "/etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-%s-x86_64" % args.releasever
    if os.path.exists(gpg_key):
        gpg_key = "file://%s" % gpg_key
    else:
        gpg_key = "https://getfedora.org/static/%s.txt" % FEDORA_KEYS_MAP[args.releasever]

    if args.mirror:
        baseurl = "{args.mirror}/releases/{args.release}/Everything/x86_64/os/".format(args=args)
        if not check_if_url_exists("%s/media.repo" % baseurl):
            baseurl = "{args.mirror}/development/{args.release}/Everything/x86_64/os/".format(args=args)

        release_url = "baseurl=%s" % baseurl
        updates_url = "baseurl={args.mirror}/updates/{args.release}/x86_64/".format(args=args)
    else:
        release_url = ("metalink=https://mirrors.fedoraproject.org/metalink?" +
                       "repo=fedora-{args.release}&arch=x86_64".format(args=args))
        updates_url = ("metalink=https://mirrors.fedoraproject.org/metalink?" +
                       "repo=updates-released-f{args.release}&arch=x86_64".format(args=args))

    config_file = os.path.join(workspace, "dnf.conf")
    with open(config_file, "w") as f:
        f.write("""\
[main]
gpgcheck=1

[fedora]
name=Fedora {args.release} - base
{release_url}
gpgkey={gpg_key}

[updates]
name=Fedora {args.release} - updates
{updates_url}
gpgkey={gpg_key}
""".format(args=args,
           gpg_key=gpg_key,
           release_url=release_url,
           updates_url=updates_url))

    invoke_dnf(args, workspace,
               args.repositories if args.repositories else ["fedora", "updates"],
               ["systemd", "fedora-release", "passwd", "glibc-minimal-langpack"],
               ["kernel-core", "systemd-udev", "binutils"],
               config_file)

    with open(os.path.join(workspace, 'root', 'etc/locale.conf'), 'w') as f:
        f.write('LANG=C.UTF-8\n')

    reenable_kernel_install(args, workspace, masked)

def install_boot_loader(args: CommandLineArguments, workspace: str, loopdev: Optional[str]) -> None:
    pass
