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
from app.main import app,check
from app.stats import summarize
from scripts.export_hungary_history import export_history
from scripts.publish_snapshot import current_seasons, refresh_sources
from test_core import game


def polish(**overrides):
    return {**game(id='poland:be:1',provider_id='be:1',league='poland',season='2018-19',season_label='2018/19',home='Legia Warszawa',away='Lech Poznań',round=31,scope='Grupo por el título',phase='Grupo por el título',corners=None,yellow=None,red=None,yellow_home=None,yellow_away=None,red_home=None,red_away=None),**overrides}


class PolandIntegrationTests(unittest.TestCase):
    def test_ht_ft_scope_does_not_create_missing_statistics(self):
        s=summarize([polish()])['summary'];self.assertEqual(s['unresolved'],0);self.assertEqual(s['corners_missing'],0);self.assertEqual(s['ht_cases'],1)
        self.assertIsNone(s['corners_average']);self.assertIsNone(s['red_average'])
        s=summarize([polish(administrative=True,ht_home=None,ht_away=None,ft_home=3,ft_away=0)])['summary']
        self.assertEqual((s['excluded'],s['ht_base'],s['ft_base']),(1,0,0))
        self.assertIn('poland',current_seasons());check('poland','2018-19')

    def test_fourth_league_filters_and_excel_have_only_requested_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed);db.upsert([polish()])
                with TestClient(app) as client:
                    meta=client.get('/api/meta').json();self.assertEqual(len(meta['leagues']),len(LEAGUES))
                    lg=next(x for x in meta['leagues'] if x['id']=='poland');self.assertFalse(lg['cards']);self.assertFalse(lg['corners']);self.assertEqual(lg['flag'],'🇵🇱')
                    data=client.get('/api/dashboard?league=poland&season=2018-19').json();self.assertEqual(data['summary']['finished'],1)
                    self.assertIn({'id':'Grupo por el título','label':'Grupo por el título'},data['options']['scopes'])
                    response=client.get('/api/export.xlsx?league=poland&season=2018-19')
                    ws=load_workbook(BytesIO(response.content))['Partidos'];self.assertEqual([c.value for c in ws[7]][:7],['N.º','Fecha · hora (COL)','Jornada','Local','Visitante','HT','FT'])
                    self.assertFalse(any(c.value in ('Córners','Amarillas','Rojas') for c in ws[7]));self.assertIsNone(ws.freeze_panes)
                    self.assertEqual(ws['G8'].fill.fgColor.rgb,'00C6EFCE');self.assertNotEqual(ws['H8'].fill.fgColor.rgb,'00C6EFCE')
                file=export_history(output=root/'poland.xlsx',league='poland');book=load_workbook(file)
                self.assertEqual(book.sheetnames,['EKSTRA 18-19','Notas']);self.assertIn('POLONIA',book.active['A1'].value)
                self.assertEqual(book.active['I24'].value,'Grupo por el título');self.assertIsNone(book.active.freeze_panes)
                self.assertEqual(book.active['F24'].data_type,'s')

    def test_awarded_correction_removes_a_placeholder_half_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'):
                db.initialize(seed);initial=polish(ht_home=0,ht_away=0);db.upsert([initial])
                db.upsert([{**initial,'administrative':True,'ht_home':None,'ht_away':None,'ft_home':3,'ft_away':0}])
                row=db.games('poland','2018-19')[0];self.assertTrue(row['administrative']);self.assertIsNone(row['ht_home']);self.assertIsNone(row['ht_away']);self.assertEqual(row['ft_home'],3)

    def test_refresh_retries_old_missing_ht_and_counts_retained_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir();seed=root/'seed.json';seed.write_text('[]')
            with patch.object(db,'DATA',root),patch.object(db,'DB_PATH',root/'test.sqlite3'),patch('scripts.publish_snapshot.ROOT',root):
                db.initialize(seed)
                known=polish(id='poland:ek:known',provider_id='ek:known',season='2026-27')
                missing=polish(id='poland:ek:missing',provider_id='ek:missing',season='2026-27',ht_home=None,ht_away=None)
                db.upsert([known,missing])
                incoming=[{**known,'ht_home':None,'ht_away':None},missing]
                with patch('scripts.publish_snapshot.current_seasons',return_value={'poland':'2026-27'}),patch('app.poland.sync_games',return_value=incoming) as fetch,patch('scripts.publish_snapshot.get_coverage',return_value={'fixtures_available':True,'missing_ht':2}):
                    refresh_sources()
                fetch.assert_called_once_with('2026-27',pending_ids=['poland:ek:missing'])
                snapshot=json.loads((root/'data/current.json').read_text())
                self.assertEqual(snapshot['sources'][0]['coverage']['missing_ht'],1)
                self.assertEqual(next(g for g in snapshot['games'] if g['id']==known['id'])['ht_home'],known['ht_home'])


if __name__=='__main__':unittest.main()
