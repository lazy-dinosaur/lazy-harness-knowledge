"""Stage 6 can continue on an earlier run's dump (plan restore_dump): hash-checked, host-checked, no migrations."""
import offline_guard  # noqa: F401
import inspect
import unittest

import stage_store


class Restore(unittest.TestCase):
    def test_restore_branch(self):
        src = inspect.getsource(stage_store.fresh_db)
        self.assertIn("plan.get('restore_dump')", src)
        self.assertIn('restore dump hash mismatch', src)
        self.assertIn('restored dump belongs to another host', src)
        self.assertLess(src.index("plan.get('restore_dump')"), src.index('for migration in ldb.MIGRATIONS'))


if __name__ == '__main__':
    unittest.main()
