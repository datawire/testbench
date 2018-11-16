# gcr.io/datawireio/testbench-mkosi
FROM docker.io/fedora
RUN dnf -y update
# I hope this list is correct.  It was taken from the mkosi README.
# HAHA, NOPE!  The upstream list is missing:
# - 'systemd-container' (for systemd-nspawn)
# - 'psmisc' (for fuser)
RUN dnf -y install arch-install-scripts btrfs-progs debootstrap dosfstools edk2-ovmf e2fsprogs squashfs-tools gnupg python3 tar veritysetup xfsprogs xz zypper systemd-container psmisc
# Load in our patched systemd-nspawn, to work around Fedora's being
# broken https://github.com/systemd/mkosi/issues/280
ADD *.rpm ./
RUN dnf -y install systemd-239*.rpm systemd-container-239*.rpm systemd-libs-239*.rpm systemd-pam-239*.rpm
RUN rm -- *.rpm
# Final clean-up
RUN dnf clean all
