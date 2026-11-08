from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from app import db
from app.main import app,check,current_season
from app.stats import summarize
from test_core import game


def hungarian(**overrides):
    row=game(id='hungary:mlsz:1',provider_id='mlsz:1',league='hungary',season='2025-26',season_label='2025/26',home='Ferencváros',away='Újpest',round=24,scope='all',phase='NB I',corners=None,yellow=None,red=None,yellow_home=None,yellow_away=None,red_home=None,red_away=None)
    return {**row,**overrides}


class HungarianCoverageTests(unittest.TestCase):
    def test_requested_fields_are_not_reported_as_missing(self):
        s=summarize([hungarian()])['summary']
        self.assertEqual(s['finished'],1);self.assertEqual(s['ht_cases'],1)
        self.assertEqual(s['unresolved'],0);self.assertEqual(s['corners_missing'],0)
        self.assertIsNone(s['corners_average']);self.assertIsNone(s['yellow_average'])
        unknown=summarize([hungarian(ht_home=None,ht_away=None)])['summary']
        self.assertEqual(unknown['unresolved'],1);self.assertEqual(unknown['ht_base'],0)

    def test_awarded_game_and_future_are_not_played_ht_events(self):
        s=summarize([hungarian(),hungarian(id='admin',administrative=True,ht_home=None,ht_away=None,ft_home=3,ft_away=0),hungarian(id='future',status='scheduled',ht_home=None,ht_away=None,ft_home=None,ft_away=None)])['summary']
        self.assertEqual((s['total'],s['finished'],s['scheduled'],s['excluded']),(3,1,1,1))
        self.assertEqual((s['ht_base'],s['ht_cases']),(1,1))

    def test_provisional_fixture_becomes_report_without_merging_replays(self):
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed)
                key='mlsz:67:33586:14:1:2'
                scheduled=hungarian(id='hungary:fixture:67:33586:14:1:2',provider_id='fixture:67:33586:14:1:2',fixture_key=key,status='scheduled',ht_home=None,ht_away=None,ft_home=None,ft_away=None)
                db.upsert([scheduled])
                report=hungarian(id='hungary:mlsz:100',provider_id='mlsz:100',fixture_key=key)
                db.upsert([report]);stored=db.games('hungary','2025-26')
                self.assertEqual(len(stored),1);self.assertEqual(stored[0]['id'],scheduled['id']);self.assertEqual(stored[0]['provider_id'],'mlsz:100')
                db.upsert([scheduled]);stored=db.games('hungary','2025-26')
                self.assertEqual(stored[0]['status'],'finished');self.assertEqual(stored[0]['provider_id'],'mlsz:100')
                abandoned=hungarian(id='hungary:mlsz:101',provider_id='mlsz:101',fixture_key=key,status='cancelled',administrative=True,ht_home=None,ht_away=None,ft_home=None,ft_away=None)
                db.upsert([abandoned]);self.assertEqual(len(db.games('hungary','2025-26')),2)
                replay=hungarian(id='hungary:mlsz:102',provider_id='mlsz:102',fixture_key=key)
                db.upsert([replay]);self.assertEqual(len(db.games('hungary','2025-26')),3)

    def test_new_league_calendar_style_and_excel(self):
        check('hungary','2018-19');self.assertRegex(current_season('hungary'),r'^20\d\d-\d\d$')
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed);db.upsert([hungarian(),hungarian(id='hungary:mlsz:2',provider_id='mlsz:2',home='Paks',round=33,date='2025-08-01')])
                with TestClient(app) as client:
                    meta=client.get('/api/meta').json();league=next(x for x in meta['leagues'] if x['id']=='hungary')
                    self.assertFalse(league['cards']);self.assertFalse(league['corners']);self.assertEqual(league['flag'],'🇭🇺')
                    data=client.get('/api/dashboard?league=hungary&season=2025-26&order=chronological').json()
                    self.assertEqual([g['home'] for g in data['games']],['Paks','Ferencváros'])
                    self.assertEqual(data['options']['scopes'],[{'id':'all','label':'Temporada completa'}])
                    self.assertEqual(data['summary']['unresolved'],0)
                    response=client.get('/api/export.xlsx?league=hungary&season=2025-26')
                    self.assertEqual(response.status_code,200)
                    wb=load_workbook(BytesIO(response.content));ws=wb['Partidos']
                    headers=[c.value for c in ws[7]]
                    self.assertNotIn('Córners',headers);self.assertNotIn('Amarillas',headers);self.assertNotIn('Rojas',headers)
                    self.assertEqual(headers[:7],['N.º','Fecha · hora (COL)','Jornada','Local','Visitante','HT','FT'])
                    self.assertEqual(ws['G8'].fill.fgColor.rgb,'00C6EFCE')
                    self.assertNotEqual(ws['H8'].fill.fgColor.rgb,'00C6EFCE')
                    self.assertIsNone(ws.freeze_panes);self.assertFalse(ws._images)
                    self.assertEqual(client.get('/api/forecast?league=hungary').json()['status'],'insufficient_history')
                    self.assertEqual(client.get('/api/dashboard?league=hungary&season=2025').status_code,400)
                    self.assertEqual(client.get('/api/dashboard?league=hungary&season=2025-99').status_code,400)


if __name__=='__main__':unittest.main()
