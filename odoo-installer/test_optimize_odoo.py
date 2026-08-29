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
