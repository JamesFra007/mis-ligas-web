"""The published JavaScript snapshot must agree with the Python data contract."""
import json
from pathlib import Path
import random
import shutil
import subprocess
import unittest

from app.order import order_games
from app.leagues import LEAGUES
from app.rounds import round_id, round_options, select_round
from app.stats import summarize
from app.streaks import ht_droughts, ft_droughts, red_droughts
from test_core import game

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for the hosted snapshot contract')
class HostedViewsTests(unittest.TestCase):
    def test_rounds_and_droughts_match_for_partial_data_and_tied_kickoffs(self):
        rng = random.Random(20261109)
        samples = [[], [game(ht_home=0)], [game(ht_home=None, ht_away=None)]]
        # Unknown observations split verification; future/admin cases do not
        # terminate a verified run. Explicit zeros must remain observed zeros.
        samples.append([
            game(id=f'norm:{i}',date=f'2025-09-{i+1:02d}',time='14:00',
                 ht_home=0,ht_away=0,ft_home=0,ft_away=0,red=red,
                 status='scheduled' if i==7 else 'finished',
                 administrative=i==8,source_order=i)
            for i,red in enumerate([0,0,None,0,0,1,0,2,2])
        ])
        for sample in range(25):
            rows = []
            for i in range(30):
                ht = rng.choice([(0,0),(1,0),(0,1),(1,1),(2,1),(None,None)])
                ft = rng.choice([(0,0),(1,0),(0,1),(2,2),(None,None)])
                if None not in ft and None not in ht:
                    ft = tuple(max(a,b) for a,b in zip(ft,ht))
                rows.append(game(
                    id=f'spain:{sample}:{i}', provider_id=f'{sample}:{i}',
                    date=f'2025-09-{1+i//5:02d}',
                    time=rng.choice(['12:00','14:00','14:00',None]),
                    date_confirmed=not (sample % 7 == 0 and i == 10),
                    time_confirmed=not (sample % 3 == 0 and i == 12),
                    status=rng.choice(['finished']*7+['scheduled','live']),
                    administrative=rng.random()<0.08,
                    ht_home=ht[0], ht_away=ht[1], ft_home=ft[0], ft_away=ft[1],
                    red=rng.choice([0,0,0,1,2,None]),
                    round=rng.choice([0,1,2,3,None]),
                    scope=rng.choice(['Apertura','Finalización']),
                    phase=rng.choice(['Fase regular','Cuadrangulares']),
                    source_order=i//3,
                ))
            samples.append(rows)
        cases = []
        for i,rows in enumerate(samples):
            league = tuple(LEAGUES)[i%len(LEAGUES)]
            season = '2025' if league=='colombia' else '2025-26'
            rows = [{**row,'league':league,'season':season} for row in rows]
            for order in ('round','chronological'):
                for selected in ('','auto',round_id(rows[-1]) if rows else 'stale'):
                    cases.append({'games':rows,'leagues':[{'id':key,**info} for key,info in LEAGUES.items()],'params':{'league':league,'season':season,'order':order,'match_round':selected,'page':'2','page_size':'3'}})
        runner = r"""
const fs=require('fs'),vm=require('vm');
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
const code=fs.readFileSync('static/offline.js','utf8');
const results=cases.map(test=>{
  const bundle={games:test.games,meta:{leagues:test.leagues,updated_at:null}};
  const context={document:{getElementById:()=>({textContent:JSON.stringify(bundle)}),addEventListener:()=>{}},window:{},URL,URLSearchParams,Response};
  vm.runInNewContext(code,context);
  return context.window.__offlineDashboard(new URLSearchParams(test.params));
});
process.stdout.write(JSON.stringify(results));
"""
        result = subprocess.run(['node','-e',runner],input=json.dumps(cases),text=True,cwd=ROOT,capture_output=True,check=True)
        actual = json.loads(result.stdout)
        self.assertEqual(len(actual),len(cases))
        for i,(case,data) in enumerate(zip(cases,actual)):
            with self.subTest(case=i):
                rows=case['games'];params=case['params']
                ordered=order_games(rows,params['order'])
                rounds=round_options(rows)
                selected,key=select_round(ordered,params['match_round'],rounds)
                pages=max(1,(len(selected)+2)//3);page=min(2,pages)
                self.assertEqual(data['ht_droughts'],ht_droughts(rows))
                self.assertEqual(data['droughts'],{'ht':ht_droughts(rows),'ft':ft_droughts(rows),'red':red_droughts(rows) if LEAGUES[params['league']]['cards'] else None})
                self.assertEqual(data['options']['rounds'],rounds)
                self.assertEqual(data['options']['selected_round'],key)
                for field,value in summarize(ordered).items():
                    self.assertEqual(data[field],value,field)
                self.assertEqual(data['pagination'],{'page':page,'page_size':3,'total':len(selected),'pages':pages})
                self.assertEqual([g['id'] for g in data['games']],[g['id'] for g in selected[(page-1)*3:page*3]])


if __name__ == '__main__':
    unittest.main()
