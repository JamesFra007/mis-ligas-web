from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import db
from app.leagues import LEAGUES
from app.main import app, select_games
from app.rounds import round_id, round_options
from test_core import game


class MatchRoundTests(unittest.TestCase):
    def test_official_current_spanish_calendar_has_38_complete_matchdays(self):
        root=Path(__file__).resolve().parents[1]
        archive=json.loads((root/'data/laliga_2026_27_rounds.json').read_text())
        records=archive['matches']
        self.assertEqual((archive['league'],archive['season']),('spain','2026-27'))
        self.assertEqual(len(records),380)
        self.assertEqual(len({(g['home'],g['away']) for g in records}),380)
        teams={g[k] for g in records for k in ('home','away')}
        self.assertEqual(len(teams),20)
        for number in range(1,39):
            matches=[g for g in records if g['round']==number]
            self.assertEqual(len(matches),10)
            self.assertEqual(len({g[k] for g in matches for k in ('home','away')}),20)
        current=json.loads((root/'data/current.json').read_text())['games']
        indexed={(g['home'],g['away']):g for g in current if g['league']=='spain' and g['season']=='2026-27'}
        for match in records:
            stored=indexed[(match['home'],match['away'])]
            self.assertEqual(stored['round'],match['round'])
            self.assertEqual(stored['scope'],'first' if match['round']<=19 else 'second')

    def test_drought_metrics_apply_to_the_season_and_card_leagues(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed)
                rows=[game(id=f'{league}:one',provider_id=f'{league}:one',league=league,season='2025' if league=='colombia' else '2025-26',ht_home=0,ht_away=0,ft_home=0,ft_away=0,red=0) for league in LEAGUES]
                db.upsert(rows)
                with TestClient(app) as client:
                    for row in rows:
                        query={'league':row['league'],'season':row['season']}
                        data=client.get('/api/dashboard',params=query).json()
                        chronological=client.get('/api/dashboard',params={**query,'order':'chronological','match_round':'auto'}).json()
                        self.assertEqual(data['droughts'],chronological['droughts'])
                        self.assertEqual(data['droughts']['ht'],data['ht_droughts'])
                        self.assertEqual(data['droughts']['ht']['current']['count'],1)
                        self.assertEqual(data['droughts']['ft']['current']['count'],1)
                        if LEAGUES[row['league']]['cards']:
                            self.assertEqual(data['droughts']['red']['current']['count'],1)
                        else:
                            self.assertIsNone(data['droughts']['red'])

    def test_real_groups_disambiguate_tournaments_phases_and_pending_rounds(self):
        rows = [
            game(id='regular',scope='Apertura',phase='Fase regular',round=1,source_order=1),
            game(id='group',scope='Apertura',phase='Cuadrangulares',round=1,source_order=2),
            game(id='final',scope='Apertura',phase='Final',round=1,source_order=3),
            game(id='second',scope='Finalización',phase='Fase regular',round=1,source_order=4),
            game(id='pending',scope='Finalización',phase='Final',round=None,source_order=5),
        ]
        options=round_options(list(reversed(rows)))
        self.assertEqual([item['id'] for item in options], [round_id(g) for g in rows])
        self.assertEqual(options[0]['label'],'Apertura · Fase regular · Jornada 1')
        self.assertEqual(options[1]['label'],'Apertura · Cuadrangulares · Jornada 1')
        self.assertEqual(options[-1]['label'],'Finalización · Final · Jornada pendiente')
        self.assertEqual(len({item['id'] for item in options}),5)
        self.assertEqual(json.loads(round_id(game(scope='all',phase='NB I',round=0))),['all','NB I',0])

    def test_table_round_keeps_season_summary_droughts_and_stable_dropdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed)
                rows=[
                    game(id='a1',provider_id='a1',league='colombia',season='2025',scope='Apertura',phase='Fase regular',round=1,date='2025-02-01',source_order=1),
                    game(id='a2',provider_id='a2',league='colombia',season='2025',scope='Apertura',phase='Fase regular',round=2,date='2025-01-01',source_order=2,ht_home=0),
                    game(id='f1',provider_id='f1',league='colombia',season='2025',scope='Finalización',phase='Fase regular',round=1,date='2025-08-01',source_order=3,ht_home=0),
                    game(id='f1b',provider_id='f1b',league='colombia',season='2025',scope='Finalización',phase='Fase regular',round=1,date='2025-08-02',source_order=4),
                ]
                db.upsert(rows)
                with TestClient(app) as client:
                    query={'league':'colombia','season':'2025'}
                    original=client.get('/api/dashboard',params=query).json()
                    chosen=client.get('/api/dashboard',params={**query,'match_round':round_id(rows[2])}).json()
                    self.assertEqual([g['id'] for g in chosen['games']],['f1','f1b'])
                    self.assertEqual(chosen['pagination']['total'],2)
                    self.assertEqual(chosen['summary'],original['summary'])
                    self.assertEqual(chosen['ht_droughts'],original['ht_droughts'])
                    self.assertEqual(chosen['options']['selected_round'],round_id(rows[2]))
                    chronological=client.get('/api/dashboard',params={**query,'order':'chronological'}).json()
                    self.assertEqual(chronological['options']['rounds'],original['options']['rounds'])
                    self.assertEqual([g['id'] for g in chronological['games']],['a2','a1','f1','f1b'])
                    for selected in ('auto','stale-value'):
                        fallback=client.get('/api/dashboard',params={**query,'match_round':selected}).json()
                        self.assertEqual([g['id'] for g in fallback['games']],['a1'])
                        self.assertEqual(fallback['options']['selected_round'],round_id(rows[0]))
                    scope=client.get('/api/dashboard',params={**query,'scope':'Finalización','match_round':'auto'}).json()
                    self.assertEqual(len(scope['options']['rounds']),1)
                    self.assertEqual(scope['summary']['total'],2)
                    empty=client.get('/api/dashboard',params={**query,'search':'no match','match_round':'auto'}).json()
                    self.assertEqual(empty['options']['rounds'],[])
                    self.assertEqual(empty['options']['selected_round'],'')
                    self.assertEqual(empty['pagination']['total'],0)

    def test_round_dropdown_keeps_original_order_when_source_positions_tie(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed)
                rows=[game(id='a',provider_id='a',round=2,time='15:00',source_order=7),game(id='b',provider_id='b',home='Celta',round=1,time='13:00',source_order=7)]
                db.upsert(rows)
                original,original_options=select_games('spain','2025-26')
                calendar,calendar_options=select_games('spain','2025-26',order='chronological')
                self.assertEqual([g['id'] for g in original],['a','b'])
                self.assertEqual([g['id'] for g in calendar],['b','a'])
                self.assertEqual(original_options['rounds'],calendar_options['rounds'])
                self.assertEqual(original_options['rounds'][0]['id'],round_id(rows[0]))

    def test_export_matches_round_and_includes_all_selected_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed)
                first=game(id='round1',provider_id='round1',round=1)
                second=game(id='round2',provider_id='round2',round=2,home='Celta')
                db.upsert([first,second])
                with TestClient(app) as client:
                    response=client.get('/api/export.xlsx',params={'league':'spain','season':'2025-26','match_round':round_id(second)})
                book=load_workbook(BytesIO(response.content));ws=book['Partidos']
                self.assertEqual(ws.max_row,8)
                self.assertEqual(ws['C8'].value,2)
                self.assertEqual(ws['D8'].value,'Celta')
                self.assertIn('Jornada 2',ws['A5'].value)


if __name__=='__main__':unittest.main()
