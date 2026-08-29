#!/usr/bin/env python3
"""
Odoo + PostgreSQL Optimizer
Tunes an installed Odoo instance and its PostgreSQL server for the host's
RAM / CPU / disk. Writes managed keys into the Odoo .conf and a PostgreSQL
drop-in file. Does NOT restart services -- prints the restart commands.
"""

import argparse, os, re, shutil, subprocess, sys
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


def main():
    raise SystemExit("not implemented yet")


if __name__ == "__main__":
    main()
