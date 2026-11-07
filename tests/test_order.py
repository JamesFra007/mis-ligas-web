from copy import deepcopy
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from app import db
from app.main import app
from app.order import order_games
from test_core import game


class OrderTests(unittest.TestCase):
    def test_original_stays_and_calendar_sorts_postponed_dates(self):
        rows=[game(id='first',date='2025-10-10',source_order=1),game(id='second',date='2025-08-01',source_order=2),game(id='third',date='2025-08-01',time=None,source_order=3),game(id='unknown',date='2025-01-01',date_confirmed=False,source_order=4)]
        self.assertEqual([g['id'] for g in order_games(rows)],['first','second','third','unknown'])
        self.assertEqual([g['id'] for g in order_games(rows,'chronological')],['second','third','first','unknown'])
        self.assertEqual([g['id'] for g in rows],['first','second','third','unknown'])
        with self.assertRaises(ValueError):order_games(rows,'anything')

    def test_withdrawn_future_time_does_not_survive_but_finished_time_is_preserved(self):
        with tempfile.TemporaryDirectory() as path:
            base=Path(path);seed=base/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',base),patch.object(db,'DB_PATH',base/'test.sqlite3'):
                db.initialize(seed)
                a=game(status='scheduled',ht_home=None,ht_away=None,ft_home=None,ft_away=None,kickoff='2025-09-01T19:00:00Z',time_confirmed=True,date_confirmed=True)
                db.upsert([a]);db.upsert([{**a,'time':None,'kickoff':None,'time_confirmed':False,'date_confirmed':False}])
                row=db.games('spain','2025-26')[0]
                self.assertIsNone(row['time']);self.assertIsNone(row['kickoff']);self.assertFalse(row['time_confirmed'])
                b=game(id='finished',provider_id='2',home='Celta',status='finished',time='14:00',kickoff='2025-09-01T19:00:00Z',time_confirmed=True,date_confirmed=True)
                db.upsert([b]);db.upsert([{**b,'time':None,'date':'2025-09-02','date_confirmed':False,'time_confirmed':False}])
                row=next(r for r in db.games('spain','2025-26') if r['id']=='finished')
                self.assertEqual(row['time'],'14:00');self.assertEqual(row['date'],'2025-09-01');self.assertTrue(row['time_confirmed'])

    def test_api_sort_export_and_no_future_contamination(self):
        with tempfile.TemporaryDirectory() as path:
            base=Path(path);seed=base/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',base),patch.object(db,'DB_PATH',base/'test.sqlite3'):
                db.initialize(seed);db.upsert([game(date='2025-10-10'),game(id='second',provider_id='2',home='Celta',date='2025-08-01')])
                with TestClient(app) as client:
                    result=client.get('/api/dashboard?league=spain&season=2025-26&order=chronological').json()
                    self.assertEqual([g['id'] for g in result['games']],['second','test:1'])
                    self.assertEqual(client.get('/api/dashboard?order=arbitrary').status_code,400)
                    exported=client.get('/api/export.xlsx?league=spain&season=2025-26&order=chronological')
                    book=load_workbook(BytesIO(exported.content));self.assertEqual(book['Partidos']['D8'].value,'Celta')
                    forecast=client.get('/api/forecast?league=spain').json()
                    self.assertEqual(forecast['status'],'insufficient_history');self.assertIsNone(forecast['baseline'])
                    self.assertEqual(client.get('/api/forecast?league=other').status_code,400)


if __name__=='__main__':unittest.main()
