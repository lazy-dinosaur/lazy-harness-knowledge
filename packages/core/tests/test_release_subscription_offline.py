"""p2-s4-1: Main may release a subscription writer reservation the provider rejected for its usage limit (charge 0)."""
import offline_guard  # noqa: F401
import tempfile, unittest
from pathlib import Path

from safety import Ledger, Stop

LIMIT = {'stopReason': 'error', 'errorMessage': 'The ChatGPT user has reached their Subscription Sharing usage limit.'}


def ledger(d, account='subscription', step='generation'):
    l = Ledger(Path(d)/'paid.jsonl', 'b', {'generation': 1.0, 'embedding': 1.0})
    l.append({'id': 'x', 'step': step, 'state': 'reserved', 'charge': .1, 'account': account, 'model': 'Luna'})
    return l


class ReleaseUsageLimit(unittest.TestCase):
    def test_usage_limit_rejection_is_released_at_zero(self):
        with tempfile.TemporaryDirectory() as d:
            l = ledger(d); l.release('x', 'usage limit', evidence=LIMIT)
            row = l.rows[-1]
            self.assertEqual((row['state'], row['charge'], row['resolution']), ('released', 0, 'subscription-usage-limit-rejected'))
            self.assertEqual(l._effect_rows('x'), [])  # the same request may be sent again

    def test_other_generation_failures_stay_uncertain(self):
        with tempfile.TemporaryDirectory() as d:
            for evidence in (None, {'stopReason': 'length'}, {'stopReason': 'error', 'errorMessage': 'timeout'}):
                with self.assertRaises(Stop):
                    ledger(d).release('x', 'r', evidence=evidence)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(Stop):
                ledger(d, account='cash').release('x', 'r', evidence=LIMIT)


if __name__ == '__main__':
    unittest.main()
