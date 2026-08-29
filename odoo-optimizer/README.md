# Odoo Optimizer

Tunes an already-installed Odoo instance and its PostgreSQL server for the
host's RAM, CPU count, and disk type. It edits the managed performance keys in
the Odoo config and writes a PostgreSQL drop-in at
`<pg-confdir>/99-odoo-tuning.conf`. It does **not** restart services — it prints
the `systemctl restart` commands for you to run during a maintenance window.

```bash
# preview only, change nothing
python3 optimize_odoo.py --odoo-version 17 --odoo-conf /etc/odoo/odoo17.conf \
  --pg-version 16 --dry-run

# apply
sudo python3 optimize_odoo.py --odoo-version 17 --odoo-conf /etc/odoo/odoo17.conf \
  --pg-version 16 --disk-type ssd
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--odoo-version` | Yes | — | Odoo major version (used in the printed restart command) |
| `--odoo-conf` | Yes | — | Path to the Odoo `.conf` to tune |
| `--pg-version` | Yes | — | PostgreSQL major version |
| `--pg-confdir` | No | derived from `--pg-version` + OS | Override for the PostgreSQL `conf.d` directory |
| `--disk-type` | No | `ssd` | `ssd` or `hdd`; sets `effective_io_concurrency` and `random_page_cost` |
| `--reserve-ram-mb` | No | `1024` | RAM held back from the tuning math for the OS / other services |
| `--reserve-cpu` | No | `0` | CPU cores held back from the worker calculation |
| `--dry-run` | No | `False` | Print the computed values and exit without changing anything |

The Odoo config is backed up to `<path>.bak` before editing; only the
performance keys (`workers`, `max_cron_threads`, `limit_memory_*`,
`limit_time_*`, `limit_request`, `db_maxconn`) are changed. The PostgreSQL
drop-in is overwritten in full on every run. On CentOS the script also ensures
`include_dir = 'conf.d'` is active in `postgresql.conf`.
