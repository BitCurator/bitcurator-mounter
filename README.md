![Logo](https://github.com/BitCurator/bitcurator.github.io/blob/main/logos/BitCurator-Basic-400px.png)

# bitcurator-mounter

[![GitHub issues](https://img.shields.io/github/issues/bitcurator/bitcurator-mounter.svg)](https://github.com/bitcurator/bitcurator-mounter/issues)
[![GitHub Release](https://img.shields.io/github/v/release/bitcurator/bitcurator-mounter?include_prereleases)](https://github.com/bitcurator/bitcurator-mounter/releases)
[![Release](https://github.com/bitcurator/bitcurator-mounter/actions/workflows/release.yml/badge.svg)](https://github.com/bitcurator/bitcurator-mounter/actions/workflows/release.yml)
[![Build](https://github.com/bitcurator/bitcurator-mounter/actions/workflows/validate.yml/badge.svg)](https://github.com/bitcurator/bitcurator-mounter/actions/workflows/validate.yml)
[![GitHub forks](https://img.shields.io/github/forks/bitcurator/bitcurator-mounter.svg)](https://github.com/bitcurator/bitcurator-mounter/network)

GTK3 mount-policy indicator and block-device mounter for the BitCurator
environment. Replaces the legacy `bc_mounter.py` and `bc_policyapp.py`
scripts and the `rbfstab` fstab-rewriting mechanism.

Tested against Ubuntu 22.04, 24.04, and 26.04.

## Architecture summary

- **`bc-mounter`** lists physical block devices (those backed by a real
  udisks2 `Drive`) and lets the user mount or unmount them. Live-updates
  when devices are plugged or removed. When a device is read-only at the
  block layer (because the read-only policy is active), it is mounted
  read-only automatically.
- **`bc-policyapp`** is an Ayatana AppIndicator that toggles the read-only
  USB mount policy. It calls `bc-mountpolicy` via pkexec.
- **`bc-mountpolicy`** is a small privileged helper (replacing `rbfstab`)
  that activates or deactivates the policy by installing or removing one
  udev rule.

Both GUI apps share `bitcurator_mounter.udisks`, a thin dasbus wrapper
around the `org.freedesktop.UDisks2` service.

## How the read-only policy works

The policy no longer rewrites `/etc/fstab`. Instead:

1. **Block-layer protection.** When active, a udev rule
   (`99-bitcurator-readonly.rules`) matches USB block partitions and runs
   `blockdev --setro` on each, marking the device read-only at the block
   layer. Writes — including filesystem journal/log recovery — are rejected
   by the device itself, regardless of how it is later mounted. This is the
   modern equivalent of `rbfstab`'s loop-mount trick, and is stronger
   because it applies to every access path, not just fstab-driven mounts.
2. **No automount race.** The same rule sets `UDISKS_AUTO=0` so USB devices
   do not automount ahead of the `--setro` call. The user mounts explicitly
   via `bc-mounter`, which is the established BitCurator workflow.
3. **Journal-safe mounting.** `/etc/udisks2/mount_options.conf` supplies
   per-filesystem `*_ro_defaults` (`noload`, `norecovery`, `nointegrity`,
   `nolog`) that udisks2 applies to read-only mounts, so journaled
   filesystems mount cleanly against a read-only block device instead of
   failing on a write attempt.
4. **Writeable exemption.** A partition labelled **`BITCURATOR-SAVE`** is
   left writeable and automountable, to support user-created USB-persistent
   installs with a writeable save partition. **This is the only label that
   grants writeability under the read-only policy** — a device carrying it
   is writeable even when the policy is read-only. This is a deliberate
   sharp edge; see the warning below.

The policy state is indicated by the presence of
`/etc/udev/rules.d/99-bitcurator-readonly.rules`. `bc-policyapp` checks for
this file to choose its icon and watches the directory for external changes.

> **Write-blocker warning.** The read-only policy is a convenience to avoid
> inadvertent writes to USB devices. It is **not** a substitute for a
> hardware write blocker for forensic acquisition. In particular, any 
> partition labelled `BITCURATOR-SAVE` is writeable regardless of policy.

## System dependencies

Runtime dependencies, required on all three target releases. All are in the
main or universe archives.

```
python3-dasbus
python3-gi
gir1.2-gtk-3.0
gir1.2-ayatanaappindicator3-0.1
gir1.2-glib-2.0
polkitd            # + pkexec; on 22.04 both come from policykit-1
udisks2
util-linux        # provides blockdev
```

For building the `.deb` from source — including the additional build
dependencies and the Ubuntu 22.04 setuptools requirement — see
[`BUILD.md`](BUILD.md).

## File inventory and install destinations

| Source | Installed to | Role |
| --- | --- | --- |
| `bitcurator_mounter/` | python site-packages | the two GUI apps + udisks client |
| `data/sbin/bc-mountpolicy` | `/usr/sbin/bc-mountpolicy` (0755) | privileged policy toggle helper |
| `data/udev/readonly.rules` | `/usr/share/bitcurator-mounter/readonly.rules` | udev rule **template** (inactive) |
| `data/udisks2/mount_options.conf` | `/etc/udisks2/mount_options.conf` | journal-safe RO mount options (static) |
| `data/polkit/io.bitcurator.mountpolicy.policy` | `/usr/share/polkit-1/actions/` | pkexec auth for `bc-mountpolicy` |
| `data/desktop/bc-mounter.desktop` | `/usr/share/applications/` | mounter launcher |
| `data/desktop/bc-policyapp.desktop` | `/etc/xdg/autostart/` | indicator autostart |
| `data/pixmaps/mounter/harddisk-readonly.png` | `/usr/share/pixmaps/mounter/` | indicator icon (read-only / safe) |
| `data/pixmaps/mounter/harddisk-writeable.png` | `/usr/share/pixmaps/mounter/` | indicator icon (writeable / danger) |

When the policy is active, `bc-mountpolicy install` additionally creates
`/etc/udev/rules.d/99-bitcurator-readonly.rules` (copied from the template).
That file is the only one whose presence changes with the policy state.

## Salt state changes required

The existing states in `bitcurator-salt` need the following. **There is no
upgrade path from previous BitCurator releases**, so no migration of old
`rbfstab`/`fstab.rules` state is needed — but a clean install must not leave
any legacy artifacts behind.

1. **Remove rbfstab entirely.** Ensure `rbfstab` is absent from
   `/usr/sbin`, and that no `KERNEL=="sd?*", RUN+="/usr/sbin/rbfstab"` udev
   rule and no `fstab.rules` file are installed. Remove any state that
   dropped the legacy `.py` scripts into `/usr/local/bin`.
2. **Install the `bitcurator-mounter` package** (as a `.deb`) and add the
   system dependencies above to the apt package list for all three release
   maps in `bitcurator/files/`.
3. **Verify the static files** land at the destinations in the table above:
   the udisks2 config, the polkit action, the udev rule template, the
   helper in `/usr/sbin`, the two desktop files, and the indicator pixmaps.
   These are `file.managed` states (or handled by the `.deb`).
4. **Do not pre-create** `99-bitcurator-readonly.rules`. The default policy
   state is writeable (no rule present); the user opts into read-only via
   the indicator. If a deployment should default to read-only, add a state
   that runs `bc-mountpolicy install` once at provision time.

The icon pixmaps at
`/usr/share/pixmaps/mounter/harddisk-{readonly,writeable}.png` are installed
by the package; the indicator falls back to themed icons if they are missing.

## Packaging and CI

- **`debian/`** — the Debian packaging tree. Build a `.deb` with
  `dpkg-buildpackage -us -uc -b`; see [`BUILD.md`](BUILD.md) for the full
  per-release instructions, including the Ubuntu 22.04 setuptools handling.
- **`.github/workflows/release.yml`** — builds the `.deb` and publishes a
  GitHub release when a version tag is pushed. A tag with a hyphen (e.g.
  `0.4.3-rc.1`) is published as a pre-release; a plain tag (e.g. `0.4.3`) as
  a full release.
- **`.github/workflows/validate.yml`** — on every push to the main branch
  and every pull request, builds, installs, and tests the package in
  containers for all three target releases. This validates build
  dependencies, runtime-dependency resolution, file placement, and Python
  import compatibility — it does not perform functional (mount) testing,
  which requires real hardware.

## Why GTK3 (not GTK4)

libadwaita versions diverge across 22.04 (1.0), 24.04 (1.4), and 26.04
(1.6+). Targeting the lowest common denominator gives up most of the
benefit of moving to GTK4, while targeting newer APIs breaks 22.04. GTK3
looks and behaves identically across all three and is fully supported on
each. Revisit once 22.04 leaves standard support (April 2027).

## Why udisks2 + udev (not blkid/sfdisk/fstab shell-outs)

- udisks2 already distinguishes real block devices from loop/zram/dm
  internals — no string-grepping device names.
- No `sudo` from a GUI session; udisks2 handles polkit, and the one
  privileged action (`bc-mountpolicy`) goes through pkexec.
- Free live device updates via D-Bus signals.
- No `/etc/fstab` mutation, no PID lock, no `/media` cleanup — eliminating
  the class of bugs `rbfstab` accumulated fixes for since 2011.
- Stable interfaces across all three target releases.

## Testing

`UDisksClient` can be exercised on any VM with at least one block device:

```python
from bitcurator_mounter.udisks import UDisksClient
c = UDisksClient()
for d in c.list_devices(include_internal=True):
    print(d.device, d.fstype, d.display_status, d.block_read_only)
```

### Per-release verification checklist (run on 22.04, 24.04, 26.04)

- `mount_options.conf` honors the `*_ro_defaults` / `*_ro_allow` keys
  (oldest udisks2 is on 22.04 — verify the key syntax there first).
- After `bc-mountpolicy install`, plugging a USB stick marks it read-only:
  `blockdev --getro /dev/sdX1` returns 1, and a write to the raw device
  fails. Confirm `--setro` is applied to the **partition**, not just the
  parent disk.
- The device does not automount while the policy is active (`UDISKS_AUTO=0`
  honored by the desktop automounter).
- Mounting a journaled FS (ext4/xfs) via `bc-mounter` while write-protected
  succeeds and `findmnt -no OPTIONS <mp>` shows `ro` plus the journal-safe
  option; no write reaches the device.
- A partition labelled `BITCURATOR-SAVE` stays writeable and automountable
  while a sibling USB stick goes read-only.
- `pkexec bc-mountpolicy install|remove` produces a graphical auth prompt.
- Toggling the policy updates the indicator icon within ~1s (both via the
  menu and when `bc-mountpolicy` is run directly from a terminal).
- Whether NTFS is handled by the kernel `ntfs3` driver or `ntfs-3g`, so the
  correct `mount_options.conf` stanza (`ntfs3` vs `ntfs`) applies.

## Deliberately out of scope

- The optional polkit rule governing
  `org.freedesktop.udisks2.filesystem-mount-other` (defense-in-depth on top
  of `--setro`). The block-layer protection is the real control; add this
  later only if tighter governance of writeable mounting is wanted.
- udisks2 `LoopSetup`/`Encrypted.Unlock` operations (mounting disk images,
  unlocking encrypted devices) — useful forensic features, but a scope
  expansion rather than a translation of the existing apps.

## BitCurator documentation, help, and discussions

Visit the [BitCurator wiki on GitHub](https://github.com/BitCurator/bitcurator-distro/wiki/Releases) to find the [latest version of our Quickstart Guide](https://github.com/BitCurator/bitcurator-distro/wiki/Releases#quickstart-guide).

Have a question or need help? Visit the [bitcurator-users Google Group](https://groups.google.com/d/forum/bitcurator-users).

Some community maintained documentation and resources are available at
[the BitCurator documentation hosted on GitHub Pages](https://bitcurator.github.io/documentation/). Note that the information on this site may lag behind the latest release(s).

A [BitCurator Discussions](https://github.com/orgs/BitCurator/discussions) board is also available for ideas or comments.

## License(s)

The BitCurator logo, BitCurator project documentation, and other non-software products of the BitCurator team are subject to the the Creative Commons Attribution 4.0 Generic license (CC By 4.0).

Unless otherwise indicated, software items in this repository are distributed under the terms of the GNU General Public License v3.0. See the LICENSE file for additional details.

In addition to software produced by the BitCurator team, BitCurator packages and modifies open source software produced by other developers. Licenses and attributions are retained here where applicable.

## Development Team and Support

The BitCurator environment is volunteer-maintained. BitCurator was originally developed in the School of Library and Information Science at the University of North Carolina at Chapel Hill with funding provided by the Andrew W. Mellon Foundation (2011-2014).

Community support is managed by the BitCurator Consortium. Find out more at:

https://bitcuratorconsortium.org/
