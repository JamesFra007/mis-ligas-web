from copy import deepcopy
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
from app.stats import summarize

ROOT=Path(__file__).resolve().parents[1]

def game(**overrides):
    row={'id':'test:1','provider_id':'1','league':'spain','season':'2025-26','season_label':'2025/26','date':'2025-09-01','time':'14:00','home':'Real Madrid','away':'Barcelona','round':3,'scope':'first','phase':'Fase regular','status':'finished','ht_home':1,'ht_away':1,'ft_home':2,'ft_away':1,'corners':0,'yellow':0,'red':0,'yellow_home':0,'yellow_away':0,'red_home':0,'red_away':0,'administrative':False,'source':'audit','source_order':1}
    return {**row,**overrides}


class StatisticsTests(unittest.TestCase):
    def test_missing_zero_future_admin_have_distinct_bases(self):
        rows=[game(),game(id='2',ht_home=None,ht_away=None,corners=None,yellow=None,red=None),game(id='3',status='scheduled',ht_home=9,ft_home=9),game(id='4',administrative=True,ht_home=4,ft_home=4)]
        result=summarize(rows)['summary']
        self.assertEqual((result['total'],result['finished'],result['excluded'],result['scheduled']),(4,2,1,1))
        self.assertEqual((result['ht_base'],result['ht_cases'],result['ft_base']),(1,1,2))
        self.assertEqual((result['corners_average'],result['corners_base'],result['corners_missing']),(0,1,1))
        self.assertEqual((result['red_average'],result['red_match_rate'],result['red_base']),(0,0,1))
        self.assertEqual(result['unresolved'],1)

    def test_historical_spain_golden_totals(self):
        rows=json.loads((ROOT/'data/seed.json').read_text())
        expected=[(2018,402,983,61,197,1965,79),(2019,400,942,61,189,1950,86),(2020,439,953,75,197,1694,76),(2021,415,951,70,185,1954,88),(2022,425,955,69,191,1907,135),(2023,455,1005,77,188,1812,87),(2024,438,995,70,206,1703,78),(2025,437,1024,69,215,1677,103)]
        for year,ht_goals,ft_goals,ht,ft,ys,rs in expected:
            season=f'{year}-{(year+1)%100:02d}'
            s=summarize([g for g in rows if g['league']=='spain' and g['season']==season])['summary']
            self.assertEqual((s['finished'],s['ht_cases'],s['ft_cases'],s['yellow_total'],s['red_total']),(380,ht,ft,ys,rs))
            self.assertAlmostEqual(s['goals_ht_average']*380,ht_goals)
            self.assertAlmostEqual(s['goals_ft_average']*380,ft_goals)

    def test_frequency_original_order_and_team_own_cards(self):
        rows=[game(id=str(i),ht_home=(1 if i in (2,5) else 0),ht_away=1,source_order=i,yellow=7,yellow_home=2,yellow_away=5) for i in range(1,7)]
        result=summarize(rows,'Real Madrid')
        self.assertEqual(result['frequency']['ht'],{'max_without':2,'current_without':1,'mean_gap':3})
        self.assertEqual(result['summary']['ht_every'],3)
        self.assertEqual(result['team_stats']['own_yellow_average'],2)
        self.assertEqual(result['summary']['yellow_average'],7)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
        self.patch1=patch.object(db,'DATA',self.data);self.patch2=patch.object(db,'DB_PATH',self.data/'db.sqlite3')
        self.patch1.start();self.patch2.start()
        empty=self.data/'seed.json';empty.write_text('[]');db.initialize(empty)

    def tearDown(self):
        self.patch1.stop();self.patch2.stop();self.temp.cleanup()

    def test_idempotent_update_preserves_known_data_scope_phase_order(self):
        original=game();db.upsert([original])
        incoming=game(id='new-provider-id',provider_id='different',round=None,scope='all',phase='LaLiga',ht_home=None,ht_away=None,corners=None,source_order=999,source='provider')
        first=db.upsert([incoming]);second=db.upsert([incoming]);actual=db.games('spain','2025-26')
        self.assertEqual(len(actual),1);self.assertEqual(first['updated'],1);self.assertEqual(second['unchanged'],1)
        self.assertEqual((actual[0]['id'],actual[0]['source_order'],actual[0]['round'],actual[0]['scope'],actual[0]['phase'],actual[0]['ht_home'],actual[0]['corners']),('test:1',1,3,'first','Fase regular',1,0))

    def test_colombia_repeat_fixture_keeps_event_ids_and_admin(self):
        a=game(id='colombia:1',provider_id='1',league='colombia',season='2025',scope='Finalización',administrative=True)
        b=game(id='colombia:2',provider_id='2',league='colombia',season='2025',date='2025-10-02')
        db.upsert([a,b]);db.upsert([dict(a,id='different',scope='Temporada',phase='Liga BetPlay',administrative=False,ft_home=0,ht_home=0)])
        result=db.games('colombia','2025');self.assertEqual(len(result),2)
        self.assertTrue(result[0]['administrative']);self.assertEqual(result[0]['ft_home'],2);self.assertEqual(result[0]['scope'],'Finalización')

    def test_invalid_snapshot_is_atomic(self):
        with self.assertRaises(ValueError):db.upsert([game(),game(id='2',home='Celta',ht_home=7)])
        self.assertEqual(db.games('spain','2025-26'),[])

    def test_dashboard_filter_stats_and_safe_export(self):
        db.upsert([game(home='=2+2'),game(id='2',provider_id='2',home='=2+2',away='Celta',red=1,status='finished')])
        with TestClient(app) as client:
            r=client.get('/api/dashboard',params={'league':'spain','season':'2025-26','team':'=2+2','condition':'home','page_size':1})
            self.assertEqual(r.status_code,200);data=r.json();self.assertEqual(data['summary']['finished'],2);self.assertEqual(len(data['games']),1);self.assertTrue(data['games'][0]['btts_ht']);self.assertEqual(data['pagination']['pages'],2)
            response=client.get('/api/export.xlsx',params={'league':'spain','season':'2025-26'})
            self.assertEqual(response.status_code,200);book=load_workbook(BytesIO(response.content));ws=book['Partidos']
            self.assertEqual(ws['D8'].value,'=2+2');self.assertEqual(ws['D8'].data_type,'s');self.assertIsNone(ws['J8'].value);self.assertIsNone(ws['J8'].fill.patternType)
            self.assertEqual(ws['H8'].fill.fgColor.rgb,'00C6EFCE');self.assertIsNone(ws.freeze_panes)
            self.assertEqual(len(ws._images),3)
            self.assertEqual(client.get('/api/dashboard',params={'league':'unknown'}).status_code,400)
            self.assertEqual(client.get('/api/dashboard',params={'season':'2025-99'}).status_code,400)


if __name__=='__main__':unittest.main()
