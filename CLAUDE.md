# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Helper scripts for Odoo developers. Two tools: `odoo-installer/install_odoo.py`
(and its CentOS twin) provisions a full Odoo stack on a fresh server;
`odoo-installer/optimize_odoo.py` tunes an already-installed instance and its
PostgreSQL server for the host's hardware.

Each script is a **single self-contained Python 3 file** with no dependencies
beyond the standard library. There is no build step and no package. The
installers have no test suite (verified via `--dry-run` + a disposable VM);
`optimize_odoo.py` has `odoo-installer/test_optimize_odoo.py` (stdlib
`unittest`).

## The two installer variants

`install_odoo.py` (Ubuntu 22.04+, Odoo 17+) and `install_odoo_centos.py`
(CentOS 7.9, Odoo 15) are near-identical in structure and must be kept in sync
when logic changes. They share the same CLI (`argparse` args: `--version`,
`--edition`, `--domain`, `--github-user/--github-token`, `--port`, `--with-rtl`,
`--dry-run`), the same helper functions (`run`, `install_wkhtmltopdf`,
`backup_file`, `randpwd`), and the same `main()` step sequence. They diverge only
where the OS forces it:

| Concern | Ubuntu | CentOS |
|---|---|---|
| Package manager | `apt-get` | `yum` + EPEL + SCL |
| Python | system `python3` | SCL `rh-python38` (`SCL_PYTHON` constant); pip via `scl enable rh-python38 --` |
| wkhtmltopdf asset | `.jammy_amd64.deb` | `.centos7.x86_64.rpm` |
| PostgreSQL | distro `postgresql` | PGDG `postgresql13`, explicit `initdb` + service enable |
| Nginx site | `sites-available` + symlink to `sites-enabled` | drop file in `conf.d/` |
| Extra hardening | — | SELinux `httpd_can_network_connect`, `firewall-cmd` port/service |
| File ownership | (not set) | explicit `chown -R odoo:odoo` on `$base` and `/var/log/odoo` |

When you change one script's behavior, apply the equivalent change to the other
unless it is genuinely OS-specific.

## optimize_odoo.py

A third script, separate from the installer pair: it tunes an existing install
rather than creating one. Single OS-detected file (no CentOS twin — the
OS-specific surface is just the PostgreSQL `conf.d` path and service name). It
edits only the `MANAGED_ODOO_KEYS` performance keys in the Odoo `.conf`
(everything else is preserved byte-for-byte), writes a fully-owned PostgreSQL
drop-in `99-odoo-tuning.conf`, and — unlike the installers — restarts nothing,
printing the `systemctl` commands instead. All tuning arithmetic lives in pure
functions (`compute_odoo`, `compute_pg`, `render_pg_dropin`) covered by
`test_optimize_odoo.py` (stdlib `unittest`: `cd odoo-installer && python3 -m
unittest test_optimize_odoo -v`).

## Conventions in these scripts

- **Every shell action goes through `run(cmd)`**, which prints `> <cmd>` then
  `subprocess.run(shell=True)`. Pass `check=False` for steps that are expected to
  be idempotent no-ops on re-run (user already exists, role already exists, repo
  already cloned). (`optimize_odoo.py` copies `run` for consistency but never
  calls it — it runs no shell commands.)
- **Idempotency is done inline in the shell string**, e.g.
  `id -u odoo >/dev/null 2>&1 || adduser ...`, or a `psql ... | grep -q 1 ||`
  guard before `CREATE ROLE`. Keep this style rather than adding Python-side
  existence checks.
- **`--dry-run` must stay accurate.** In the installers it prints the
  `apt`/`yum` command list, the wkhtmltopdf line, then `npm_cmds` and
  `pip_cmds`, and returns before any mutation. In `optimize_odoo.py` it prints
  the computed Odoo keys and the full PG drop-in, and returns before any
  filesystem write. If you add a mutating step, add its printed form to the
  dry-run branch too.
- **Paths, service names, DB user, and config filenames are all suffixed with
  `--version`** (`/opt/odoo17`, `odoo17.service`, role `odoo17`,
  `/etc/odoo/odoo17.conf`). This is what lets multiple Odoo versions coexist on
  one host — preserve the suffixing.
- **Config/service files are backed up with `backup_file()` (`.bak`) before being
  overwritten;** Nginx site files are not, and neither is the `optimize_odoo.py`
  PostgreSQL drop-in (it is 100% owned by the script and rewritten wholesale).
- **Generated secrets** come from `randpwd()` (`secrets.token_urlsafe(24)`) and
  are written to `/root/odoo<v>_installation.json` and
  `/root/odoo<v>_credentials.txt`. Never log them to stdout.
- The scripts assume they run as **root** and make system-wide changes. There is
  no rollback beyond the `.bak` copies.

## Running

```bash
# Ubuntu, community
sudo python3 odoo-installer/install_odoo.py --version 17 --edition community --domain odoo.example.com

# Enterprise (needs GitHub creds with access to odoo/enterprise)
sudo python3 odoo-installer/install_odoo.py --version 17 --edition enterprise \
  --domain odoo.example.com --github-user USER --github-token TOKEN

# Preview without changes
python3 odoo-installer/install_odoo.py --version 17 --edition community --domain odoo.example.com --dry-run

# Tune an existing install (preview, then apply)
python3 odoo-installer/optimize_odoo.py --odoo-version 17 --odoo-conf /etc/odoo/odoo17.conf --pg-version 16 --dry-run
sudo python3 odoo-installer/optimize_odoo.py --odoo-version 17 --odoo-conf /etc/odoo/odoo17.conf --pg-version 16
```

Testing the installers is manual: run `--dry-run` to sanity-check command
generation, then run the full script on a disposable VM/container matching the
target OS. For `optimize_odoo.py`, run the unit suite
(`cd odoo-installer && python3 -m unittest test_optimize_odoo -v`) plus a
`--dry-run` against a real `.conf`.

See `odoo-installer/README.md` for the full step list, argument tables, and the
list of paths the scripts create.
