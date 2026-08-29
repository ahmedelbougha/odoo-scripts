# Odoo + PostgreSQL Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `odoo-installer/optimize_odoo.py`, a stdlib-only script that reads host RAM/CPU/disk, computes tuned Odoo and PostgreSQL settings, writes them into the Odoo `.conf` (managed keys only) and a PostgreSQL `99-odoo-tuning.conf` drop-in, and prints the restart commands without restarting anything.

**Architecture:** Single self-contained Python 3 file. A layer of pure functions (`compute_odoo`, `compute_pg`, `render_pg_dropin`, `pg_confdir`) does all the arithmetic and text generation with no I/O, so they are directly unit-testable. A thin I/O layer (`detect_os`, `host_facts`, `rewrite_odoo_conf`, `ensure_include_dir`) and `main()` orchestrate. `run()` and `backup_file()` are copied verbatim from the installers for consistency.

**Tech Stack:** Python 3 standard library only (`argparse`, `os`, `re`, `shutil`, `subprocess`, `pathlib`, `datetime`). Tests use stdlib `unittest` (no pytest in this repo). No build step.

**Spec:** `docs/superpowers/specs/2026-08-29-odoo-optimizer-design.md`

## Global Constraints

- Single self-contained Python 3 file, **standard library only** — no third-party imports in `optimize_odoo.py`.
- Lives at `odoo-installer/optimize_odoo.py`. Tests at `odoo-installer/test_optimize_odoo.py`.
- `run(cmd, check=True)` and `backup_file(path)` are copied **verbatim** from `odoo-installer/install_odoo.py` — do not redesign them.
- `--dry-run` must return **before any filesystem mutation** and print the computed Odoo keys plus the full PG drop-in text.
- The script performs **no service restarts**. It prints `systemctl restart …` commands to stdout and exits.
- Odoo config edits: `.bak` the file first (via `backup_file`), then rewrite **only** the keys in `MANAGED_ODOO_KEYS`, preserving every other line byte-for-byte. The PG drop-in is overwritten wholesale with no `.bak`.
- `MANAGED_ODOO_KEYS` is the single source of truth for what the script owns in the Odoo file. Exact contents (order matters for append):
  `("workers", "max_cron_threads", "limit_memory_soft", "limit_memory_hard", "limit_time_cpu", "limit_time_real", "limit_time_real_cron", "limit_request", "db_maxconn")`
- PG drop-in filename is exactly `99-odoo-tuning.conf`.
- Assumes it runs as root on the target host (same as the installers). No rollback beyond `<odoo-conf>.bak` and deleting the drop-in.
- Memory limit values in the Odoo conf are written **in bytes** (integers). Memory values in the PG drop-in are written with an explicit `MB` unit.
- PG sizing uses **total** `ram_mb`; Odoo sizing uses `usable_ram = ram_mb - reserve_ram_mb`.

---

### Task 1: Scaffold file, copied helpers, and constants

**Files:**
- Create: `odoo-installer/optimize_odoo.py`
- Create: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - Module `optimize_odoo` importable without side effects (guarded by `if __name__ == "__main__":`).
  - `MANAGED_ODOO_KEYS: tuple[str, ...]` — the 9 keys listed in Global Constraints, in that order.
  - `PG_DROPIN_NAME = "99-odoo-tuning.conf"`.
  - `run(cmd, check=True) -> None` and `backup_file(path) -> None` copied verbatim from `install_odoo.py`.

- [ ] **Step 1: Write the failing test**

Create `odoo-installer/test_optimize_odoo.py`:

```python
import unittest

import optimize_odoo


class ScaffoldTests(unittest.TestCase):
    def test_managed_odoo_keys_exact_contents_and_order(self):
        self.assertEqual(
            optimize_odoo.MANAGED_ODOO_KEYS,
            (
                "workers",
                "max_cron_threads",
                "limit_memory_soft",
                "limit_memory_hard",
                "limit_time_cpu",
                "limit_time_real",
                "limit_time_real_cron",
                "limit_request",
                "db_maxconn",
            ),
        )

    def test_pg_dropin_name(self):
        self.assertEqual(optimize_odoo.PG_DROPIN_NAME, "99-odoo-tuning.conf")

    def test_helpers_exist(self):
        self.assertTrue(callable(optimize_odoo.run))
        self.assertTrue(callable(optimize_odoo.backup_file))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'optimize_odoo'`

- [ ] **Step 3: Write minimal implementation**

Create `odoo-installer/optimize_odoo.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Scaffold optimize_odoo.py with copied helpers and constants"
```

---

### Task 2: `compute_odoo` — Odoo `[options]` tuning math

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: `MANAGED_ODOO_KEYS` from Task 1.
- Produces:
  `compute_odoo(ram_mb: int, cpus: int, disk_type: str, reserve_ram_mb: int, reserve_cpu: int) -> "collections.OrderedDict[str, int]"`
  Returns exactly the 9 `MANAGED_ODOO_KEYS`, in that order. All values are `int`. `limit_memory_soft` and `limit_memory_hard` are **bytes**. `disk_type` is accepted for signature symmetry with `compute_pg` but is unused here.

**Formulas (from spec — implement exactly):**

- `usable_ram = ram_mb - reserve_ram_mb` (MB)
- `usable_cpus = max(1, cpus - reserve_cpu)`
- Provisional `workers_p = usable_cpus * 2 + 1`
- Per-worker memory, computed as a function of a given worker count `w`:
  - `soft_mb(w) = clamp(640, 2048, (0.6 * usable_ram) / (w + cron(w)))`
  - `hard_mb(w) = min(3072, soft_mb(w) * 1.25)`
  - `cron(w) = 1 if w <= 4 else 2`
- Clamp workers down while `w * hard_mb(w) * 1024 * 1024 > 0.75 * usable_ram * 1024 * 1024`, i.e. while `w * hard_mb(w) > 0.75 * usable_ram`; floor at `2`. One decrement loop from `workers_p` down to 2.
- Final `workers = w` from the clamp. `max_cron_threads = cron(workers)`.
- `limit_memory_soft = round(soft_mb(workers)) * 1024 * 1024`
- `limit_memory_hard = round(hard_mb(workers)) * 1024 * 1024`
- `limit_time_cpu = 60`
- `limit_time_real = 120`
- `limit_time_real_cron = 1200`
- `limit_request = 8192`
- `db_maxconn = max(8, 64 // workers)`

