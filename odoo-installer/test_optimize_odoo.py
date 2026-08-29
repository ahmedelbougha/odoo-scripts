import os
import re
import tempfile
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
        self.assertEqual(pg["work_mem_mb"], 30)
        self.assertEqual(pg["max_connections"], 134)

    def test_maintenance_work_mem_capped_at_2048(self):
        pg = optimize_odoo.compute_pg(131072, self._odoo(ram_mb=131072, cpus=32, reserve_ram_mb=4096), "ssd")
        self.assertEqual(pg["maintenance_work_mem_mb"], 2048)

    def test_max_connections_floor_100(self):
        pg = optimize_odoo.compute_pg(4096, self._odoo(ram_mb=4096, cpus=2), "ssd")
        self.assertEqual(pg["max_connections"], 100)

    def test_disk_type_hdd(self):
        pg = optimize_odoo.compute_pg(16384, self._odoo(), "hdd")
        self.assertEqual(pg["effective_io_concurrency"], 2)
        self.assertEqual(pg["random_page_cost"], 4.0)

    def test_disk_type_ssd(self):
        pg = optimize_odoo.compute_pg(16384, self._odoo(), "ssd")
        self.assertEqual(pg["effective_io_concurrency"], 200)
        self.assertEqual(pg["random_page_cost"], 1.1)


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
        self.assertTrue(any(re.match(r"shared_buffers = \d+MB$", l) for l in text.splitlines()))
        self.assertIn("shared_buffers = 4096MB", text)
        self.assertIn("effective_cache_size = 11469MB", text)
        self.assertIn("maintenance_work_mem = 819MB", text)
        self.assertTrue(any(re.match(r"work_mem = \d+MB$", l) for l in text.splitlines()))

    def test_passthrough_keys(self):
        text = self._text()
        self.assertIn("effective_io_concurrency = 200", text)
        self.assertIn("random_page_cost = 1.1", text)
        self.assertIn("wal_buffers = -1", text)
        self.assertTrue(any(re.match(r"max_connections = \d+$", l) for l in text.splitlines()))

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

    def test_normalizes_trailing_newline_exactly_one(self):
        # Input has all managed keys already present, plus trailing blank lines
        all_keys = "\n".join(
            f"{k} = 0" for k in optimize_odoo.MANAGED_ODOO_KEYS
        )
        original = "[options]\n" + all_keys + "\n\n"
        p = self._write(original)
        optimize_odoo.rewrite_odoo_conf(p, self._vals())
        out = open(p).read()
        # Must end with exactly one newline, not two or more
        self.assertTrue(out.endswith("\n"), "File must end with newline")
        self.assertFalse(out.endswith("\n\n"), "File must not end with multiple newlines")


if __name__ == "__main__":
    unittest.main()
