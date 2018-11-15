# Datawire testbench --- Run tests in many environments

## Installation

Requirements:

 - Python 3.5 or better
 - Python setuptools
 - QEMU
 - Docker
 - GNU Make as `/usr/bin/make`
 - OVMF
 - `kubernaut`
 - `sudo`
 - Only runs on x86_64 hosts

Requirements of `testbench-mkosi withmount`:

 - `losetup` (part of util-linux)
 - Linux' `unshare(CLONE_NEWNS)`
 - Probably some linux specific things about `mount(8)`

If all of the dependencies are present, it should install just like
any other Python package:

    $ python3 setup.py install

## Usage

There are several executables included; however, some of them are
helper scripts that you can ignore.

### `testbench`

The main program is `testbench`.

	Usage: testbench [options] CMD='some command that generates TAP on stdout'

	options:
	  -j N     Run N virtual machines at once (default: 1).  You
	           should generally use `-j$(nproc)`.

Operation:

 1. It looks for test environment descriptions at `./environments/*.mkosi`
 2. If nescessary, it compiles those descriptions to virtual machine
    images at `./environments/*.osi`
 3. It runs the specified `CMD` in each of the test environments,
    storing the output for each at `./environments/*.tap`.
 4. It creates `./testbench.html` which is a matrix view of each of
    all of the `.tap` files.

Step 2 can be slow; but it only needs to happen once for each `.mkosi`
file.  Step 3 should be fairly fast.  Runs where step 2 has already
been completed should take on the scale of `(X+20s)*ceil(M/N)`, where:

 - X is the ammount of time it takes to run the tests natively
 - M is the number of test environments defined in `./environments/`
 - N is the `-j N` flag.

So if your normal `make test` takes 3s, and you have 12 test
environments, and have 8 cores available, `testbench -j$(nproc)`
should take around `(3s+20s)*ceil(12/8) = 23s*2 = 46s`.

### `testbench-mkosi`

`testbench-mkosi` is a fork of
[`mkosi`](https://github.com/systemd/mkosi).  It is used by
`testbench` internally, but is also useful for entering a virtual
machine to investigate failed tests.  Read not-updated-for-the-fork
docs at [./README.mkosi.md][].

You can interactively launch a test environment (for tight-loop)
development with:

	$ testbench-mkosi \
	    --output   ./environments/ENVNAME.tap.osi \
	    --defaults ./environments/ENVNAME.mkosi \
	    qemu

## Format of `.mkosi` test environment descriptions

I fibbed a little bit.  Test environment descriptions can actualy be
multiple files.

 - `./environments/ENVNAME.mkosi` (required) base INI-ish description
 - `./environments/ENVNAME.postinst` (optional) shell script to run
   after base operating system install
 - `./environments/ENVNAME.extra` (optional) More files that get
   overlaid on to filesystem, after install (FIXME: does this happen
   before or after the `postinst` script is run?).  This may either be
   a directory (in which case all files will be owned by root when
   copied), or a tarball (which will preserve the ownership
   information in the tarball).

For the syntax of the `.mkosi` file itself, see [./README.mkosi.md][].

Arch Linux example:

	[Distribution]
	Distribution=arch

	[Output]
	Bootable=yes

	[Partitions]
	RootSize=4G

	[Packages]
	Packages=
		docker
		man
		sudo
		networkmanager
	WithNetwork=yes

Debian example:

	[Distribution]
	Distribution=debian

	[Output]
	Bootable=yes

	[Partitions]
	RootSize=4G

	[Packages]
	Packages=
		docker
		man
		sudo
	WithNetwork=yes

## Related work

 - https://github.com/systemd/mkosi
 - https://git.parabola.nu/~lukeshu/notsystemd-tests.git/
 - https://godarch.com/
