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
            for invalid in ({},dict(data,packageVersion='91'),dict(data,packageVersion='98'),
                            dict(data,packageVersion='99'),dict(data,packageVersion='100'),
                            dict(data,orderCapacity={'complete':False, 'reason':'unverified_package_version'}),
                            dict(data,orderCapacity=dict(data['orderCapacity'],entries=[capacity(),capacity()])),
                            dict(data,orderCapacity=dict(data['orderCapacity'],entries=None)),
                            self.data(True),self.data(-1)):
                with self.assertRaises(decoded.DecodeError):decoded.order_capacity('party',invalid)

    def test_package_99_allows_normal_orders_and_verified_party_takeout_capacity(self):
        self.assertEqual(decoded.require_normal_package({'packageVersion': '99'}), '99')
        self.assertEqual(decoded.VERIFIED_CAPACITY_PACKAGES, frozenset({'90', '99'}))
        self.assertIn('98', decoded.VERIFIED_NORMAL_PACKAGES)
        self.assertNotIn('98', decoded.VERIFIED_CAPACITY_PACKAGES)
        with self.assertRaisesRegex(decoded.DecodeError, "100.*unverified"):
            decoded.require_normal_package({'packageVersion': '100'})
        data = self.data(6, '99')
        with patch.object(decoded, 'validate'):
            party = decoded.order_capacity('party', data)
            takeout = decoded.order_capacity('takeout', data)
            self.assertEqual(party['tokenItemId'], 103)
            self.assertEqual(party['remaining'], 6)
            self.assertEqual(takeout['tokenItemId'], 104)
            self.assertEqual(takeout['remaining'], 0)
            with self.assertRaisesRegex(decoded.DecodeError, "98.*unverified"):
                decoded.order_capacity('party', self.data(6, '98'))

    def test_zero_and_insufficient_capacity_wait_but_disagreement_is_unknown(self):
        for remaining,required,status in ((0,6,'order_capacity_exhausted'),(1,6,'order_batch_unavailable'),(1,1,'available'),(6,6,'available')):
            current=dict(status='available',reason='visible_order_card',ui_counter=[remaining,6],next_available=None)
            with patch.object(board,'read_availability',return_value=current),patch.object(board,'screenshot'), \
                    patch.object(decoded,'enabled',return_value=True),patch.object(decoded,'order_capacity',return_value=capacity(remaining)):
                result=board.probe_availability(None,None,'party',required_count=required)
            self.assertEqual(result['status'],status)
            self.assertIsNone(result['next_available'])
        for counter in (None,[0,6],[6,12],[True,6]):
            current=dict(status='available',ui_counter=counter)
            with patch.object(board,'read_availability',return_value=current),patch.object(board,'screenshot'), \
                    patch.object(decoded,'enabled',return_value=True),patch.object(decoded,'order_capacity',return_value=capacity(6)):
                self.assertEqual(board.probe_availability(None,None,'party',required_count=6)['status'],'board_unknown')

    def test_waiting_capacity_stops_scanner_before_another_pass(self):
        for status in ('order_capacity_exhausted','order_batch_unavailable'):
            with patch.object(cycle,'run_script') as run,patch.object(cycle,'read_json',return_value={'status':status}):
                self.assertEqual(cycle.scan_for_target(6,'party')['status'],status)
                self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
