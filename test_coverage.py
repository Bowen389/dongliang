import unittest
import pandas as pd
from scan import candidates

class Coverage(unittest.TestCase):
    def frame(self):
        days=pd.bdate_range(end='2026-11-19',periods=61).strftime('%Y-%m-%d').tolist()
        return pd.DataFrame(dict(date=days,code='600001',name='样本',open=10.,close=10.,prev_close=10.,volume=100.,amount=300_000_000.)),days
    def test_zero_volume_is_rule_exclusion(self):
        frame,days=self.frame()
        frame.loc[0,'volume']=0
        _,coverage=candidates(frame,days,['600001'])
        self.assertEqual(coverage['excluded_inactive_history'],1)
        self.assertEqual(coverage['missing_history'],0)
    def test_absent_bar_remains_unverified(self):
        frame,days=self.frame()
        _,coverage=candidates(frame.drop(0),days,['600001'])
        self.assertEqual(coverage['missing_history'],1)
        self.assertEqual(coverage['missing_history_details'][0]['dates'],[days[0]])

if __name__=='__main__':unittest.main()