`clamp(lo, hi, x) = max(lo, min(hi, x))`.

- [ ] **Step 1: Write the failing test**

Add to `test_optimize_odoo.py`:

```python
class ComputeOdooTests(unittest.TestCase):
    def test_returns_managed_keys_in_order_all_ints(self):
        vals = optimize_odoo.compute_odoo(
            ram_mb=16384, cpus=8, disk_type="ssd", reserve_ram_mb=1024, reserve_cpu=0
        )
        self.assertEqual(tuple(vals.keys()), optimize_odoo.MANAGED_ODOO_KEYS)
        for k, v in vals.items():
            self.assertIsInstance(v, int, k)

    def test_small_box_4gb_2cpu(self):
        # usable_ram = 3072 MB, usable_cpus = 2, workers_p = 5
        # soft/hard clamp will pull workers down because
        # 5 * hard(5) > 0.75 * 3072 = 2304
        vals = optimize_odoo.compute_odoo(
            ram_mb=4096, cpus=2, disk_type="ssd", reserve_ram_mb=1024, reserve_cpu=0
        )
        self.assertGreaterEqual(vals["workers"], 2)
        self.assertLessEqual(vals["workers"], 5)
        # memory limits are in bytes and soft < hard
        self.assertLess(vals["limit_memory_soft"], vals["limit_memory_hard"])
        self.assertEqual(vals["limit_memory_soft"] % (1024 * 1024), 0)
        # RAM budget respected
        self.assertLessEqual(
            vals["workers"] * vals["limit_memory_hard"],
            int(0.75 * 3072) * 1024 * 1024 + vals["limit_memory_hard"],
        )

    def test_fixed_keys(self):
        vals = optimize_odoo.compute_odoo(
            ram_mb=65536, cpus=16, disk_type="hdd", reserve_ram_mb=2048, reserve_cpu=0
        )
        self.assertEqual(vals["limit_time_cpu"], 60)
        self.assertEqual(vals["limit_time_real"], 120)
        self.assertEqual(vals["limit_time_real_cron"], 1200)
        self.assertEqual(vals["limit_request"], 8192)

    def test_cron_threads_follow_worker_count(self):
        big = optimize_odoo.compute_odoo(
            ram_mb=65536, cpus=16, disk_type="ssd", reserve_ram_mb=2048, reserve_cpu=0
        )
        self.assertIn(big["max_cron_threads"], (1, 2))
        if big["workers"] <= 4:
            self.assertEqual(big["max_cron_threads"], 1)
        else:
            self.assertEqual(big["max_cron_threads"], 2)

    def test_reserve_cpu_reduces_workers(self):
        base = optimize_odoo.compute_odoo(
            ram_mb=65536, cpus=16, disk_type="ssd", reserve_ram_mb=2048, reserve_cpu=0
        )
        reserved = optimize_odoo.compute_odoo(
            ram_mb=65536, cpus=16, disk_type="ssd", reserve_ram_mb=2048, reserve_cpu=8
        )
        self.assertLess(reserved["workers"], base["workers"])

    def test_db_maxconn_floor(self):
        vals = optimize_odoo.compute_odoo(
            ram_mb=65536, cpus=32, disk_type="ssd", reserve_ram_mb=2048, reserve_cpu=0
        )
        self.assertGreaterEqual(vals["db_maxconn"], 8)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL with `AttributeError: module 'optimize_odoo' has no attribute 'compute_odoo'`

- [ ] **Step 3: Write minimal implementation**

Add `from collections import OrderedDict` to the imports. Add:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS (all ComputeOdooTests + Task 1 tests)

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Add compute_odoo tuning math"
```

---

### Task 3: `compute_pg` — PostgreSQL drop-in tuning math

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: `compute_odoo` output shape from Task 2 (`odoo_vals["workers"]`, `["max_cron_threads"]`, `["db_maxconn"]`).
- Produces:
  `compute_pg(ram_mb: int, odoo_vals: "OrderedDict[str, int]", disk_type: str) -> "collections.OrderedDict[str, object]"`
  Keys in this exact order:
  `("max_connections", "shared_buffers_mb", "effective_cache_size_mb", "maintenance_work_mem_mb", "work_mem_mb", "effective_io_concurrency", "random_page_cost", "wal_buffers")`
  Value types: all `int` except `random_page_cost` which is `float`, and `wal_buffers` which is the `int` `-1`. The `_mb` keys carry integer megabyte counts (rendering adds the `MB` unit in Task 4).

**Formulas (from spec — uses total `ram_mb`):**

- `workers = odoo_vals["workers"]`, `cron = odoo_vals["max_cron_threads"]`, `db_maxconn = odoo_vals["db_maxconn"]`
- `max_connections = max(100, workers * db_maxconn + cron + 20)`
- `shared_buffers_mb = round(0.25 * ram_mb)`
- `effective_cache_size_mb = round(0.70 * ram_mb)`
- `maintenance_work_mem_mb = min(2048, round(0.05 * ram_mb))`
- `work_mem_mb = int(_clamp(4, 64, (0.25 * ram_mb) / max_connections))`
- `effective_io_concurrency = 200 if disk_type == "ssd" else 2`
- `random_page_cost = 1.1 if disk_type == "ssd" else 4.0`
- `wal_buffers = -1`

- [ ] **Step 1: Write the failing test**

Add to `test_optimize_odoo.py`:

