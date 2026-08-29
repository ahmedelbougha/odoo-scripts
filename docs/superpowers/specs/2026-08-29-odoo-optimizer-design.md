# Odoo + PostgreSQL Optimizer — Design

**Date:** 2026-08-29
**Status:** Approved for planning
**Component:** `odoo-installer/optimize_odoo.py` (new)

## Purpose

A companion script to the Odoo installers that tunes an already-installed Odoo
instance and its PostgreSQL server for the host's hardware. It reads RAM, CPU
count, and free disk from the running server, computes tuned values, and writes
them into the Odoo config file and a PostgreSQL drop-in file. It does **not**
restart services — it prints the restart commands and exits, leaving all timing
to the operator.

## Scope

**In scope:**

- One stdlib-only Python 3 file, `odoo-installer/optimize_odoo.py`.
- OS detection (Ubuntu vs CentOS/RHEL) from `/etc/os-release`.
- Auto-detection of RAM, CPU count, free disk space on the host.
- A fixed set of Odoo `[options]` tuning keys (see Tuning Math).
- A PostgreSQL tuning drop-in file `99-odoo-tuning.conf` in the server's
  `conf.d/` directory.
- `--dry-run` that prints the computed values and the full drop-in file without
  mutating anything.
- Idempotent re-runs: rewrite only the managed keys in the Odoo config;
  overwrite the PG drop-in wholesale; `.bak` the Odoo config first.

**Out of scope (may be added later):**

- A `--workload` profile hint (web / mixed / heavy-cron).
- Automatic service restarts.
- Applying PG settings via `ALTER SYSTEM` or editing `postgresql.conf` directly.
- Tuning knobs beyond the fixed list: `wal_buffers` (written as `-1` marker
  only), `huge_pages`, `checkpoint_completion_target`, `synchronous_commit`,
  `min_wal_size` / `max_wal_size`, etc.
- A separate CentOS variant script (the OS-specific surface is small enough for
  one file).
- Rollback beyond the `.bak` copy and "delete the drop-in file".

## CLI

```
optimize_odoo.py
  --odoo-version N        (required, int)   Odoo major version; used for the printed
                                            `systemctl restart odoo<N>` command and a
                                            sanity check against the conf file.
  --odoo-conf PATH        (required)         Path to the Odoo .conf to tune.
  --pg-version N          (required, int)    PostgreSQL major version, e.g. 13, 16.
  --pg-confdir PATH       (optional)         Override for the PG conf.d directory. If
                                            omitted, derived from --pg-version + detected OS.
  --disk-type {ssd,hdd}   (default: ssd)     Feeds effective_io_concurrency and
                                            random_page_cost.
  --reserve-ram-mb N      (default: 1024)    RAM held back from the tuning math for other
                                            services / the OS.
  --reserve-cpu N         (default: 0)       CPU cores held back from the worker math.
  --dry-run                                  Print computed Odoo keys and the full PG
                                            drop-in file, then exit without changes.
```

## Execution Flow

1. Parse args.
2. Detect OS from `/etc/os-release` → `ubuntu` or `centos`/`rhel`. Unknown ID is
   a fatal error unless `--pg-confdir` is given (which removes the only
   OS-dependent path).
3. Gather host facts:
   - RAM: `MemTotal` from `/proc/meminfo` (kB → MB).
   - CPUs: `os.cpu_count()`.
   - Free disk: `shutil.disk_usage()` on the filesystem containing `--odoo-conf`.
4. Validate:
   - `--odoo-conf` exists and contains an `[options]` section.
   - `usable_ram = ram_mb - reserve_ram_mb` is positive and at least 1024 MB;
     otherwise fatal ("not enough RAM to tune").
   - `usable_cpus = max(1, cpus - reserve_cpu)`.
5. Compute tuned values (see Tuning Math). All formulas are deterministic pure
   functions of `(ram_mb, cpus, disk_type, reserve_ram_mb, reserve_cpu)`.
6. Resolve the PG conf.d directory:
   - If `--pg-confdir` given, use it verbatim.
   - Else Ubuntu: `/etc/postgresql/<pg-version>/main/conf.d`.
   - Else CentOS: `/var/lib/pgsql/<pg-version>/data/conf.d`.
7. If `--dry-run`: print the computed Odoo keys (as `key = value` lines) and the
   full text of the PG drop-in file, then return before any mutation.
8. Apply Odoo config:
   - `backup_file(odoo_conf)` → `<path>.bak`.
   - Parse the file, preserving all lines. In the `[options]` section, replace
     the value of each **managed key** if present, or append it if absent. All
     non-managed keys (`admin_passwd`, `db_*`, `addons_path`, `logfile`,
     `proxy_mode`, `xmlrpc_port`, anything else) are left byte-for-byte intact.
   - Write the file back.
