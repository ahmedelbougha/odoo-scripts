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


if __name__ == "__main__":
    unittest.main()
