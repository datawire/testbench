# gcr.io/datawireio/testbench-mkosi
FROM docker.io/fedora
# I hope this list is correct.  It was taken from the mkosi README.
# HAHA, NOPE!  The upstream list is missing:
# - 'systemd-container' (for systemd-nspawn)
# - 'psmisc' (form fuser)
RUN dnf -y update && dnf -y install arch-install-scripts btrfs-progs debootstrap dosfstools edk2-ovmf e2fsprogs squashfs-tools gnupg python3 tar veritysetup xfsprogs xz zypper systemd-container psmisc && dnf clean all
