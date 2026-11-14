"""Capture the current Paraguayan year using identified, cross-checked matches."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.paraguay import BASE, parse_espn_event, parse_round_table, enrich_match
from scripts.import_paraguay_history import fetch, parse_report


def capture(cache,output,year=2026):
    cache=Path(cache);output=Path(output);proofs=[];errors=[]
    url=f'{BASE}/scoreboard?dates={year}&limit=1000'
    raw=fetch(url,cache/f'scoreboard{year}.json')
    games=[g for event in json.loads(raw)['events'] if (g:=parse_espn_event(event,str(year))) is not None]
    if len(games)!=264:raise ValueError('Current annual calendar is incomplete')
    proofs.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest()})
    mapping={}
    tables=[('apertura-results','Apertura','results/','EBFnzgo3'),
            ('clausura-results','Clausura','results/','Aw9eYWFF'),
            ('clausura-fixtures','Clausura','fixtures/','Aw9eYWFF')]
    for suffix,scope,section,stage in tables:
        url=f'https://www.betexplorer.com/football/paraguay/copa-de-primera/{section}?stage={stage}'
        raw=fetch(url,cache/f'rounds{year}-{suffix}.html')
        observed=parse_round_table(raw,str(year),scope,url)
        for key,value in observed.items():
            if key in mapping and mapping[key]!=value:raise ValueError('Contradictory current round table')
            mapping[key]=value
        proofs.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest()})
    for game in games:
        key=game['scope'],game['home'],game['away']
        if key not in mapping:raise ValueError('Missing explicit current matchday')
        game.update(mapping[key])
    def complete(game):
        if game['status']!='finished' or game.get('administrative'):return game,[]
        url=f"{BASE}/summary?event={game['espn_id']}"
        raw=fetch(url,cache/f"summary{game['espn_id']}.json")
        result=enrich_match(game,json.loads(raw));evidence=[{'url':url,'sha256':hashlib.sha256(raw).hexdigest()}]
        if result.get('ht_home') is None or result.get('ht_away') is None:
            url=game['archive_url'];raw=fetch(url,cache/'reports'/(game['archive_id']+'.html.gz'))
            report=parse_report(raw,game)
            result.update({key:report[key] for key in ('ht_home','ht_away','ht_source','ht_source_url')})
            evidence.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest()})
        return result,evidence
    results={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks={pool.submit(complete,game):game for game in games}
        for index,future in enumerate(as_completed(tasks),1):
            game=tasks[future]
            try:
                result,evidence=future.result();results[game['id']]=result;proofs.extend(evidence)
            except Exception as error:errors.append({'id':game['id'],'error':str(error)})
            if index%25==0:print(f'Current reports {index} / {len(games)}',flush=True)
    games=[results.get(game['id'],game) for game in games]
    games.sort(key=lambda g:(g['scope']!='Apertura',g['round'],g['date'],g.get('time') or '',g['id']))
    for index,game in enumerate(games,1):game['source_order']=index
    output.write_text(json.dumps(games,ensure_ascii=False,indent=2)+'\n')
    played=[g for g in games if g['status']=='finished' and not g.get('administrative')]
    qa={'captured_at':datetime.now(timezone.utc).isoformat(),'matches':len(games),'finished':len(played),
        'administrative':sum(bool(g.get('administrative')) for g in games),
        'scheduled':sum(g['status']=='scheduled' for g in games),
        'missing_in_played':{key:sum(g.get(key) is None for g in played) for key in ('ht_home','ht_away','ft_home','ft_away','round','corners')},
        'errors':errors}
    (output.parent/'current_import_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n')
    (output.parent/'current_source_evidence.json').write_text(json.dumps(proofs,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(qa,ensure_ascii=False,indent=2),flush=True)
    return games


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,default=Path('/workspace/paraguay-data/provider-cache'))
    parser.add_argument('--output',type=Path,default=Path('/workspace/paraguay-data/current_games.json'))
    args=parser.parse_args();capture(args.cache,args.output)
