#!/usr/bin/env python3
"""
Odoo + PostgreSQL Optimizer
Tunes an installed Odoo instance and its PostgreSQL server for the host's
RAM / CPU / disk. Writes managed keys into the Odoo .conf and a PostgreSQL
drop-in file. Does NOT restart services -- prints the restart commands.
"""

import argparse, os, re, shutil, subprocess, sys
from collections import OrderedDict
from datetime import date
from pathlib import Path

MANAGED_ODOO_KEYS = (
    "workers",
    "max_cron_threads",
    "limit_memory_soft",
    "limit_memory_hard",
    "limit_time_cpu",
    "limit_time_real",
    "limit_time_real_cron",
    "limit_request",
    "db_maxconn",
)

PG_DROPIN_NAME = "99-odoo-tuning.conf"


def run(cmd, check=True):
    print(">", cmd)
    subprocess.run(cmd, shell=True, check=check)


def backup_file(path):
    p = Path(path)
    if p.exists():
        shutil.copy2(p, str(p) + ".bak")


def _clamp(lo, hi, x):
    return max(lo, min(hi, x))


def compute_odoo(ram_mb, cpus, disk_type, reserve_ram_mb, reserve_cpu):
    usable_ram = ram_mb - reserve_ram_mb
    usable_cpus = max(1, cpus - reserve_cpu)

    def cron(w):
        return 1 if w <= 4 else 2

    def soft_mb(w):
        return _clamp(640, 2048, (0.6 * usable_ram) / (w + cron(w)))

    def hard_mb(w):
        return min(3072, soft_mb(w) * 1.25)

    w = usable_cpus * 2 + 1
    while w > 2 and w * hard_mb(w) > 0.75 * usable_ram:
        w -= 1

    workers = w
    vals = OrderedDict()
    vals["workers"] = int(workers)
    vals["max_cron_threads"] = int(cron(workers))
    vals["limit_memory_soft"] = int(round(soft_mb(workers))) * 1024 * 1024
    vals["limit_memory_hard"] = int(round(hard_mb(workers))) * 1024 * 1024
    vals["limit_time_cpu"] = 60
    vals["limit_time_real"] = 120
    vals["limit_time_real_cron"] = 1200
    vals["limit_request"] = 8192
    vals["db_maxconn"] = max(8, 64 // workers)
    return vals


def main():
    raise SystemExit("not implemented yet")


if __name__ == "__main__":
    main()
