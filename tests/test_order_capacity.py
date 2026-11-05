from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import decoded_runtime as decoded
import order_board_availability as board
from tools import run_short_cycle as cycle


def capacity(remaining=0,kind='party'):
    return dict(category=kind,tokenItemId=103 if kind=='party' else 104,remaining=remaining,limit=6)


class CapacityTests(unittest.TestCase):
    def data(self,remaining=0,version='90'):
        return dict(packageVersion=version,orderCapacity=dict(complete=True,semanticsPackageVersion=version,
                    entries=[capacity(remaining),capacity(0,'takeout')]))

    def test_decoded_capacity_requires_complete_current_version_and_exact_category(self):
        data=self.data(6)
        with patch.object(decoded,'validate'):
            self.assertEqual(decoded.order_capacity('party',data)['remaining'],6)
            self.assertEqual(decoded.order_capacity('party',self.data(6,'99'))['remaining'],6)
            self.assertEqual(decoded.order_capacity('party',self.data(0,'100'))['remaining'],0)
            self.assertEqual(decoded.order_capacity('party',self.data(6,'100'))['remaining'],6)
            for invalid in ({},dict(data,packageVersion='91'),dict(data,packageVersion='98'),
                            dict(data,packageVersion='99'),dict(data,packageVersion='100'),
                            dict(data,orderCapacity={'complete':False, 'reason':'unverified_package_version'}),
                            dict(data,orderCapacity=dict(data['orderCapacity'],entries=[capacity(),capacity()])),
                            dict(data,orderCapacity=dict(data['orderCapacity'],entries=None)),
                            self.data(True),self.data(-1)):
                with self.assertRaises(decoded.DecodeError):decoded.order_capacity('party',invalid)

    def test_packages_99_and_100_share_capacity_without_claiming_100_production(self):
        self.assertEqual(decoded.require_normal_package({'packageVersion': '99'}), '99')
        self.assertEqual(decoded.require_normal_package({'packageVersion': '100'}), '100')
        self.assertEqual(decoded.VERIFIED_CAPACITY_PACKAGES, frozenset({'90', '99', '100'}))
        self.assertEqual(decoded.VERIFIED_NORMAL_PACKAGES, frozenset({'90', '98', '99', '100'}))
        self.assertNotIn('98', decoded.VERIFIED_CAPACITY_PACKAGES)
        with self.assertRaisesRegex(decoded.DecodeError, "101.*unverified"):
            decoded.require_normal_package({'packageVersion': '101'})
        with patch.object(decoded, 'validate'):
            for version in ('99', '100'):
                empty = self.data(0, version)
                party = decoded.order_capacity('party', empty)
                takeout = decoded.order_capacity('takeout', empty)
                self.assertEqual(party['tokenItemId'], 103)
                self.assertEqual(party['remaining'], 0)
                self.assertEqual(takeout['tokenItemId'], 104)
                self.assertEqual(takeout['remaining'], 0)
                self.assertEqual(decoded.order_capacity('party', self.data(6, version))['remaining'], 6)
                bad = self.data(0, version)
                bad['orderCapacity']['entries'][0]['tokenItemId'] = 105
                with self.assertRaisesRegex(decoded.DecodeError, 'identity'):
                    decoded.order_capacity('party', bad)
                limited = self.data(0, version)
                limited['orderCapacity']['entries'][0]['limit'] = 7
                with self.assertRaisesRegex(decoded.DecodeError, 'invalid'):
                    decoded.order_capacity('party', limited)
            with self.assertRaisesRegex(decoded.DecodeError, "98.*unverified"):
                decoded.order_capacity('party', self.data(6, '98'))
            with self.assertRaisesRegex(decoded.DecodeError, 'invalid'):
                decoded.order_capacity('party', self.data(7, '100'))

    def test_zero_and_insufficient_capacity_wait_but_disagreement_is_unknown(self):
        for remaining,required,status in ((0,6,'order_capacity_exhausted'),(1,6,'order_batch_unavailable'),(1,1,'available'),(6,6,'available')):
            current=dict(status='available',reason='visible_order_card',ui_counter=[remaining,6],next_available=None)
            with patch.object(board,'read_availability',return_value=current),patch.object(board,'screenshot'), \
                    patch.object(decoded,'enabled',return_value=True),patch.object(decoded,'order_capacity',return_value=capacity(remaining)):
                result=board.probe_availability(None,None,'party',required_count=required)
            self.assertEqual(result['status'],status)
            self.assertIsNone(result['next_available'])
        current=dict(status='available',reason='visible_order_card',ui_counter=[4,6],next_available=None)
        with patch.object(board,'read_availability',return_value=current),patch.object(board,'screenshot'), \
                patch.object(decoded,'enabled',return_value=True),patch.object(decoded,'order_capacity',return_value=capacity(4)):
            matched=board.probe_availability(None,None,'party',required_count=4)
        self.assertEqual(matched['status'],'available')
        self.assertEqual(matched['capacity']['remaining'],4)
        for counter in (None,[0,6],[6,12],[True,6]):
            current=dict(status='available',ui_counter=counter)
            with patch.object(board,'read_availability',return_value=current),patch.object(board,'screenshot'), \
                    patch.object(decoded,'enabled',return_value=True),patch.object(decoded,'order_capacity',return_value=capacity(6)):
                self.assertEqual(board.probe_availability(None,None,'party',required_count=6)['status'],'board_unknown')

    def _probe(self, counter, required, remaining=0, kind='party', **extra):
        current = dict(status='available', reason='visible_order_card', ui_counter=counter, next_available=None)
        row = capacity(remaining, kind)
        row.update(extra)
        with patch.object(board, 'read_availability', return_value=current), patch.object(board, 'screenshot'), \
                patch.object(decoded, 'enabled', return_value=True), \
                patch.object(decoded, 'order_capacity', return_value=row):
            return board.probe_availability(None, None, kind, required_count=required)

    def test_empty_cache_trusts_a_readable_ui_counter(self):
        for required in range(1, 7):
            result = self._probe([6, 6], required)
            self.assertEqual(result['status'], 'available')
            self.assertEqual(result['reason'], 'visible_order_card')
            self.assertEqual(result['effective_remaining'], 6)
            self.assertEqual(result['capacity_source'], 'ui_counter')
            self.assertEqual(result['capacity']['remaining'], 0)
            self.assertIsNone(result['next_available'])
        takeout = self._probe([6, 6], 6, kind='takeout')
        self.assertEqual(takeout['status'], 'available')
        self.assertEqual(takeout['capacity']['tokenItemId'], 104)
        partial = self._probe([2, 6], 6)
        self.assertEqual(partial['status'], 'order_batch_unavailable')
        self.assertEqual(partial['reason'], 'insufficient_order_tokens_for_requested_batch')
        self.assertEqual(partial['effective_remaining'], 2)
        self.assertEqual(partial['capacity_source'], 'ui_counter')
        self.assertEqual(self._probe([2, 6], 2)['status'], 'available')
        self.assertEqual(self._probe([6, 6], 6, refresh_time=0)['capacity_source'], 'ui_counter')

    def test_empty_cache_zero_ui_stays_exhausted_and_other_disagreements_stop(self):
        exhausted = self._probe([0, 6], 6)
        self.assertEqual(exhausted['status'], 'order_capacity_exhausted')
        self.assertEqual(exhausted['reason'], 'confirmed_zero_order_tokens')
        self.assertNotIn('capacity_source', exhausted)
        self.assertNotIn('effective_remaining', exhausted)
        still = self._probe([0, 6], 6, records=[dict(itemId=103, count=0)])
        self.assertEqual(still['status'], 'order_capacity_exhausted')
        mismatch = self._probe([6, 6], 6, remaining=2)
        self.assertEqual(mismatch['status'], 'board_unknown')
        self.assertEqual(mismatch['reason'], 'capacity_ui_cache_mismatch')
        self.assertNotIn('capacity_source', mismatch)
        self.assertEqual(mismatch['capacity']['remaining'], 2)
        for counter in ([6, 5], [6, 7], [6, 12], None, [6], [True, 6], [7, 6], ['6', 6]):
            stopped = self._probe(counter, 6)
            self.assertEqual(stopped['status'], 'board_unknown', counter)
            self.assertEqual(stopped['reason'], 'capacity_ui_cache_mismatch')
            self.assertNotIn('effective_remaining', stopped)
        clock = self._probe([6, 6], 6, refresh_time=1700000000)
        self.assertEqual(clock['status'], 'board_unknown')
        rows = self._probe([6, 6], 6, records=[dict(itemId=103, count=0)])
        self.assertEqual(rows['status'], 'board_unknown')
        agreed = self._probe([4, 6], 4, remaining=4)
        self.assertEqual(agreed['status'], 'available')
        self.assertEqual(agreed['capacity']['remaining'], 4)
        self.assertNotIn('capacity_source', agreed)

    def test_unverified_package_does_not_trust_the_ui_counter(self):
        current = dict(status='available', reason='visible_order_card', ui_counter=[6, 6], next_available=None)
        cases = (
            dict(packageVersion='98', orderCapacity=dict(complete=False, reason='unverified_package_version',
                 semanticsPackageVersion='98', entries=[])),
            dict(packageVersion='100', orderCapacity=dict(complete=False, reason='incomplete',
                 semanticsPackageVersion='100', entries=[])),
        )
        for data in cases:
            with patch.object(board, 'read_availability', return_value=current), patch.object(board, 'screenshot'), \
                    patch.object(decoded, 'enabled', return_value=True), patch.object(decoded, 'validate'), \
                    patch.object(decoded, 'snapshot', return_value=data):
                with self.assertRaises(decoded.DecodeError):
                    board.probe_availability(None, None, 'party', required_count=6)

    def test_waiting_capacity_stops_scanner_before_another_pass(self):
        for status in ('order_capacity_exhausted','order_batch_unavailable'):
            with patch.object(cycle,'run_script') as run,patch.object(cycle,'read_json',return_value={'status':status}):
                self.assertEqual(cycle.scan_for_target(6,'party')['status'],status)
                self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
