"""Paraguay keeps match and corner coverage while excluding card statistics."""
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


def paraguayan(**overrides):
    row=game(id='paraguay:espn:1',provider_id='espn:1',league='paraguay',
             season='2025',season_label='2025',home='Olimpia',away='Cerro Porteño',
             date='2025-02-01',time='18:00',kickoff='2025-02-01T23:00:00+00:00',
             date_confirmed=True,time_confirmed=True,scope='Apertura',phase='Fase regular',
             round=1,corners=0,yellow=None,red=None,yellow_home=None,yellow_away=None,
             red_home=None,red_away=None)
    return {**row,**overrides}


class ParaguayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        seed=root/'seed.json';seed.write_text('[]')
        self.data_patch=patch.object(db,'DATA',root)
        self.path_patch=patch.object(db,'DB_PATH',root/'test.sqlite3')
        self.data_patch.start();self.path_patch.start();db.initialize(seed)

    def tearDown(self):
        self.path_patch.stop();self.data_patch.stop();self.temp.cleanup()

    def test_calendar_year_capabilities_and_unknown_corners_are_explicit(self):
        check('paraguay','2018')
        zero=paraguayan()
        pending=paraguayan(id='paraguay:espn:2',provider_id='espn:2',home='Libertad',
                          away='Nacional',date='2025-08-01',scope='Clausura',corners=None,
                          ht_home=0,ht_away=1)
        future=paraguayan(id='paraguay:espn:3',provider_id='espn:3',home='Guaraní',
                         away='Libertad',date='2025-08-02',scope='Clausura',status='scheduled',
                         ht_home=None,ht_away=None,ft_home=None,ft_away=None,corners=None)
        db.upsert([zero,pending,future])
        with TestClient(app) as client:
            meta=client.get('/api/meta').json()
            self.assertEqual(len(meta['leagues']),len(LEAGUES))
            league=next(item for item in meta['leagues'] if item['id']=='paraguay')
            self.assertEqual((league['country'],league['flag']),('Paraguay','🇵🇾'))
            self.assertFalse(league['split_season']);self.assertFalse(league['cards'])
            self.assertTrue(league['corners'])
            data=client.get('/api/dashboard?league=paraguay&season=2025').json()
            self.assertEqual(data['options']['scopes'],[
                {'id':'all','label':'Temporada completa'},
                {'id':'Apertura','label':'Apertura'},{'id':'Clausura','label':'Clausura'}])
            summary=data['summary']
            self.assertEqual((summary['total'],summary['finished'],summary['scheduled']),(3,2,1))
            self.assertEqual((summary['corners_base'],summary['corners_average'],summary['corners_missing']),(1,0,1))
            self.assertEqual(summary['unresolved'],1)
            self.assertEqual((summary['yellow_base'],summary['red_base']),(0,0))
            self.assertIsNone(summary['yellow_total']);self.assertIsNone(summary['red_total'])
            self.assertIsNone(data['droughts']['red'])
            self.assertEqual(client.get('/api/dashboard?league=paraguay&season=2025-26').status_code,400)

    def test_repeated_pair_and_round_numbers_remain_distinct_between_tournaments(self):
        first=paraguayan(date='2025-07-01',corners=11)
        second=paraguayan(id='paraguay:espn:2',provider_id='espn:2',scope='Clausura',
                         date='2025-02-01',corners=7,ht_home=0,ht_away=1)
        db.upsert([first,second])
        db.upsert([{**second,'corners':None,'ht_home':None,'ht_away':None}])
        self.assertEqual(len(db.games('paraguay','2025')),2)
        with TestClient(app) as client:
            params={'league':'paraguay','season':'2025'}
            all_data=client.get('/api/dashboard',params=params).json()
            chosen=client.get('/api/dashboard',params={**params,'match_round':round_id(second)}).json()
            calendar=client.get('/api/dashboard',params={**params,'order':'chronological'}).json()
            self.assertEqual([g['id'] for g in chosen['games']],[second['id']])
            self.assertEqual((chosen['games'][0]['corners'],chosen['games'][0]['ht_home']),(7,0))
            self.assertEqual(chosen['summary'],all_data['summary'])
            self.assertEqual(chosen['droughts'],calendar['droughts'])
            self.assertEqual([g['id'] for g in calendar['games']],[second['id'],first['id']])
            self.assertEqual(len(all_data['options']['rounds']),2)
            self.assertTrue(all('Jornada 1' in item['label'] for item in all_data['options']['rounds']))

    def test_2020_finals_keep_phase_and_do_not_create_an_extra_matchday(self):
        rows=[paraguayan(season='2020',season_label='2020',date='2020-10-01',scope='Clausura',round=11)]
        for n,phase in enumerate(('Cuartos de final','Semifinal','Final'),2):
            rows.append(paraguayan(id=f'paraguay:espn:{n}',provider_id=f'espn:{n}',
                                  season='2020',season_label='2020',date=f'2020-12-{n:02d}',
                                  scope='Clausura',phase=phase,round=None,round_not_applicable=True))
        db.upsert(rows)
        with TestClient(app) as client:
            original=client.get('/api/dashboard?league=paraguay&season=2020').json()
            for match in rows[1:]:
                selected=client.get('/api/dashboard',params={'league':'paraguay','season':'2020','match_round':round_id(match)}).json()
                self.assertEqual([g['id'] for g in selected['games']],[match['id']])
                self.assertEqual(selected['summary'],original['summary'])
                self.assertNotIn('pendiente',next(item['label'] for item in selected['options']['rounds'] if item['id']==round_id(match)).lower())
            self.assertEqual(len(original['options']['rounds']),4)

    def test_export_has_corners_after_ft_and_never_card_columns_or_icons(self):
        zero=paraguayan()
        missing=paraguayan(id='paraguay:espn:2',provider_id='espn:2',home='Libertad',away='Nacional',
                          date='2025-02-02',corners=None,ht_home=0)
        db.upsert([zero,missing])
        with TestClient(app) as client:
            response=client.get('/api/export.xlsx?league=paraguay&season=2025')
            self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content));ws=book['Partidos']
        self.assertEqual([c.value for c in ws[7]][:8],['N.º','Fecha · hora (COL)','Jornada','Local','Visitante','HT','FT','Córners'])
        self.assertFalse(any(c.value in ('Amarillas','Rojas') for c in ws[7]))
        self.assertEqual((ws['H8'].value,ws['H9'].value),(0,'N/D'))
        self.assertEqual(ws['F8'].value,'1-1');self.assertEqual(ws['F8'].data_type,'s')
        self.assertEqual(len(ws._images),0)
        self.assertTrue(all(ws.cell(8,col).fill.fgColor.rgb=='00C6EFCE' for col in range(1,9)))
        self.assertNotEqual(ws['I8'].fill.fgColor.rgb,'00C6EFCE')
        self.assertTrue(all(sheet.freeze_panes is None for sheet in book))

    def test_administrative_correction_removes_placeholder_half_time(self):
        original=paraguayan()
        db.upsert([original])
        db.upsert([{**original,'administrative':True,'ht_home':None,'ht_away':None,'ft_home':3,'ft_away':0}])
        saved=db.games('paraguay','2025')[0]
        self.assertTrue(saved['administrative'])
        self.assertIsNone(saved['ht_home']);self.assertIsNone(saved['ht_away'])
        self.assertEqual((saved['ft_home'],saved['ft_away']),(3,0))


if __name__=='__main__':unittest.main()