```python
class ComputePgTests(unittest.TestCase):
    def _odoo(self, **kw):
        base = dict(ram_mb=16384, cpus=8, disk_type="ssd", reserve_ram_mb=1024, reserve_cpu=0)
        base.update(kw)
        return optimize_odoo.compute_odoo(**base)

    def test_key_order_and_types(self):
        pg = optimize_odoo.compute_pg(16384, self._odoo(), "ssd")
        self.assertEqual(
            tuple(pg.keys()),
            (
                "max_connections",
                "shared_buffers_mb",
                "effective_cache_size_mb",
                "maintenance_work_mem_mb",
                "work_mem_mb",
                "effective_io_concurrency",
                "random_page_cost",
                "wal_buffers",
            ),
        )
        self.assertIsInstance(pg["random_page_cost"], float)
        self.assertEqual(pg["wal_buffers"], -1)
        for k in ("max_connections", "shared_buffers_mb", "effective_cache_size_mb",
                  "maintenance_work_mem_mb", "work_mem_mb", "effective_io_concurrency"):
            self.assertIsInstance(pg[k], int, k)

    def test_sizing_16gb(self):
        pg = optimize_odoo.compute_pg(16384, self._odoo(), "ssd")
        self.assertEqual(pg["shared_buffers_mb"], 4096)
        self.assertEqual(pg["effective_cache_size_mb"], 11469)
        self.assertEqual(pg["maintenance_work_mem_mb"], 819)
        self.assertGreaterEqual(pg["work_mem_mb"], 4)
        self.assertLessEqual(pg["work_mem_mb"], 64)

    def test_maintenance_work_mem_capped_at_2048(self):
        pg = optimize_odoo.compute_pg(131072, self._odoo(ram_mb=131072, cpus=32, reserve_ram_mb=4096), "ssd")
        self.assertEqual(pg["maintenance_work_mem_mb"], 2048)

    def test_max_connections_floor_100(self):
        pg = optimize_odoo.compute_pg(4096, self._odoo(ram_mb=4096, cpus=2), "ssd")
        self.assertGreaterEqual(pg["max_connections"], 100)

    def test_disk_type_hdd(self):
        pg = optimize_odoo.compute_pg(16384, self._odoo(), "hdd")
        self.assertEqual(pg["effective_io_concurrency"], 2)
        self.assertEqual(pg["random_page_cost"], 4.0)

    def test_disk_type_ssd(self):
        pg = optimize_odoo.compute_pg(16384, self._odoo(), "ssd")
        self.assertEqual(pg["effective_io_concurrency"], 200)
        self.assertEqual(pg["random_page_cost"], 1.1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL with `AttributeError: module 'optimize_odoo' has no attribute 'compute_pg'`

- [ ] **Step 3: Write minimal implementation**

Add:

```python
def compute_pg(ram_mb, odoo_vals, disk_type):
    workers = odoo_vals["workers"]
    cron = odoo_vals["max_cron_threads"]
    db_maxconn = odoo_vals["db_maxconn"]

    max_connections = max(100, workers * db_maxconn + cron + 20)

    pg = OrderedDict()
    pg["max_connections"] = int(max_connections)
    pg["shared_buffers_mb"] = int(round(0.25 * ram_mb))
    pg["effective_cache_size_mb"] = int(round(0.70 * ram_mb))
    pg["maintenance_work_mem_mb"] = int(min(2048, round(0.05 * ram_mb)))
    pg["work_mem_mb"] = int(_clamp(4, 64, (0.25 * ram_mb) / max_connections))
    pg["effective_io_concurrency"] = 200 if disk_type == "ssd" else 2
    pg["random_page_cost"] = 1.1 if disk_type == "ssd" else 4.0
    pg["wal_buffers"] = -1
    return pg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS

If `test_sizing_16gb` fails on `effective_cache_size_mb` or `maintenance_work_mem_mb`, recompute the expected literals by hand: `round(0.70 * 16384) = 11469`, `round(0.05 * 16384) = 819`. Adjust the test literals, not the formula.

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Add compute_pg tuning math"
```

---

### Task 4: `render_pg_dropin` — generate the drop-in file text

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: `compute_pg` output from Task 3.
- Produces:
  `render_pg_dropin(pg_vals: "OrderedDict[str, object]", ram_mb: int, cpus: int, disk_type: str) -> str`
  Returns the full text of `99-odoo-tuning.conf`, ending with a trailing newline. Format:

```
# generated by optimize_odoo.py on 2026-08-29 — ram=16384MB cpus=8 disk=ssd
# managed file — re-running optimize_odoo.py overwrites it wholesale
max_connections = 202
shared_buffers = 4096MB
effective_cache_size = 11469MB
maintenance_work_mem = 819MB
work_mem = 8MB
effective_io_concurrency = 200
random_page_cost = 1.1
wal_buffers = -1
```

Mapping from `pg_vals` keys to file keys: `shared_buffers_mb` → `shared_buffers` (append `MB`), `effective_cache_size_mb` → `effective_cache_size` (append `MB`), `maintenance_work_mem_mb` → `maintenance_work_mem` (append `MB`), `work_mem_mb` → `work_mem` (append `MB`). `max_connections`, `effective_io_concurrency`, `random_page_cost`, `wal_buffers` pass through as-is. Line order matches the file example above.

- [ ] **Step 1: Write the failing test**

Add to `test_optimize_odoo.py`:

```python
class RenderPgDropinTests(unittest.TestCase):
    def _text(self):
        odoo = optimize_odoo.compute_odoo(
            ram_mb=16384, cpus=8, disk_type="ssd", reserve_ram_mb=1024, reserve_cpu=0
        )
        pg = optimize_odoo.compute_pg(16384, odoo, "ssd")
        return optimize_odoo.render_pg_dropin(pg, 16384, 8, "ssd")

    def test_has_header_with_inputs(self):
        text = self._text()
        self.assertIn("# generated by optimize_odoo.py on ", text)
        self.assertIn("ram=16384MB cpus=8 disk=ssd", text)
        self.assertIn("# managed file", text)

    def test_mb_units_applied(self):
        text = self._text()
        self.assertRegex(text, r"^shared_buffers = \d+MB$", )
        self.assertIn("shared_buffers = 4096MB", text)
        self.assertIn("effective_cache_size = 11469MB", text)
        self.assertIn("maintenance_work_mem = 819MB", text)
        self.assertRegex(text, r"^work_mem = \d+MB$")

    def test_passthrough_keys(self):
        text = self._text()
        self.assertIn("effective_io_concurrency = 200", text)
        self.assertIn("random_page_cost = 1.1", text)
        self.assertIn("wal_buffers = -1", text)
        self.assertRegex(text, r"^max_connections = \d+$")

    def test_ends_with_newline_and_no_mb_key_leaks(self):
        text = self._text()
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("_mb", text)

    def test_line_order(self):
        lines = [l for l in self._text().splitlines() if l and not l.startswith("#")]
        self.assertEqual(
            [l.split(" = ")[0] for l in lines],
            [
                "max_connections",
                "shared_buffers",
                "effective_cache_size",
                "maintenance_work_mem",
                "work_mem",
                "effective_io_concurrency",
                "random_page_cost",
                "wal_buffers",
            ],
        )
