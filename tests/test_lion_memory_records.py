import struct
import unittest

from tools.read_only_memory import Memory


class LionMemoryRecordTests(unittest.TestCase):
    def record(self,item,extra):
        memory=object.__new__(Memory)
        memory.cached=lambda *_:struct.pack('<4I',12345*2,item*2,2,1)
        memory.string=lambda *_:extra
        return memory.record(0x100000020)

    def test_only_explicit_season_and_counter_record_shapes_are_read(self):
        self.assertEqual(self.record(3921,'season,126;e_time,1000;status,1')['itemId'],3921)
        self.assertEqual(self.record(3972,'r_id,7;r_c,2;exp,164')['itemId'],3972)
        for item,extra in ((3921,'lv,4'),(3972,'r_id,7;exp,164'),(3972,'r_id,7;r_c,bad;exp,164'),(3973,'r_id,7;r_c,2;exp,164')):
            with self.subTest(item=item,extra=extra),self.assertRaises(ValueError):self.record(item,extra)
