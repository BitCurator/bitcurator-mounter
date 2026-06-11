# Indicator icons

These two PNGs are used by `bc-policyapp` and install to
`/usr/share/pixmaps/mounter/`:

- `harddisk-readonly.png`  — shown when the read-only USB policy is active
- `harddisk-writeable.png` — shown when devices are writeable

The colors follow the danger semantics:

- `harddisk-readonly.png`  — GREEN (read-only is the safe state)
- `harddisk-writeable.png` — RED   (writeable is the dangerous state: USB
  devices can be modified)

If these files are absent at runtime, `bc-policyapp` falls back to the
themed `drive-harddisk-symbolic` icon, so a missing icon degrades gracefully
rather than breaking the indicator.