9. Apply PG drop-in:
   - `mkdir -p` the conf.d directory.
   - Overwrite `<confdir>/99-odoo-tuning.conf` with the generated content
     (no `.bak` — the file is 100% owned by this script).
   - CentOS only: ensure `postgresql.conf` in the data directory has an active
     `include_dir = 'conf.d'` line. If a commented or missing line, append
     `include_dir = 'conf.d'` at the end of `postgresql.conf` (after
     `backup_file()` on it). On Ubuntu this is active by default — no change.
10. Print, to stdout, the two commands the operator must run, with the correct
    service names for the detected OS:
    - `systemctl restart odoo<odoo-version>`
    - `systemctl restart postgresql` (Ubuntu) or
      `systemctl restart postgresql-<pg-version>` (CentOS)
11. Exit 0.

## Tuning Math

`usable_ram = ram_mb - reserve_ram_mb`
`usable_cpus = max(1, cpus - reserve_cpu)`

Intermediate values (`workers`, `max_cron_threads`, `limit_memory_hard`,
`db_maxconn`) are computed in this order and reused by later rows.

### Odoo `[options]` — managed keys

| Key | Formula | Notes |
|---|---|---|
| `workers` | `usable_cpus * 2 + 1`, then reduced while `workers * limit_memory_hard > 0.75 * usable_ram_bytes`; floor `2` | CPU-bound, then RAM-clamped |
| `max_cron_threads` | `1` if `workers <= 4` else `2` | |
| `limit_memory_soft` | `clamp(640, 2048, (0.6 * usable_ram) / (workers + max_cron_threads))` MB, written in **bytes** | per-worker soft cap |
| `limit_memory_hard` | `min(3072, limit_memory_soft_mb * 1.25)` MB, written in **bytes** | |
| `limit_time_cpu` | `60` | seconds |
| `limit_time_real` | `120` | seconds |
| `limit_time_real_cron` | `1200` | seconds |
| `limit_request` | `8192` | |
| `db_maxconn` | `max(8, 64 // workers)` | bounds `workers * db_maxconn` |

`limit_memory_hard` used in the `workers` clamp is the *capped* per-worker hard
value; compute a provisional `limit_memory_soft`/`limit_memory_hard` from
`usable_cpus * 2 + 1` workers, run the clamp, then recompute the memory limits
with the final `workers`. One clamp iteration is sufficient (monotonic).

### PostgreSQL drop-in — `99-odoo-tuning.conf`

Uses **total** `ram_mb` (not `usable_ram`) — standard PG sizing convention.

| Key | Formula |
|---|---|
| `max_connections` | `max(100, workers * db_maxconn + max_cron_threads + 20)` |
| `shared_buffers` | `round(0.25 * ram_mb)` MB |
| `effective_cache_size` | `round(0.70 * ram_mb)` MB |
| `maintenance_work_mem` | `min(2048, round(0.05 * ram_mb))` MB |
| `work_mem` | `clamp(4, 64, (0.25 * ram_mb) / max_connections)` MB |
| `effective_io_concurrency` | `200` if `disk_type == "ssd"` else `2` |
| `random_page_cost` | `1.1` if `disk_type == "ssd"` else `4.0` |
| `wal_buffers` | `-1` (auto; written as an explicit marker line) |

All memory values written with an explicit `MB` unit so the file is
human-readable. Header comment:

```
# generated by optimize_odoo.py on 2026-08-29 — ram=<N>MB cpus=<N> disk=<ssd|hdd>
# managed file — re-running optimize_odoo.py overwrites it wholesale
```

## Components

The file is small but breaks into these functions, each independently testable:

| Function | Responsibility | Depends on |
|---|---|---|
| `run(cmd, check=True)` | Print `> cmd`, `subprocess.run(shell=True)`. Copied from the installers for consistency. | `subprocess` |
| `backup_file(path)` | Copy `path` → `path.bak` if it exists. Copied from the installers. | `shutil`, `pathlib` |
| `detect_os()` | Read `/etc/os-release`, return `"ubuntu"` / `"centos"` / raise. | filesystem |
| `host_facts(conf_path)` | Return `(ram_mb, cpus, free_disk_mb)`. | `/proc/meminfo`, `os`, `shutil` |
| `compute_odoo(ram_mb, cpus, disk_type, reserve_ram_mb, reserve_cpu)` | Return an ordered dict of the 9 managed Odoo keys → values. Pure. | — |
| `compute_pg(ram_mb, odoo_vals, disk_type)` | Return an ordered dict of the 8 PG keys → values. Pure. | — |
| `render_pg_dropin(pg_vals, facts, disk_type)` | Return the full text of `99-odoo-tuning.conf`. Pure. | — |
| `rewrite_odoo_conf(path, odoo_vals)` | Parse, replace-or-append managed keys in `[options]`, preserve everything else, write back. | filesystem |
| `pg_confdir(os_name, pg_version, override)` | Resolve the conf.d path. Pure given `os_name`. | — |
| `ensure_include_dir(pg_data_conf)` | CentOS: append `include_dir = 'conf.d'` to `postgresql.conf` if not active. | filesystem |
| `main()` | Arg parsing, orchestration, `--dry-run` branch, final print. | all of the above |

