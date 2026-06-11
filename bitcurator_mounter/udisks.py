"""
udisks2 D-Bus client for the BitCurator mounter and policy apps.

Wraps the org.freedesktop.UDisks2 service to provide:
  - Enumeration of block devices, filtered to drive-backed physical devices
  - Mount and unmount operations
  - Live notifications via InterfacesAdded / InterfacesRemoved signals

Designed to work identically on Ubuntu 22.04, 24.04, and 26.04. The udisks2
D-Bus interface has been stable across all three.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from dasbus.connection import SystemMessageBus
from dasbus.error import DBusError
from dasbus.loop import EventLoop  # noqa: F401  (re-exported for convenience)
from dasbus.typing import Variant

log = logging.getLogger(__name__)

UDISKS_BUS = "org.freedesktop.UDisks2"
UDISKS_PATH = "/org/freedesktop/UDisks2"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"
BLOCK_IFACE = "org.freedesktop.UDisks2.Block"
PARTITION_IFACE = "org.freedesktop.UDisks2.Partition"
FILESYSTEM_IFACE = "org.freedesktop.UDisks2.Filesystem"
DRIVE_IFACE = "org.freedesktop.UDisks2.Drive"


def _bytes_to_str(value) -> str:
    """udisks2 returns byte arrays (ay) for strings like device paths and labels.

    Strip the trailing NUL and decode as UTF-8 with replacement for any bad
    bytes (which shouldn't happen on labels but might on disk-image artifacts).
    """
    if value is None:
        return ""
    # dasbus surfaces 'ay' as bytes already
    if isinstance(value, bytes):
        return value.rstrip(b"\x00").decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    # Fallback: list of ints
    try:
        return bytes(value).rstrip(b"\x00").decode("utf-8", errors="replace")
    except (TypeError, ValueError):
        return str(value)


@dataclass
class DeviceInfo:
    """Snapshot of a single block device for display purposes."""
    object_path: str        # udisks2 object path, used as stable identity
    device: str             # e.g. /dev/sdb1
    fstype: str             # e.g. ext4, ntfs, vfat, "" if none
    label: str              # filesystem label, "" if none
    size: int               # size in bytes
    mountpoint: str         # current mount point, "" if not mounted
    read_only: bool         # whether mounted read-only (meaningful if mounted)
    block_read_only: bool   # whether the block device itself is read-only (BLKROGET)
    is_partition: bool
    drive_path: str         # parent drive object path, "" if none
    drive_vendor: str = ""
    drive_model: str = ""
    drive_removable: bool = False
    drive_connection: str = ""  # "usb", "sata", etc.

    @property
    def display_size(self) -> str:
        """Human-readable size."""
        if self.size <= 0:
            return ""
        units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
        size = float(self.size)
        for unit in units:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} EiB"

    @property
    def display_status(self) -> str:
        if not self.mountpoint:
            return "write-protected" if self.block_read_only else "(not mounted)"
        return "READ ONLY" if self.read_only else "WRITEABLE"


class UDisksClient:
    """Thin wrapper around udisks2 via dasbus.

    Use as a context-managed long-lived object: construct once, call list_devices()
    whenever a fresh snapshot is needed, and optionally subscribe via
    connect_change_signal() for live updates.
    """

    def __init__(self):
        self._bus = SystemMessageBus()
        self._manager = self._bus.get_proxy(UDISKS_BUS, UDISKS_PATH)
        # Cache drive properties by path to avoid repeated round-trips during
        # enumeration. Cleared whenever the object graph changes.
        self._drive_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ enum

    def list_devices(self, include_internal: bool = True) -> list[DeviceInfo]:
        """Return all block devices we want to expose to the user.

        Filtering rules:
          - Must have a Block interface (everything in udisks2 does, but defensive)
          - Must have a non-empty Drive backing (excludes loop, zram, ramdisks,
            and dm-crypt/LVM internals that don't surface as Drives)
          - Drive must not be Optical with no media
          - If include_internal is False, drive must report Removable or be
            connected via usb/firewire/sdio
        """
        self._drive_cache.clear()
        try:
            objects = self._manager.GetManagedObjects()
        except DBusError:
            log.exception("Failed to enumerate udisks2 objects")
            return []

        devices: list[DeviceInfo] = []
        for path, ifaces in objects.items():
            if BLOCK_IFACE not in ifaces:
                continue
            info = self._build_device_info(path, ifaces, objects)
            if info is None:
                continue
            if not include_internal and not self._is_user_facing_removable(info):
                continue
            devices.append(info)

        devices.sort(key=lambda d: d.device)
        return devices

    def _build_device_info(self, path: str, ifaces: dict, all_objects: dict) -> Optional[DeviceInfo]:
        block = ifaces.get(BLOCK_IFACE, {})
        partition = ifaces.get(PARTITION_IFACE)
        filesystem = ifaces.get(FILESYSTEM_IFACE)

        drive_path = _bytes_to_str(self._unwrap(block.get("Drive")))
        if not drive_path or drive_path == "/":
            # No backing drive: loop, ram, zram, dm-* internals
            return None

        drive_props = self._get_drive_props(drive_path, all_objects)
        if drive_props is None:
            return None

        # Skip optical drives with no media
        if drive_props.get("Optical") and not drive_props.get("MediaAvailable"):
            return None

        # HintIgnore is udisks2's own "hide this" flag (e.g. internal swap)
        if self._unwrap(block.get("HintIgnore")):
            return None

        device_path = _bytes_to_str(self._unwrap(block.get("Device")))
        fstype = _bytes_to_str(self._unwrap(block.get("IdType")))
        label = _bytes_to_str(self._unwrap(block.get("IdLabel")))
        size = int(self._unwrap(block.get("Size")) or 0)

        # Block.ReadOnly reflects the block device's own RO flag (BLKROGET),
        # which is what `blockdev --setro` sets. Available whether or not the
        # device is mounted, so we can use it to decide whether to force a
        # read-only mount regardless of udisks2's auto-detection behavior.
        block_read_only = bool(self._unwrap(block.get("ReadOnly")))

        mountpoint = ""
        read_only = False
        if filesystem is not None:
            mountpoints = self._unwrap(filesystem.get("MountPoints")) or []
            if mountpoints:
                mountpoint = _bytes_to_str(mountpoints[0])
                read_only = block_read_only

        return DeviceInfo(
            object_path=path,
            device=device_path,
            fstype=fstype,
            label=label,
            size=size,
            mountpoint=mountpoint,
            read_only=read_only,
            block_read_only=block_read_only,
            is_partition=partition is not None,
            drive_path=drive_path,
            drive_vendor=str(self._unwrap(drive_props.get("Vendor", "")) or ""),
            drive_model=str(self._unwrap(drive_props.get("Model", "")) or ""),
            drive_removable=bool(self._unwrap(drive_props.get("Removable"))),
            drive_connection=str(self._unwrap(drive_props.get("ConnectionBus", "")) or ""),
        )

    def _get_drive_props(self, drive_path: str, all_objects: dict) -> Optional[dict]:
        if drive_path in self._drive_cache:
            return self._drive_cache[drive_path]
        drive_ifaces = all_objects.get(drive_path)
        if not drive_ifaces or DRIVE_IFACE not in drive_ifaces:
            return None
        props = drive_ifaces[DRIVE_IFACE]
        self._drive_cache[drive_path] = props
        return props

    @staticmethod
    def _unwrap(value):
        """Pull a Python value out of a GLib.Variant, or pass through.

        GetManagedObjects returns property values as GLib.Variant (dasbus
        re-exports GLib.Variant as Variant). .unpack() recursively converts to
        native Python types. Note that 'ay' byte-arrays unpack to a list of
        ints, not bytes -- _bytes_to_str handles that case.
        """
        if isinstance(value, Variant):
            return value.unpack()
        return value

    @staticmethod
    def _is_user_facing_removable(info: DeviceInfo) -> bool:
        return info.drive_removable or info.drive_connection in {"usb", "ieee1394", "sdio"}

    # ------------------------------------------------------------- mount ops

    def mount(self, object_path: str, read_only: bool = False) -> tuple[bool, str]:
        """Mount a filesystem. Returns (success, message-or-mountpoint).

        If read_only is True, pass options={"options": "ro"} to force a
        read-only mount. The caller is expected to set this when the device's
        block layer is already read-only (info.block_read_only), so that the
        mount succeeds as read-only even if udisks2 does not auto-detect the
        block RO flag. udisks2 applies the journal-safe *_ro_defaults from
        /etc/udisks2/mount_options.conf to read-only mounts.
        """
        proxy = self._bus.get_proxy(UDISKS_BUS, object_path)
        options: dict = {}
        if read_only:
            options["options"] = Variant("s", "ro")
        try:
            mountpoint = proxy.Mount(options)
            return True, mountpoint
        except DBusError as e:
            log.warning("Mount failed for %s: %s", object_path, e)
            return False, str(e)

    def unmount(self, object_path: str) -> tuple[bool, str]:
        proxy = self._bus.get_proxy(UDISKS_BUS, object_path)
        try:
            proxy.Unmount({})
            return True, ""
        except DBusError as e:
            log.warning("Unmount failed for %s: %s", object_path, e)
            return False, str(e)

    # ------------------------------------------------------------- signals

    def connect_change_signal(self, callback: Callable[[], None]):
        """Fire `callback` whenever the device graph changes.

        We don't try to do delta updates — the caller just re-runs list_devices()
        from scratch. For ~dozens of devices that's negligible and far simpler
        than tracking partial state.
        """
        def _on_added(*_args, **_kwargs):
            self._drive_cache.clear()
            callback()

        def _on_removed(*_args, **_kwargs):
            self._drive_cache.clear()
            callback()

        # ObjectManager signals
        self._manager.InterfacesAdded.connect(_on_added)
        self._manager.InterfacesRemoved.connect(_on_removed)
