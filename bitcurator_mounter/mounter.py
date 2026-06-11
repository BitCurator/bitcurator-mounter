"""
BitCurator Mounter

GTK3 application that lists physical block devices via udisks2 and lets the
user mount selected ones. Honors the system mount policy (set by the policy
indicator app via rbfstab) unless the user explicitly chooses to force a
device read-only.

Live-updates when devices are plugged or removed.
"""

from __future__ import annotations

import logging
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from .udisks import DeviceInfo, UDisksClient

log = logging.getLogger(__name__)


# ListStore column indices
COL_SELECTED = 0
COL_DEVICE = 1
COL_LABEL = 2
COL_FSTYPE = 3
COL_SIZE = 4
COL_MOUNTPOINT = 5
COL_STATUS = 6
COL_DRIVE = 7
COL_FORCE_RO = 8
COL_OBJECT_PATH = 9   # hidden
COL_MOUNTED = 10      # hidden, bool
COL_REMOVABLE = 11    # hidden, bool
COL_BLOCK_RO = 12     # hidden, bool — block device itself is read-only


class MounterWindow(Gtk.Window):

    def __init__(self, client: UDisksClient):
        super().__init__(title="BitCurator Mounter")
        self.client = client
        self.set_border_width(6)
        self.set_default_size(820, 480)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add(outer)

        info_label = Gtk.Label()
        info_label.set_markup(
            "Select devices to mount. Devices will be mounted according to the "
            "system policy unless <i>Force RO</i> is checked.\n"
            "Currently mounted devices will not be remounted."
        )
        info_label.set_halign(Gtk.Align.START)
        info_label.set_line_wrap(True)
        outer.pack_start(info_label, False, False, 0)

        # Toggle: show internal drives
        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.show_internal_check = Gtk.CheckButton.new_with_label(
            "Show internal (non-removable) drives"
        )
        self.show_internal_check.set_active(True)
        self.show_internal_check.connect("toggled", lambda *_: self.refresh())
        toggle_row.pack_start(self.show_internal_check, False, False, 0)

        refresh_button = Gtk.Button.new_with_label("Refresh")
        refresh_button.connect("clicked", lambda *_: self.refresh())
        toggle_row.pack_end(refresh_button, False, False, 0)
        outer.pack_start(toggle_row, False, False, 0)

        # Device list
        self.liststore = Gtk.ListStore(
            bool,   # selected
            str,    # device
            str,    # label
            str,    # fstype
            str,    # size
            str,    # mountpoint
            str,    # status
            str,    # drive (vendor model)
            bool,   # force RO
            str,    # object path
            bool,   # mounted
            bool,   # removable
            bool,   # block read-only
        )
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.treeview = Gtk.TreeView(model=self.liststore)
        scrolled.add(self.treeview)
        outer.pack_start(scrolled, True, True, 0)

        self._build_columns()

        # Button row
        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mount_button = Gtk.Button.new_with_label("Mount Selected Devices")
        mount_button.connect("clicked", self.on_mount_clicked)
        unmount_button = Gtk.Button.new_with_label("Unmount Selected Devices")
        unmount_button.connect("clicked", self.on_unmount_clicked)
        close_button = Gtk.Button.new_with_label("Close")
        close_button.connect("clicked", lambda *_: self.close())
        button_row.pack_start(mount_button, False, False, 0)
        button_row.pack_start(unmount_button, False, False, 0)
        button_row.pack_end(close_button, False, False, 0)
        outer.pack_start(button_row, False, False, 0)

        # Status bar
        self.statusbar = Gtk.Statusbar()
        self.status_ctx = self.statusbar.get_context_id("mounter")
        outer.pack_start(self.statusbar, False, False, 0)

        self.connect("destroy", Gtk.main_quit)

        # Subscribe to udisks2 changes — callbacks come in on the D-Bus thread,
        # so marshal them onto the GTK main loop via idle_add.
        self.client.connect_change_signal(
            lambda: GLib.idle_add(self.refresh)
        )

        self.refresh()

    # ----------------------------------------------------------- UI building

    def _build_columns(self):
        # Selected checkbox
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self._on_selected_toggled)
        self.treeview.append_column(
            Gtk.TreeViewColumn("Select", toggle, active=COL_SELECTED)
        )

        def text_column(title, col_index, expand=False):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=col_index)
            col.set_resizable(True)
            if expand:
                col.set_expand(True)
            self.treeview.append_column(col)

        text_column("Device", COL_DEVICE)
        text_column("Label", COL_LABEL, expand=True)
        text_column("FS", COL_FSTYPE)
        text_column("Size", COL_SIZE)
        text_column("Mount Point", COL_MOUNTPOINT, expand=True)
        text_column("Status", COL_STATUS)
        text_column("Drive", COL_DRIVE, expand=True)

        # Force-RO checkbox
        ro_toggle = Gtk.CellRendererToggle()
        ro_toggle.connect("toggled", self._on_force_ro_toggled)
        self.treeview.append_column(
            Gtk.TreeViewColumn("Force RO", ro_toggle, active=COL_FORCE_RO)
        )

    # ----------------------------------------------------------- model sync

    def refresh(self):
        """Re-snapshot devices from udisks2 and rebuild the list."""
        include_internal = self.show_internal_check.get_active()
        devices = self.client.list_devices(include_internal=include_internal)

        # Preserve checkbox state across refreshes, keyed by object path
        prev_selected = set()
        prev_force_ro = set()
        for row in self.liststore:
            if row[COL_SELECTED]:
                prev_selected.add(row[COL_OBJECT_PATH])
            if row[COL_FORCE_RO]:
                prev_force_ro.add(row[COL_OBJECT_PATH])

        self.liststore.clear()
        for d in devices:
            self.liststore.append(self._row_for(d, prev_selected, prev_force_ro))

        self._set_status(f"{len(devices)} device(s) listed.")
        return False  # for idle_add — don't repeat

    def _row_for(self, d: DeviceInfo, prev_selected: set, prev_force_ro: set):
        drive_str = " ".join(filter(None, [d.drive_vendor, d.drive_model])).strip()
        if d.drive_connection:
            drive_str = f"{drive_str} ({d.drive_connection})" if drive_str else d.drive_connection
        return [
            d.object_path in prev_selected,
            d.device,
            d.label,
            d.fstype,
            d.display_size,
            d.mountpoint,
            d.display_status,
            drive_str,
            d.object_path in prev_force_ro,
            d.object_path,
            bool(d.mountpoint),
            d.drive_removable,
            d.block_read_only,
        ]

    def _on_selected_toggled(self, _widget, path):
        self.liststore[path][COL_SELECTED] = not self.liststore[path][COL_SELECTED]

    def _on_force_ro_toggled(self, _widget, path):
        self.liststore[path][COL_FORCE_RO] = not self.liststore[path][COL_FORCE_RO]

    # ---------------------------------------------------------- mount logic

    def on_mount_clicked(self, _button):
        mounted, skipped, failed = 0, 0, []
        for row in self.liststore:
            if not row[COL_SELECTED]:
                continue
            if row[COL_MOUNTED]:
                skipped += 1
                continue
            if not row[COL_FSTYPE] or row[COL_FSTYPE] == "swap":
                skipped += 1
                continue
            # Force a read-only mount if the user requested it OR the block
            # device is already read-only (e.g. set by the read-only policy via
            # blockdev --setro). The latter guards against any udisks2 build
            # that does not auto-mount a block-RO device read-only: passing "ro"
            # explicitly makes the mount succeed and picks up the journal-safe
            # *_ro_defaults from mount_options.conf.
            force_ro = bool(row[COL_FORCE_RO]) or bool(row[COL_BLOCK_RO])
            ok, msg = self.client.mount(
                row[COL_OBJECT_PATH], read_only=force_ro
            )
            if ok:
                mounted += 1
            else:
                failed.append((row[COL_DEVICE], msg))

        self._report("Mount", mounted, skipped, failed)
        self.refresh()

    def on_unmount_clicked(self, _button):
        unmounted, skipped, failed = 0, 0, []
        for row in self.liststore:
            if not row[COL_SELECTED]:
                continue
            if not row[COL_MOUNTED]:
                skipped += 1
                continue
            ok, msg = self.client.unmount(row[COL_OBJECT_PATH])
            if ok:
                unmounted += 1
            else:
                failed.append((row[COL_DEVICE], msg))

        self._report("Unmount", unmounted, skipped, failed)
        self.refresh()

    def _report(self, verb: str, successes: int, skipped: int, failures: list):
        msg = f"{verb}: {successes} succeeded, {skipped} skipped"
        if failures:
            msg += f", {len(failures)} failed"
        self._set_status(msg)
        if failures:
            details = "\n".join(f"{dev}: {err}" for dev, err in failures)
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.CLOSE,
                text=f"{verb} failed for {len(failures)} device(s)",
            )
            dialog.format_secondary_text(details)
            dialog.run()
            dialog.destroy()

    def _set_status(self, text: str):
        self.statusbar.pop(self.status_ctx)
        self.statusbar.push(self.status_ctx, text)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    client = UDisksClient()
    window = MounterWindow(client)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
