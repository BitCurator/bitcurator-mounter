# Building the `.deb` manually

These steps build `bitcurator-mounter` from source into an installable `.deb`
on Ubuntu 22.04, 24.04, and 26.04. The package is `Architecture: all` (pure
Python), so a `.deb` built on any one release installs on all three — but
building on each is the surest way to confirm the build and its dependencies
are correct per release.

## Build-time requirement note (setuptools on 22.04)

Package metadata is declared in `pyproject.toml` using a PEP 621 `[project]`
table, which requires **setuptools >= 61** to parse. Ubuntu 22.04 ships
setuptools 59.6, which is too old; 24.04 and 26.04 are new enough.

To handle this, `debian/rules` upgrades setuptools in the build environment
**only when it is too old** (i.e. on 22.04) by fetching it with `pip`. This has
two consequences for building:

- **`python3-pip` and `ca-certificates` are required build dependencies.**
- **The build needs network access** so pip can fetch setuptools. The package
  therefore does **not** build offline in a clean `sbuild`/`pbuilder` chroot
  without network. On 24.04/26.04 no fetch happens (the override detects a new
  enough setuptools and skips it), so network is only strictly required on
  22.04.

## 1. Install the build dependencies

The same set works on all three releases:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    build-essential \
    fakeroot \
    dpkg-dev \
    debhelper \
    dh-python \
    pybuild-plugin-pyproject \
    python3-all \
    python3-setuptools \
    python3-pip \
    ca-certificates \
    git
```

> `python3-pip` and `ca-certificates` are needed so the `debian/rules` override
> can upgrade setuptools on 22.04 (see the note above). Without `python3-pip`,
> `dpkg-checkbuilddeps` aborts the build before it starts, on every release,
> because it is now a declared build dependency.

## 2. Get the source

```bash
git clone https://github.com/BitCurator/bitcurator-mounter.git
cd bitcurator-mounter
```

To build a specific release, check out its tag first, e.g.
`git checkout 0.4.3`.

## 3. Build

```bash
dpkg-buildpackage -us -uc -b
```

`-us -uc` skip signing; `-b` builds a binary-only package. Artifacts are
written to the **parent** directory, not the source tree. On 22.04 the build
will fetch a newer setuptools at this point (network required); on 24.04/26.04
it skips that step.

## 4. Result

```bash
ls ../bitcurator-mounter_*_all.deb
```

You should see `../bitcurator-mounter_<version>_all.deb`.

Optionally inspect it before installing:

```bash
dpkg-deb -I ../bitcurator-mounter_*_all.deb   # metadata + dependencies
dpkg-deb -c ../bitcurator-mounter_*_all.deb   # file list
```

## 5. Install

Use `apt`, not `dpkg -i`, so the runtime dependencies are pulled in
automatically:

```bash
sudo apt-get install ./../bitcurator-mounter_*_all.deb
```

(`dpkg -i` will fail on a clean machine because it does not fetch
dependencies.)

## Notes

- **Per-release runtime deps:** no per-release build flags are needed. The
  runtime dependencies resolve to the correct packages on each release via the
  alternatives in `debian/control` (`polkitd | policykit-1`,
  `pkexec | policykit-1`), which matters because the `policykit-1`
  transitional package was dropped on 26.04.
- **`universe` component:** the runtime dependencies live in `main` or
  `universe`. Desktop Ubuntu installs have `universe` enabled by default; on a
  minimal system run `sudo add-apt-repository universe` before step 5 if a
  dependency cannot be found.
- **Offline / clean-room builds:** because the 22.04 path fetches setuptools
  with pip, a fully offline build is not possible on 22.04 as configured. If
  you need an offline 22.04 build, either pre-seed a setuptools >= 61 into the
  build environment by other means, or move the package metadata to `setup.cfg`
  (readable by 22.04's setuptools without an upgrade).
- **Version:** the `.deb` version comes from `debian/changelog`, not from any
  git tag. Keep the changelog in step with releases.