```

Note: the `assertRegex` calls with `^`/`$` need `re.MULTILINE`. Rewrite them in the implementation-agnostic form the test file already imports — change those two assertions to:

```python
        import re
        self.assertTrue(any(re.match(r"shared_buffers = \d+MB$", l) for l in text.splitlines()))
        self.assertTrue(any(re.match(r"work_mem = \d+MB$", l) for l in text.splitlines()))
```

and in `test_passthrough_keys`:

```python
        import re
        self.assertTrue(any(re.match(r"max_connections = \d+$", l) for l in text.splitlines()))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL with `AttributeError: module 'optimize_odoo' has no attribute 'render_pg_dropin'`

- [ ] **Step 3: Write minimal implementation**

```python
_PG_LINE_SPEC = (
    ("max_connections", "max_connections", ""),
    ("shared_buffers_mb", "shared_buffers", "MB"),
    ("effective_cache_size_mb", "effective_cache_size", "MB"),
    ("maintenance_work_mem_mb", "maintenance_work_mem", "MB"),
    ("work_mem_mb", "work_mem", "MB"),
    ("effective_io_concurrency", "effective_io_concurrency", ""),
    ("random_page_cost", "random_page_cost", ""),
    ("wal_buffers", "wal_buffers", ""),
)


def render_pg_dropin(pg_vals, ram_mb, cpus, disk_type):
    lines = [
        f"# generated by optimize_odoo.py on {date.today().isoformat()} — "
        f"ram={ram_mb}MB cpus={cpus} disk={disk_type}",
        "# managed file — re-running optimize_odoo.py overwrites it wholesale",
    ]
    for src_key, out_key, unit in _PG_LINE_SPEC:
        lines.append(f"{out_key} = {pg_vals[src_key]}{unit}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Add render_pg_dropin text generation"
```

---

### Task 5: `pg_confdir` — resolve the PostgreSQL conf.d directory

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  `pg_confdir(os_name: str, pg_version: int, override: "str | None") -> str`
  - `override` truthy → return `override` unchanged.
  - `os_name == "ubuntu"` → `f"/etc/postgresql/{pg_version}/main/conf.d"`
  - `os_name in ("centos", "rhel")` → `f"/var/lib/pgsql/{pg_version}/data/conf.d"`
  - otherwise → raise `SystemExit` with a message telling the user to pass `--pg-confdir`.

- [ ] **Step 1: Write the failing test**

