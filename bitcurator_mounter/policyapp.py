"""
BitCurator Mount Policy Indicator

System-tray application that displays the current USB mount policy
(read-only vs writeable) and lets the user toggle it. Backed by
bc-mountpolicy, invoked via pkexec, which installs or removes the
read-only udev rule.

Also offers a menu item to launch the mounter window.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as appindicator
from gi.repository import GLib, Gio, Gtk

log = logging.getLogger(__name__)

RULE_PATH = "/etc/udev/rules.d/99-bitcurator-readonly.rules"
RULE_DIR = "/etc/udev/rules.d"
RULE_BASENAME = "99-bitcurator-readonly.rules"

ICON_READONLY = "/usr/share/pixmaps/mounter/harddisk-readonly.png"
ICON_WRITEABLE = "/usr/share/pixmaps/mounter/harddisk-writeable.png"

# Fallback themed icon names if pixmaps aren't installed (useful for dev runs)
FALLBACK_ICON_READONLY = "drive-harddisk-symbolic"
FALLBACK_ICON_WRITEABLE = "drive-harddisk-symbolic"


def is_readonly_policy() -> bool:
    return os.path.isfile(RULE_PATH)


def run_mountpolicy(verb: str) -> tuple[bool, str]:
    """Invoke bc-mountpolicy via pkexec. Returns (success, stderr-on-failure).

    verb is "install" (activate read-only) or "remove" (writeable).
    """
    try:
        result = subprocess.run(
            ["pkexec", "bc-mountpolicy", verb],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return False, "pkexec not found"
    except subprocess.TimeoutExpired:
        return False, "bc-mountpolicy timed out"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "bc-mountpolicy failed").strip()
    return True, ""


class PolicyConfirmDialog(Gtk.Dialog):

    def __init__(self, message: str, parent=None):
        super().__init__(title="System Mount Policy", transient_for=parent, modal=True)
        self.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "OK", Gtk.ResponseType.OK,
        )
        self.set_border_width(6)
        self.set_default_size(520, 160)
        label = Gtk.Label(label=message)
        label.set_line_wrap(True)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        label.set_margin_start(12)
        label.set_margin_end(12)
        self.get_content_area().add(label)
        self.show_all()


class MounterAppIndicator:

    def __init__(self):
        icon = self._icon_for_current_state()
        self.ind = appindicator.Indicator.new(
            "bitcurator-mounter-indicator",
            icon,
            appindicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.ind.set_status(appindicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("BitCurator Mount Policy")

        self.menu = Gtk.Menu()

        self.ro_item = Gtk.MenuItem.new_with_label("Set USB mount policy READ-ONLY")
        self.ro_item.connect("activate", self.on_set_readonly)
        self.menu.append(self.ro_item)

        self.rw_item = Gtk.MenuItem.new_with_label("Set USB mount policy WRITEABLE")
        self.rw_item.connect("activate", self.on_set_writeable)
        self.menu.append(self.rw_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        launch_item = Gtk.MenuItem.new_with_label("Open Mounter…")
        launch_item.connect("activate", self.on_launch_mounter)
        self.menu.append(launch_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem.new_with_label("Quit")
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        self.menu.append(quit_item)

        self.menu.show_all()
        self.ind.set_menu(self.menu)

        # Guard against re-entrant dialogs. The AppIndicator menu can still be
        # activated while a modal dialog from a previous activation is open
        # (the menu lives in a separate part of the GTK main loop), which would
        # spawn a second dialog. This flag blocks that.
        self._dialog_open = False

        self._refresh_menu_sensitivity()

        # Watch /etc/udev/rules.d/ for fstab.rules being created or removed so
        # that the icon reflects state changes made outside this app (e.g.
        # someone running `rbfstab -i` directly from a terminal).
        self._setup_state_watch()

    # ----------------------------------------------------------- icon state

    def _icon_for_current_state(self) -> str:
        if is_readonly_policy():
            return ICON_READONLY if os.path.exists(ICON_READONLY) else FALLBACK_ICON_READONLY
        return ICON_WRITEABLE if os.path.exists(ICON_WRITEABLE) else FALLBACK_ICON_WRITEABLE

    def _refresh_icon(self):
        if is_readonly_policy():
            icon = ICON_READONLY if os.path.exists(ICON_READONLY) else FALLBACK_ICON_READONLY
            self.ind.set_icon_full(icon, "Read Only")
        else:
            icon = ICON_WRITEABLE if os.path.exists(ICON_WRITEABLE) else FALLBACK_ICON_WRITEABLE
            self.ind.set_icon_full(icon, "Writeable")
        self._refresh_menu_sensitivity()

    def _refresh_menu_sensitivity(self):
        # While a policy dialog is open, disable both toggle items so the menu
        # visibly reflects that an action is in progress. Otherwise, grey out
        # the option matching the current state to make the active policy obvious.
        if getattr(self, "_dialog_open", False):
            self.ro_item.set_sensitive(False)
            self.rw_item.set_sensitive(False)
            return
        ro = is_readonly_policy()
        self.ro_item.set_sensitive(not ro)
        self.rw_item.set_sensitive(ro)

    def _setup_state_watch(self):
        rules_dir = Gio.File.new_for_path(RULE_DIR)
        try:
            self._monitor = rules_dir.monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
        except GLib.Error:
            log.warning("Could not watch %s for policy changes", RULE_DIR)
            return
        self._monitor.connect("changed", self._on_rules_changed)

    def _on_rules_changed(self, _monitor, file, _other, _event):
        if file.get_basename() == RULE_BASENAME:
            GLib.idle_add(self._refresh_icon)

    # --------------------------------------------------------------- actions

    def on_set_readonly(self, _widget):
        if self._dialog_open:
            return
        if is_readonly_policy():
            self._info_dialog("The USB mount policy is already READ-ONLY.")
            return
        self._dialog_open = True
        self._refresh_menu_sensitivity()
        try:
            dialog = PolicyConfirmDialog(
                "You are about to set the system-wide USB mount policy to:\n\n"
                "READ-ONLY\n\n"
                "USB devices will be write-protected at the block layer. A "
                "partition labelled BITCURATOR-SAVE remains writeable.\n"
                "Currently mounted volumes will not be affected until remounted."
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
            ok, err = run_mountpolicy("install")
            if not ok:
                self._error_dialog(f"Failed to set READ-ONLY policy:\n\n{err}")
        finally:
            self._dialog_open = False
            self._refresh_icon()

    def on_set_writeable(self, _widget):
        if self._dialog_open:
            return
        if not is_readonly_policy():
            self._info_dialog("The USB mount policy is already WRITEABLE.")
            return
        self._dialog_open = True
        self._refresh_menu_sensitivity()
        try:
            dialog = PolicyConfirmDialog(
                "CAUTION! You are about to set the system-wide USB mount policy to:\n\n"
                "WRITEABLE\n\n"
                "Click CANCEL to remain in the READ-ONLY state. Currently mounted "
                "volumes on USB devices will not be affected until they are remounted."
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
            ok, err = run_mountpolicy("remove")
            if not ok:
                self._error_dialog(f"Failed to set WRITEABLE policy:\n\n{err}")
        finally:
            self._dialog_open = False
            self._refresh_icon()

    def on_launch_mounter(self, _widget):
        # Launch as a separate process so the indicator's main loop is unaffected
        try:
            subprocess.Popen(["bc-mounter"])
        except FileNotFoundError:
            self._error_dialog("Could not find bc-mounter on PATH.")

    # ------------------------------------------------------------- dialogs

    def _info_dialog(self, text: str):
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.run()
        dialog.destroy()

    def _error_dialog(self, text: str):
        dialog = Gtk.MessageDialog(
            transient_for=None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Mount Policy",
        )
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    MounterAppIndicator()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
