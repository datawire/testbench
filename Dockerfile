# gcr.io/datawireio/testbench-mkosi
FROM docker.io/fedora
RUN dnf -y update
# I hope this list is correct.  It was taken from the mkosi README.
# HAHA, NOPE!  The upstream list is missing 'systemd-container'
RUN dnf -y install arch-install-scripts btrfs-progs debootstrap dosfstools edk2-ovmf e2fsprogs squashfs-tools gnupg python3 tar veritysetup xfsprogs xz zypper systemd-container
RUN dnf clean all