```python
class PgConfdirTests(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(
            optimize_odoo.pg_confdir("ubuntu", 16, "/custom/path"), "/custom/path"
        )

    def test_ubuntu(self):
        self.assertEqual(
            optimize_odoo.pg_confdir("ubuntu", 16, None),
            "/etc/postgresql/16/main/conf.d",
        )

    def test_centos(self):
        self.assertEqual(
            optimize_odoo.pg_confdir("centos", 13, None),
            "/var/lib/pgsql/13/data/conf.d",
        )

    def test_rhel_same_as_centos(self):
        self.assertEqual(
            optimize_odoo.pg_confdir("rhel", 13, None),
            "/var/lib/pgsql/13/data/conf.d",
        )

    def test_unknown_os_without_override_raises(self):
        with self.assertRaises(SystemExit):
            optimize_odoo.pg_confdir("gentoo", 16, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL — `AttributeError: ... 'pg_confdir'`

- [ ] **Step 3: Write minimal implementation**

```python
def pg_confdir(os_name, pg_version, override):
    if override:
        return override
    if os_name == "ubuntu":
        return f"/etc/postgresql/{pg_version}/main/conf.d"
    if os_name in ("centos", "rhel"):
        return f"/var/lib/pgsql/{pg_version}/data/conf.d"
    raise SystemExit(
        f"cannot resolve PostgreSQL conf.d for OS '{os_name}'; pass --pg-confdir"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Add pg_confdir path resolution"
```

---

### Task 6: `rewrite_odoo_conf` — replace-or-append managed keys, preserve the rest

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: `MANAGED_ODOO_KEYS` (Task 1), `compute_odoo` output (Task 2).
- Produces:
  `rewrite_odoo_conf(path: str, odoo_vals: "OrderedDict[str, int]") -> None`
  - Reads the file at `path`. If it has no `[options]` section (a line that is exactly `[options]` after stripping) → raise `SystemExit`.
  - Walks lines. "Inside options" starts after the `[options]` header line and ends at the next line matching `^\s*\[.+\]\s*$` or EOF.
  - Inside options, a line matching `^(\s*)(<key>)(\s*)=.*$` where `<key> ∈ MANAGED_ODOO_KEYS` is replaced with `<key> = <value>` (no leading indent; the installers write flush-left). Mark that key seen.
  - All other lines pass through byte-for-byte.
  - For managed keys never seen: append `<key> = <value>` lines (in `MANAGED_ODOO_KEYS` order) at the end of the options section — i.e. immediately before the line that ended the section, or at EOF. Ensure the file still ends with exactly one newline.
  - Does **not** call `backup_file` — the caller (`main`) does that.

- [ ] **Step 1: Write the failing test**

```python
import os
import tempfile


class RewriteOdooConfTests(unittest.TestCase):
    def _write(self, text):
        fd, p = tempfile.mkstemp(suffix=".conf")
        os.close(fd)
        with open(p, "w") as f:
            f.write(text)
        self.addCleanup(os.unlink, p)
        return p

    def _vals(self):
        return optimize_odoo.compute_odoo(
            ram_mb=16384, cpus=8, disk_type="ssd", reserve_ram_mb=1024, reserve_cpu=0
        )

    def test_no_options_section_raises(self):
        p = self._write("[other]\nfoo = bar\n")
        with self.assertRaises(SystemExit):
            optimize_odoo.rewrite_odoo_conf(p, self._vals())

    def test_replaces_existing_managed_key_and_preserves_others(self):
        original = (
            "[options]\n"
            "admin_passwd = SECRET\n"
            "db_user = odoo17\n"
            "workers = 0\n"
            "addons_path = /opt/odoo17/addons\n"
        )
        p = self._write(original)
        vals = self._vals()
        optimize_odoo.rewrite_odoo_conf(p, vals)
        out = open(p).read()
        self.assertIn("admin_passwd = SECRET\n", out)
        self.assertIn("db_user = odoo17\n", out)
        self.assertIn("addons_path = /opt/odoo17/addons\n", out)
        self.assertIn(f"workers = {vals['workers']}\n", out)
        self.assertNotIn("workers = 0\n", out)

    def test_appends_missing_managed_keys(self):
        p = self._write("[options]\nadmin_passwd = SECRET\n")
        vals = self._vals()
        optimize_odoo.rewrite_odoo_conf(p, vals)
        out = open(p).read()
        for k in optimize_odoo.MANAGED_ODOO_KEYS:
            self.assertIn(f"{k} = {vals[k]}\n", out)
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_does_not_touch_keys_outside_options_section(self):
        original = (
            "[options]\n"
            "workers = 1\n"
            "[queue_job]\n"
            "workers = 99\n"   # same name, different section -- must stay
        )
        p = self._write(original)
        vals = self._vals()
        optimize_odoo.rewrite_odoo_conf(p, vals)
        out = open(p).read()
        self.assertIn("[queue_job]\nworkers = 99\n", out)
        self.assertIn(f"[options]\nworkers = {vals['workers']}\n", out)

    def test_preserves_comments_and_blank_lines(self):
        original = (
            "[options]\n"
            "; my comment\n"
            "\n"
            "workers = 2\n"
        )
        p = self._write(original)
        optimize_odoo.rewrite_odoo_conf(p, self._vals())
        out = open(p).read()
        self.assertIn("; my comment\n", out)
        self.assertIn("\n\n", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL — `AttributeError: ... 'rewrite_odoo_conf'`

- [ ] **Step 3: Write minimal implementation**

```python
def rewrite_odoo_conf(path, odoo_vals):
    lines = Path(path).read_text().splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "[options]":
            header_idx = i
            break
    if header_idx is None:
        raise SystemExit(f"{path}: no [options] section; not a valid Odoo config")

    # find end of the options section
    end_idx = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if re.match(r"^\s*\[.+\]\s*$", lines[i]):
            end_idx = i
            break

    seen = set()
    out = lines[: header_idx + 1]
    for i in range(header_idx + 1, end_idx):
        line = lines[i]
        m = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*)=.*$", line)
        if m and m.group(2) in MANAGED_ODOO_KEYS:
            key = m.group(2)
            out.append(f"{key} = {odoo_vals[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key in MANAGED_ODOO_KEYS:
        if key not in seen:
            out.append(f"{key} = {odoo_vals[key]}")

    out.extend(lines[end_idx:])
    Path(path).write_text("\n".join(out) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Add rewrite_odoo_conf managed-key editor"
```

---

### Task 7: I/O helpers — `detect_os`, `host_facts`, `ensure_include_dir`

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `detect_os(release_path: str = "/etc/os-release") -> str` — parse the `ID=` line, lowercase, strip quotes. Return `"ubuntu"`, `"centos"`, `"rhel"`, or the raw id. If the file is missing, return `"unknown"`.
  - `host_facts(conf_path: str) -> tuple[int, int, int]` — `(ram_mb, cpus, free_disk_mb)`. RAM from `/proc/meminfo` `MemTotal` (kB → MB, integer). CPUs from `os.cpu_count() or 1`. Free disk from `shutil.disk_usage(dir_of_conf)` where `dir_of_conf` is the parent dir of `conf_path` if it exists else `/`. Bytes → MB.
  - `ensure_include_dir(pg_conf_path: str) -> bool` — for CentOS. If `pg_conf_path` does not exist → return `False` (caller warns). If a line matching `^\s*include_dir\s*=\s*'?conf\.d'?` (uncommented) already exists → return `True`, no change. Otherwise `backup_file(pg_conf_path)` then append `include_dir = 'conf.d'\n` and return `True`.

- [ ] **Step 1: Write the failing test**

```python
class DetectOsTests(unittest.TestCase):
    def _write(self, text):
        fd, p = tempfile.mkstemp()
        os.close(fd)
        with open(p, "w") as f:
            f.write(text)
        self.addCleanup(os.unlink, p)
        return p

    def test_ubuntu(self):
        p = self._write('NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"\n')
        self.assertEqual(optimize_odoo.detect_os(p), "ubuntu")

    def test_centos_quoted(self):
        p = self._write('ID="centos"\n')
        self.assertEqual(optimize_odoo.detect_os(p), "centos")

    def test_missing_file(self):
        self.assertEqual(optimize_odoo.detect_os("/no/such/os-release"), "unknown")


class HostFactsTests(unittest.TestCase):
    def test_returns_three_positive_ints(self):
        ram, cpus, disk = optimize_odoo.host_facts(__file__)
        self.assertIsInstance(ram, int)
        self.assertGreater(ram, 0)
        self.assertGreaterEqual(cpus, 1)
        self.assertGreater(disk, 0)


class EnsureIncludeDirTests(unittest.TestCase):
    def _write(self, text):
        fd, p = tempfile.mkstemp(suffix=".conf")
        os.close(fd)
        with open(p, "w") as f:
            f.write(text)
        self.addCleanup(os.unlink, p)
        # also clean the .bak if created
        self.addCleanup(lambda: os.path.exists(p + ".bak") and os.unlink(p + ".bak"))
        return p

    def test_missing_file_returns_false(self):
        self.assertFalse(optimize_odoo.ensure_include_dir("/no/such/postgresql.conf"))

    def test_already_present_no_change(self):
        p = self._write("shared_buffers = 128MB\ninclude_dir = 'conf.d'\n")
        before = open(p).read()
        self.assertTrue(optimize_odoo.ensure_include_dir(p))
        self.assertEqual(open(p).read(), before)

    def test_appends_when_absent(self):
        p = self._write("shared_buffers = 128MB\n#include_dir = 'conf.d'\n")
        self.assertTrue(optimize_odoo.ensure_include_dir(p))
        out = open(p).read()
        self.assertIn("\ninclude_dir = 'conf.d'\n", out)
        self.assertTrue(os.path.exists(p + ".bak"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL — missing attributes.

- [ ] **Step 3: Write minimal implementation**

```python
def detect_os(release_path="/etc/os-release"):
    try:
        text = Path(release_path).read_text()
    except OSError:
        return "unknown"
    for line in text.splitlines():
        if line.startswith("ID="):
            return line[3:].strip().strip('"').strip("'").lower()
    return "unknown"


def host_facts(conf_path):
    ram_mb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_mb = int(line.split()[1]) // 1024
                break
    except OSError:
        pass

    cpus = os.cpu_count() or 1

    d = os.path.dirname(os.path.abspath(conf_path))
    if not os.path.isdir(d):
        d = "/"
    free_disk_mb = shutil.disk_usage(d).free // (1024 * 1024)

    return ram_mb, cpus, free_disk_mb


def ensure_include_dir(pg_conf_path):
    p = Path(pg_conf_path)
    if not p.exists():
        return False
    for line in p.read_text().splitlines():
        if re.match(r"^\s*include_dir\s*=\s*'?conf\.d'?", line):
            return True
    backup_file(pg_conf_path)
    with open(pg_conf_path, "a") as f:
        f.write("include_dir = 'conf.d'\n")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py
git commit -m "Add detect_os, host_facts, ensure_include_dir I/O helpers"
```

---

### Task 8: `main()` — argparse, orchestration, `--dry-run`, restart-command output

**Files:**
- Modify: `odoo-installer/optimize_odoo.py`
- Modify: `odoo-installer/test_optimize_odoo.py`
- Modify: `odoo-installer/README.md`

**Interfaces:**
- Consumes: every function from Tasks 1–7.
- Produces:
  - `build_parser() -> argparse.ArgumentParser` — split out so tests can exercise arg parsing without running `main`.
  - `main(argv=None) -> None` — full orchestration per the spec's Execution Flow. `argv` defaults to `sys.argv[1:]`.

**Argument definitions (exact):**

| flag | dest | type | required | default |
|---|---|---|---|---|
| `--odoo-version` | `odoo_version` | `int` | yes | — |
| `--odoo-conf` | `odoo_conf` | `str` | yes | — |
| `--pg-version` | `pg_version` | `int` | yes | — |
| `--pg-confdir` | `pg_confdir` | `str` | no | `None` |
| `--disk-type` | `disk_type` | `str`, `choices=["ssd","hdd"]` | no | `"ssd"` |
| `--reserve-ram-mb` | `reserve_ram_mb` | `int` | no | `1024` |
| `--reserve-cpu` | `reserve_cpu` | `int` | no | `0` |
| `--dry-run` | `dry_run` | `store_true` | no | `False` |

**`main()` flow (implement exactly):**

1. `args = build_parser().parse_args(argv)`.
2. `os_name = detect_os()`.
3. `ram_mb, cpus, free_disk_mb = host_facts(args.odoo_conf)`.
4. If `not os.path.exists(args.odoo_conf)` → `raise SystemExit(f"{args.odoo_conf}: not found")`.
5. `usable_ram = ram_mb - args.reserve_ram_mb`; if `usable_ram < 1024` → `raise SystemExit("not enough usable RAM to tune (need >= 1024 MB after --reserve-ram-mb)")`.
6. `odoo_vals = compute_odoo(ram_mb, cpus, args.disk_type, args.reserve_ram_mb, args.reserve_cpu)`.
7. `pg_vals = compute_pg(ram_mb, odoo_vals, args.disk_type)`.
8. `confdir = pg_confdir(os_name, args.pg_version, args.pg_confdir)`.
9. `dropin_text = render_pg_dropin(pg_vals, ram_mb, cpus, args.disk_type)`.
10. If `args.dry_run`:
    - print `f"# host: ram={ram_mb}MB cpus={cpus} free_disk={free_disk_mb}MB os={os_name}"`
    - print `"# --- odoo [options] managed keys ---"`
    - for `k` in `MANAGED_ODOO_KEYS`: print `f"{k} = {odoo_vals[k]}"`
    - print `f"# --- {confdir}/{PG_DROPIN_NAME} ---"`
    - print `dropin_text` (already newline-terminated; use `end=""`)
    - `return`
11. `backup_file(args.odoo_conf)`.
12. `rewrite_odoo_conf(args.odoo_conf, odoo_vals)`.
13. `os.makedirs(confdir, exist_ok=True)`.
14. `Path(f"{confdir}/{PG_DROPIN_NAME}").write_text(dropin_text)`.
15. If `os_name in ("centos", "rhel")`:
    - `pg_conf = f"/var/lib/pgsql/{args.pg_version}/data/postgresql.conf"`
    - `if not ensure_include_dir(pg_conf): print(f"WARNING: {pg_conf} not found; ensure include_dir = 'conf.d' is set manually")`
16. Print the restart block:
    - `print("\nConfigs written. Run these when ready (this script restarts nothing):")`
    - `print(f"  systemctl restart odoo{args.odoo_version}")`
    - Ubuntu: `print("  systemctl restart postgresql")`
    - CentOS/RHEL: `print(f"  systemctl restart postgresql-{args.pg_version}")`
    - other: `print(f"  systemctl restart <your postgresql service>  # OS '{os_name}' unrecognized")`

- [ ] **Step 1: Write the failing test**

```python
import io
import contextlib


class MainDryRunTests(unittest.TestCase):
    def _conf(self):
        fd, p = tempfile.mkstemp(suffix=".conf")
        os.close(fd)
        with open(p, "w") as f:
            f.write("[options]\nadmin_passwd = SECRET\ndb_user = odoo17\n")
        self.addCleanup(os.unlink, p)
        self.addCleanup(lambda: os.path.exists(p + ".bak") and os.unlink(p + ".bak"))
        return p

    def test_build_parser_defaults(self):
        args = optimize_odoo.build_parser().parse_args(
            ["--odoo-version", "17", "--odoo-conf", "/x", "--pg-version", "16"]
        )
        self.assertEqual(args.disk_type, "ssd")
        self.assertEqual(args.reserve_ram_mb, 1024)
        self.assertEqual(args.reserve_cpu, 0)
        self.assertFalse(args.dry_run)

    def test_dry_run_prints_and_does_not_mutate(self):
        p = self._conf()
        before = open(p).read()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            optimize_odoo.main(
                [
                    "--odoo-version", "17",
                    "--odoo-conf", p,
                    "--pg-version", "16",
                    "--pg-confdir", "/tmp/does-not-matter",
                    "--dry-run",
                ]
            )
        out = buf.getvalue()
        self.assertIn("odoo [options] managed keys", out)
        self.assertIn("workers = ", out)
        self.assertIn("shared_buffers = ", out)
        self.assertIn("/tmp/does-not-matter/99-odoo-tuning.conf", out)
        self.assertEqual(open(p).read(), before)  # unchanged
        self.assertFalse(os.path.exists(p + ".bak"))

    def test_missing_conf_raises(self):
        with self.assertRaises(SystemExit):
            optimize_odoo.main(
                ["--odoo-version", "17", "--odoo-conf", "/no/such.conf",
                 "--pg-version", "16", "--pg-confdir", "/tmp/x", "--dry-run"]
            )

    def test_apply_writes_both_files(self):
        p = self._conf()
        confdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, confdir)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            optimize_odoo.main(
                ["--odoo-version", "17", "--odoo-conf", p,
                 "--pg-version", "16", "--pg-confdir", confdir]
            )
        out = buf.getvalue()
        # odoo conf got managed keys, kept secrets
        conf_after = open(p).read()
        self.assertIn("admin_passwd = SECRET", conf_after)
        self.assertIn("workers = ", conf_after)
        self.assertTrue(os.path.exists(p + ".bak"))
        # drop-in written
        self.assertTrue(os.path.exists(os.path.join(confdir, "99-odoo-tuning.conf")))
        # restart instructions printed, nothing restarted
        self.assertIn("systemctl restart odoo17", out)
        self.assertIn("restarts nothing", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: FAIL — `build_parser` missing / `main` still raises "not implemented yet".

- [ ] **Step 3: Write minimal implementation**

Replace the placeholder `main` with `build_parser` + the full `main` per the flow above:

```python
def build_parser():
    ap = argparse.ArgumentParser(description="Tune Odoo + PostgreSQL for the host hardware.")
    ap.add_argument("--odoo-version", type=int, required=True)
    ap.add_argument("--odoo-conf", required=True)
    ap.add_argument("--pg-version", type=int, required=True)
    ap.add_argument("--pg-confdir", default=None)
    ap.add_argument("--disk-type", choices=["ssd", "hdd"], default="ssd")
    ap.add_argument("--reserve-ram-mb", type=int, default=1024)
    ap.add_argument("--reserve-cpu", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    os_name = detect_os()
    ram_mb, cpus, free_disk_mb = host_facts(args.odoo_conf)

    if not os.path.exists(args.odoo_conf):
        raise SystemExit(f"{args.odoo_conf}: not found")

    usable_ram = ram_mb - args.reserve_ram_mb
    if usable_ram < 1024:
        raise SystemExit(
            "not enough usable RAM to tune (need >= 1024 MB after --reserve-ram-mb)"
        )

    odoo_vals = compute_odoo(ram_mb, cpus, args.disk_type, args.reserve_ram_mb, args.reserve_cpu)
    pg_vals = compute_pg(ram_mb, odoo_vals, args.disk_type)
    confdir = pg_confdir(os_name, args.pg_version, args.pg_confdir)
    dropin_text = render_pg_dropin(pg_vals, ram_mb, cpus, args.disk_type)

    if args.dry_run:
        print(f"# host: ram={ram_mb}MB cpus={cpus} free_disk={free_disk_mb}MB os={os_name}")
        print("# --- odoo [options] managed keys ---")
        for k in MANAGED_ODOO_KEYS:
            print(f"{k} = {odoo_vals[k]}")
        print(f"# --- {confdir}/{PG_DROPIN_NAME} ---")
        print(dropin_text, end="")
        return

    backup_file(args.odoo_conf)
    rewrite_odoo_conf(args.odoo_conf, odoo_vals)

    os.makedirs(confdir, exist_ok=True)
    Path(f"{confdir}/{PG_DROPIN_NAME}").write_text(dropin_text)

    if os_name in ("centos", "rhel"):
        pg_conf = f"/var/lib/pgsql/{args.pg_version}/data/postgresql.conf"
        if not ensure_include_dir(pg_conf):
            print(f"WARNING: {pg_conf} not found; ensure include_dir = 'conf.d' is set manually")

    print("\nConfigs written. Run these when ready (this script restarts nothing):")
    print(f"  systemctl restart odoo{args.odoo_version}")
    if os_name == "ubuntu":
        print("  systemctl restart postgresql")
    elif os_name in ("centos", "rhel"):
        print(f"  systemctl restart postgresql-{args.pg_version}")
    else:
        print(f"  systemctl restart <your postgresql service>  # OS '{os_name}' unrecognized")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS (all tests, all tasks)

- [ ] **Step 5: Update the README**

Add a section to `odoo-installer/README.md` after the existing content:

```markdown
## optimize_odoo.py

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
```

- [ ] **Step 6: Run the full test suite once more**

Run: `cd odoo-installer && python3 -m unittest test_optimize_odoo -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add odoo-installer/optimize_odoo.py odoo-installer/test_optimize_odoo.py odoo-installer/README.md
git commit -m "Add main() orchestration, argparse, and README section for optimize_odoo"
```

---

### Task 9: Manual verification on a real config + CLAUDE.md note

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the finished script.
- Produces: nothing in code; a documentation update and a manual smoke-test record.

- [ ] **Step 1: Dry-run against a realistic config**

Create a scratch config mimicking the installer output:

```bash
mkdir -p /tmp/optcheck && cat > /tmp/optcheck/odoo17.conf <<'EOF'
[options]
admin_passwd = abc123xyz
db_host = localhost
db_port = 5432
db_user = odoo17
db_password = secretpw
addons_path = /opt/odoo17/addons,/opt/odoo17/custom/addons
logfile = /var/log/odoo/odoo17.log
proxy_mode = True
xmlrpc_port = 8069
EOF
python3 odoo-installer/optimize_odoo.py --odoo-version 17 \
  --odoo-conf /tmp/optcheck/odoo17.conf --pg-version 16 \
  --pg-confdir /tmp/optcheck/conf.d --dry-run
```

Expected: prints a host line, the 9 managed Odoo keys with numeric values, and the full `99-odoo-tuning.conf` text. No files created.

- [ ] **Step 2: Apply against the scratch config and inspect**

```bash
python3 odoo-installer/optimize_odoo.py --odoo-version 17 \
  --odoo-conf /tmp/optcheck/odoo17.conf --pg-version 16 \
  --pg-confdir /tmp/optcheck/conf.d
diff /tmp/optcheck/odoo17.conf.bak /tmp/optcheck/odoo17.conf
cat /tmp/optcheck/conf.d/99-odoo-tuning.conf
```

Expected: the `diff` shows only managed keys added/changed; `admin_passwd`, `db_*`, `addons_path`, `logfile`, `proxy_mode`, `xmlrpc_port` untouched. The drop-in file exists with sane values. Restart commands were printed, nothing was restarted.

- [ ] **Step 3: Re-run and confirm idempotency**

```bash
cp /tmp/optcheck/odoo17.conf /tmp/optcheck/odoo17.conf.run1
python3 odoo-installer/optimize_odoo.py --odoo-version 17 \
  --odoo-conf /tmp/optcheck/odoo17.conf --pg-version 16 \
  --pg-confdir /tmp/optcheck/conf.d
diff /tmp/optcheck/odoo17.conf.run1 /tmp/optcheck/odoo17.conf && echo "IDEMPOTENT OK"
```

Expected: `IDEMPOTENT OK` — second run produces an identical file.

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/optcheck
```

- [ ] **Step 5: Add a CLAUDE.md note**

In `CLAUDE.md`, under "## What this repo is", change the tool list sentence to mention both tools, and add a short subsection after "## The two installer variants":

```markdown
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
```

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "Document optimize_odoo.py in CLAUDE.md; record manual verification"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| CLI (8 flags, types, defaults) | Task 8 (`build_parser`) |
| Execution flow steps 1–11 | Task 8 (`main`) |
| OS detection from `/etc/os-release` | Task 7 (`detect_os`) |
| Host facts (RAM/CPU/disk) | Task 7 (`host_facts`) |
| `usable_ram < 1024` fatal | Task 8 step 3 (flow item 5) |
| Odoo tuning math (9 keys) | Task 2 (`compute_odoo`) |
| Workers provisional→clamp→recompute | Task 2 (one decrement loop; memory recomputed from final `workers`) |
| PG tuning math (8 keys) | Task 3 (`compute_pg`) |
| PG drop-in text + header comment | Task 4 (`render_pg_dropin`) |
| `pg_confdir` resolution (override/ubuntu/centos/raise) | Task 5 |
| Odoo conf: `.bak` then managed-key rewrite, preserve rest | Task 6 (`rewrite_odoo_conf`) + Task 8 (`backup_file` in `main`) |
| No `[options]` section → fatal | Task 6 |
| PG drop-in overwritten wholesale, no `.bak` | Task 8 flow item 14 |
| CentOS `include_dir = 'conf.d'` ensure + warn-if-missing | Task 7 (`ensure_include_dir`) + Task 8 flow item 15 |
| `--dry-run` prints, mutates nothing, returns early | Task 8 flow item 10 + `test_dry_run_prints_and_does_not_mutate` |
| Prints two restart commands, restarts nothing | Task 8 flow item 16 + `test_apply_writes_both_files` |
| `run()` / `backup_file()` copied verbatim | Task 1 |
| Error-handling table | Tasks 5, 6, 8 (SystemExit paths); Task 7 (`ensure_include_dir` returns False) |
| Testing approach (dry-run, idempotency, preservation, formula spot-checks) | Task 9 (manual) + unit tests across Tasks 2–8 |
| Consistency-with-existing-scripts section | Task 1 (helpers), Task 9 (CLAUDE.md note) |

No gaps.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step has a full code block. The one cross-reference (Task 9 → installer output shape) is spelled out with a literal config.

**3. Type consistency:**
- `compute_odoo` returns `OrderedDict[str, int]` — consumed as such in Tasks 3, 6, 8.
- `compute_pg` returns keys with `_mb` suffix (`shared_buffers_mb` etc.); `render_pg_dropin`'s `_PG_LINE_SPEC` maps those exact keys → file keys. Consistent.
- `pg_confdir(os_name, pg_version, override)` — called in Task 8 as `pg_confdir(os_name, args.pg_version, args.pg_confdir)`. Consistent.
- `rewrite_odoo_conf(path, odoo_vals)` — no `backup_file` inside; `main` calls `backup_file` then `rewrite_odoo_conf`. Matches spec ("Does not call backup_file — the caller does").
- `ensure_include_dir` returns `bool`; Task 8 branches on `if not ensure_include_dir(...)`. Consistent.
- `render_pg_dropin(pg_vals, ram_mb, cpus, disk_type)` — Task 4 signature matches the Task 8 call.
- `MANAGED_ODOO_KEYS` order (Task 1) is the append order relied on in Task 6 and the print order in Task 8. Consistent.

No issues found.
