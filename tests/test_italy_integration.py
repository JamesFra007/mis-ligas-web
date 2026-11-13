"""Italy exposes the same scored-match and card behavior as the Spanish league."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import db
from app.leagues import LEAGUES
from app.main import app, check
from app.rounds import round_id
from test_core import game


def italian(**overrides):
    row=game(id='italy:fd:2526:Inter:Milan',provider_id='fd:2526:Inter:Milan',
                league='italy',season='2025-26',season_label='2025/26',
                home='Inter',away='Milan',round=3,scope='first',phase='Serie A',
                date='2025-09-01',time='13:45',kickoff='2025-09-01T18:45:00+00:00',
                date_confirmed=True,time_confirmed=True,
                corners=8,yellow=5,yellow_home=2,yellow_away=3,
                red=0,red_home=0,red_away=0)
    return {**row,**overrides}


class ItalyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name);seed=root/'seed.json';seed.write_text('[]')
        self.data_patch=patch.object(db,'DATA',root)
        self.path_patch=patch.object(db,'DB_PATH',root/'test.sqlite3')
        self.data_patch.start();self.path_patch.start()
        db.initialize(seed)

    def tearDown(self):
        self.path_patch.stop();self.data_patch.stop();self.temp.cleanup()

    def test_metadata_scopes_and_real_missing_card_data_are_explicit(self):
        check('italy','2018-19')
        complete=italian()
        missing=italian(id='italy:unknown',provider_id='unknown',home='Roma',away='Lazio',
                        date='2025-10-01',round=20,scope='second',
                        ht_home=None,ht_away=None,corners=None,yellow=None,red=None,
                        yellow_home=None,yellow_away=None,red_home=None,red_away=None)
        future=italian(id='italy:future',provider_id='future',home='Napoli',away='Roma',
                       status='scheduled',round=21,scope='second',
                       ht_home=None,ht_away=None,ft_home=None,ft_away=None,
                       corners=None,yellow=None,red=None)
        db.upsert([complete,missing,future])
        with TestClient(app) as client:
            meta=client.get('/api/meta').json()
            self.assertEqual(len(meta['leagues']),len(LEAGUES))
            league=next(x for x in meta['leagues'] if x['id']=='italy')
            self.assertEqual((league['name'],league['country'],league['flag']),('Serie A','Italia','🇮🇹'))
            self.assertTrue(league['cards']);self.assertTrue(league['corners']);self.assertTrue(league['split_season'])
            data=client.get('/api/dashboard?league=italy&season=2025-26').json()
            self.assertEqual(data['options']['scopes'],[{'id':'all','label':'Temporada completa'},{'id':'first','label':'Primera vuelta'},{'id':'second','label':'Segunda vuelta'}])
            s=data['summary']
            self.assertEqual((s['finished'],s['ht_base'],s['red_base'],s['red_matches'],s['red_total']),(2,1,1,0,0))
            self.assertEqual((s['red_average'],s['yellow_total'],s['corners_missing']),(0,5,1))
            self.assertEqual(s['unresolved'],1)
            self.assertIsNotNone(data['droughts']['red'])
            self.assertEqual(data['droughts']['red']['missing_data'],1)
            self.assertEqual(client.get('/api/dashboard?league=italy&season=2025').status_code,400)
            self.assertEqual(client.get('/api/dashboard?league=italy&season=2025-99').status_code,400)

    def test_matchday_filter_keeps_season_analysis_and_calendar_uses_dates(self):
        first=italian(date='2025-10-01',round=4)
        early=italian(id='italy:early',provider_id='early',home='Napoli',away='Roma',
                      date='2025-08-01',round=1,red=1,red_home=1,red_away=0)
        db.upsert([first,early])
        with TestClient(app) as client:
            query={'league':'italy','season':'2025-26'}
            original=client.get('/api/dashboard',params=query).json()
            calendar=client.get('/api/dashboard',params={**query,'order':'chronological'}).json()
            chosen=client.get('/api/dashboard',params={**query,'match_round':round_id(early)}).json()
            self.assertEqual([g['id'] for g in calendar['games']],[early['id'],first['id']])
            self.assertEqual([g['id'] for g in chosen['games']],[early['id']])
            self.assertEqual(chosen['summary'],original['summary'])
            self.assertEqual(chosen['droughts'],calendar['droughts'])
            self.assertEqual(chosen['summary']['red_match_rate'],0.5)

    def test_export_keeps_corners_before_cards_and_zero_red_is_blank(self):
        first=italian()
        second=italian(id='italy:red',provider_id='red',home='Napoli',away='Roma',
                       date='2025-09-02',round=4,ht_home=0,ht_away=1,
                       red=2,red_home=1,red_away=1)
        missing=italian(id='italy:pending',provider_id='pending',home='Lazio',away='Roma',
                        date='2025-09-03',round=5,red=None,yellow=None,corners=None)
        db.upsert([first,second,missing])
        with TestClient(app) as client:
            response=client.get('/api/export.xlsx?league=italy&season=2025-26')
            self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content));ws=book['Partidos']
        self.assertEqual([c.value for c in ws[7]][:10],['N.º','Fecha · hora (COL)','Jornada','Local','Visitante','HT','FT','Córners','Amarillas','Rojas'])
        self.assertEqual((ws['H8'].value,ws['I8'].value,ws['J8'].value),(8,5,None))
        self.assertIsNone(ws['J8'].fill.patternType)
        self.assertEqual(ws['J9'].value,2)
        self.assertEqual(ws['J10'].value,'N/D')
        self.assertEqual(ws['F8'].data_type,'s');self.assertEqual(ws['F8'].value,'1-1')
        for col in range(1,9):
            self.assertEqual(ws.cell(8,col).fill.fgColor.rgb,'00C6EFCE')
        self.assertNotEqual(ws['I8'].fill.fgColor.rgb,'00C6EFCE')
        self.assertEqual(len(ws._images),3)
        anchors=[(image.anchor._from.row,image.anchor._from.col) for image in ws._images]
        self.assertNotIn((7,9),anchors)
        self.assertIn((8,9),anchors)
        self.assertTrue(all(sheet.freeze_panes is None for sheet in book))

    def test_fd_and_espn_identifiers_reconcile_without_duplicate_fixture(self):
        source=italian(id='italy:espn:1',provider_id='espn:1')
        db.upsert([source])
        db.upsert([italian(ht_home=None,ht_away=None,round=None,scope='all',corners=None,yellow=None,red=None)])
        rows=db.games('italy','2025-26')
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['ht_home'],rows[0]['round'],rows[0]['corners'],rows[0]['red']),(1,3,8,0))
        playoff=italian(id='italy:playoff:1',provider_id='playoff:1',phase='Desempate por la permanencia',round=None,date='2026-06-01')
        db.upsert([playoff])
        self.assertEqual(len(db.games('italy','2025-26')),2)

    def test_csv_on_field_score_cannot_replace_an_awarded_result(self):
        awarded=italian(administrative=True,ht_home=None,ht_away=None,ft_home=3,ft_away=0)
        db.upsert([awarded])
        db.upsert([italian(administrative=False,ht_home=0,ht_away=0,ft_home=0,ft_away=0)])
        stored=db.games('italy','2025-26')[0]
        self.assertTrue(stored['administrative'])
        self.assertEqual((stored['ft_home'],stored['ft_away']),(3,0))
        self.assertIsNone(stored['ht_home']);self.assertIsNone(stored['ht_away'])


if __name__=='__main__':
    unittest.main()