`MANAGED_ODOO_KEYS` is a module-level tuple/set naming exactly the keys
`rewrite_odoo_conf` is allowed to touch — the single source of truth for "what
this script owns" in the Odoo file.

## Config Parsing Strategy

The Odoo `.conf` is INI-shaped but written by the installers as a plain
templated string. `rewrite_odoo_conf` operates line-by-line rather than via
`configparser` to guarantee byte-for-byte preservation of comments, blank
lines, key order, and spacing of non-managed keys:

1. Read all lines.
2. Track whether the current line is inside `[options]`.
3. For a line matching `^\s*<key>\s*=` where `<key>` is in `MANAGED_ODOO_KEYS`
   and we are inside `[options]`: replace with `<key> = <new value>`. Record the
   key as seen.
4. All other lines pass through unchanged.
5. After the `[options]` section body (next `[section]` header or EOF), append
   `<key> = <value>` lines for any managed key not seen.
6. If the file has no `[options]` section at all → fatal error (this is not a
   valid Odoo config; the script refuses rather than guessing).

## Error Handling

| Condition | Behavior |
|---|---|
| `--odoo-conf` missing or unreadable | Fatal, exit 1, clear message. |
| `--odoo-conf` has no `[options]` section | Fatal, exit 1. |
| OS unknown and no `--pg-confdir` | Fatal, exit 1 — tell the user to pass `--pg-confdir`. |
| `usable_ram < 1024 MB` (reserve too high or tiny box) | Fatal, exit 1. |
| PG conf.d directory cannot be created (permissions) | Fatal, exit 1 — the script assumes it runs as root, like the installers. |
| CentOS `postgresql.conf` not found at the expected data path | Warn, skip the `include_dir` edit, still write the drop-in, and tell the user to verify `include_dir = 'conf.d'` manually. |
| `--dry-run` | No mutations, no service-name printing errors possible; prints and exits 0. |

The script assumes it runs as **root** on the **target server** (same
assumptions as the installers). There is no rollback beyond `<odoo-conf>.bak`
and deleting `99-odoo-tuning.conf`.

## Testing

Consistent with the repo (no test suite; manual verification), plus the pure
functions make a lightweight self-check practical:

1. **`--dry-run` inspection** — run against a real Odoo `.conf` on a dev box
   with several `--reserve-ram-mb` / `--disk-type` combinations; eyeball the
   printed Odoo keys and drop-in file.
2. **Idempotency check** — run twice with the same inputs; confirm the Odoo
   `.conf` is unchanged on the second run (only `.bak` differs in mtime) and the
   drop-in is byte-identical.
3. **Preservation check** — diff `<odoo-conf>` against `<odoo-conf>.bak` after a
   real run; confirm only managed keys changed and `admin_passwd` / `db_*` /
   `addons_path` are untouched.
4. **Full run on a disposable VM** — one Ubuntu, one CentOS, each with the
   matching installer already run; after `optimize_odoo.py` + the two printed
   restart commands, confirm Odoo starts with the new `workers` and
   `SHOW shared_buffers;` in `psql` reflects the drop-in.
5. **Formula spot-checks** — a handful of `(ram, cpus)` inputs checked by hand
   against `compute_odoo` / `compute_pg` output (e.g. 4 GB / 2 CPU, 16 GB / 8
   CPU, 64 GB / 16 CPU).

## Consistency with Existing Scripts

- `run()` and `backup_file()` are copied verbatim from the installers.
- `--dry-run` returns before any mutation and prints what would be written.
- Config file is `.bak`-backed before overwrite; the PG drop-in (fully owned) is
  not.
- Single self-contained stdlib-only Python 3 file, no dependencies, no build
  step.
- Runs as root, makes system-wide changes, assumes it is on the target host.
- Unlike the installers, it performs **no service restarts** — it prints the
  commands instead.
