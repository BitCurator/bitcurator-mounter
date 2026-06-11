"""
Tests for bitcurator_mounter.udisks that don't require a live D-Bus / udisks2.

These exercise the pure-logic parts: DeviceInfo display formatting and the
read-only state reporting. The D-Bus enumeration and mount/unmount paths need
a real system and are covered by the manual per-release checklist in README.md.

Run with: python3 -m pytest tests/
"""

from bitcurator_mounter.udisks import DeviceInfo


def _dev(**kw):
    base = dict(
        object_path="/org/freedesktop/UDisks2/block_devices/sdb1",
        device="/dev/sdb1",
        fstype="ext4",
        label="EVIDENCE",
        size=16 * 1024**3,
        mountpoint="",
        read_only=False,
        block_read_only=False,
        is_partition=True,
        drive_path="/org/freedesktop/UDisks2/drives/drive0",
        drive_vendor="Kingston",
        drive_model="DataTraveler",
        drive_removable=True,
        drive_connection="usb",
    )
    base.update(kw)
    return DeviceInfo(**base)


def test_display_size_human_readable():
    assert _dev(size=16 * 1024**3).display_size == "16.0 GiB"
    assert _dev(size=4 * 1024**3).display_size == "4.0 GiB"
    assert _dev(size=512 * 1024).display_size == "512.0 KiB"
    assert _dev(size=0).display_size == ""
    assert _dev(size=-1).display_size == ""


def test_status_unmounted_writeable():
    d = _dev(mountpoint="", block_read_only=False)
    assert d.display_status == "(not mounted)"


def test_status_unmounted_write_protected():
    # Block device marked read-only (e.g. by the read-only policy) but not yet
    # mounted: the examiner should see it is write-protected.
    d = _dev(mountpoint="", block_read_only=True)
    assert d.display_status == "write-protected"


def test_status_mounted_readonly():
    d = _dev(mountpoint="/run/media/x/EVIDENCE", read_only=True, block_read_only=True)
    assert d.display_status == "READ ONLY"


def test_status_mounted_writeable():
    d = _dev(mountpoint="/run/media/x/SAVE", read_only=False, block_read_only=False)
    assert d.display_status == "WRITEABLE"
