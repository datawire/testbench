import os
import shlex
from typing import List


# Kinda like Bash <<-'EOT' here-docs
def trim(s: str) -> str:
    return "\n".join([line.lstrip("\t") for line in str.lstrip("\n").split("\n")])


def write(fname: str, content: str, mode: int = 0o644) -> None:
    with open(fname, 'wt') as file:
        file.write(trim(content))
    os.chmod(fname, mode)


def main(args: List[str]) -> None:
    write('./etc/systemd/system/testbench-run.target', """
        [Unit]
        Description=testbench-run target
        Requires=multi-user.target
        After=multi-user.target
        Conflicts=rescue.target
        AllowIsolate=yes
        """)
    os.symlink('testbench-run.target', './etc/systemd/system/default.target')

    write('./etc/systemd/system/testbench-run.service', """
        [Unit]
        Description=testbench-run service
        Wants=network-online.target
        After=network-online.target
        ConditionFileIsExecutable=/etc/testbench-run

        [Service]
        User=testbench
        WorkingDirectory=/home/testbench
        ExecStart=/etc/testbench-run
        StandardOutput=file:/var/log/testbench-run.tap
        ExecStopPost=+/bin/sh -c 'rm -f /etc/testbench-run; systemctl poweroff --no-block'

        [Install]
        WantedBy=testbench-run.target
        """)
    # systemctl enable tesbtench-run.service
    try:
        os.mkdir('./etc/systemd/system/testbench-run.target.wants', mode=0o755)
    except FileExistsError:
        pass
    os.symlink('../testbench-run.service', './etc/systemd/system/testbench-run.target.wants/testbench-run.service')

    write('./etc/testbench-run',
          "#!/bin/sh\n" + " ".join(shlex.quote(arg) for arg in args)+"\n",
          mode=0o755)


if __name__ == "__main__":
    import sys
    main(sys.args[1:])
