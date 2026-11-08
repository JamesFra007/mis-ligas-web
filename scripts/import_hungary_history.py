"""Import NB I HT/FT from public MLSZ round pages and explicit match reports.

The cache is evidence, not a replacement for unknown observations. Future clocks,
awarded results and abandoned fixtures are never converted to 0-0 matches.
This command writes only its output/QA/cache; it does not modify the application DB.
"""
from __future__ import annotations
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.hungary import BASE, CONTEXTS, parse_round, enrich_match


def fetch(url,cache):
    if cache.exists():
        return gzip.decompress(cache.read_bytes()).decode('utf-8-sig')
    cache.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'MisLigas/1.0 (sports archive)'}),timeout=25) as response:
                raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:raise ValueError('El informe oficial supera el tamaño previsto.')
            text=raw.decode('utf-8-sig')
            temporary=cache.with_suffix(cache.suffix+'.new')
            temporary.write_bytes(gzip.compress(raw));temporary.replace(cache)
            return text
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(min(20,float(error.headers.get('Retry-After') or 1)*(attempt+1)))
    raise RuntimeError('No se pudo descargar '+url)


def import_history(start,end,cache,output,workers=6):
    if any(y not in CONTEXTS for y in range(start,end+1)):
        raise ValueError('Se requiere un identificador oficial de temporada antes de importarla.')
    evidence=[];round_errors=[];games={}
    def load_round(year,rnd):
        season_id,league_id=CONTEXTS[year]
        season=f'{year}-{(year+1)%100:02d}'
        url=f'{BASE}/league/{season_id}/0/{league_id}/{rnd}.html'
        path=cache/'rounds'/str(year)/f'{rnd:02}.html.gz'
        text=fetch(url,path)
        rows=parse_round(text,season)
        return rows,{'url':url,'cache':str(path),'sha256':hashlib.sha256(text.encode()).hexdigest(),'rows':len(rows)}
    tasks=[(y,r) for y in range(start,end+1) for r in range(1,34)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(load_round,y,r):(y,r) for y,r in tasks}
        for index,future in enumerate(as_completed(futures),1):
            try:
                rows,proof=future.result();evidence.append(proof)
                for game in rows:
                    if game['id'] in games and games[game['id']]!=game:raise ValueError('ID oficial repetido con datos distintos: '+game['id'])
                    games[game['id']]=game
            except Exception as error:round_errors.append({'round':futures[future],'error':str(error)})
            if index%40==0:print(f'Jornadas {index}/{len(tasks)}',flush=True)
    if round_errors:
        (output.parent/'history_round_errors.json').write_text(json.dumps(round_errors,ensure_ascii=False,indent=2))
        raise RuntimeError(f'No se completó el calendario de {len(round_errors)} jornadas; no se publica una importación incompleta.')
    rows=sorted(games.values(),key=lambda g:(g['season'],g['round'],g['date'],g.get('time') or '',g['id']))
    report_errors=[]
    def load_report(game):
        ident=game.get('mlsz_id')
        if not ident:return game,None
        path=cache/'reports'/f'{ident}.html.gz'
        text=fetch(game['source_url'],path)
        return enrich_match(game,text),{'url':game['source_url'],'cache':str(path),'sha256':hashlib.sha256(text.encode()).hexdigest()}
    eligible=[g for g in rows if g['status']=='finished' and not g.get('administrative') and g.get('mlsz_id')]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(load_report,g):g for g in eligible}
        for index,future in enumerate(as_completed(futures),1):
            game=futures[future]
            try:
                updated,proof=future.result();games[game['id']]=updated
                if proof:evidence.append(proof)
            except Exception as error:report_errors.append({'id':game['id'],'url':game['source_url'],'error':str(error)})
            if index%100==0:print(f'Informes HT {index}/{len(eligible)}',flush=True)
    # This federation record is an awarded 3-0, corroborated independently.
    for game in games.values():
        if game['season']=='2020-21' and game['date']=='2020-11-06' and game['home']=='Diósgyőr' and game['away']=='Újpest':
            game.update(administrative=True,administrative_reason='Resultado administrativo 3-0; partido no disputado (AWA).',ht_home=None,ht_away=None)
    rows=sorted(games.values(),key=lambda g:(g['season'],g['round'],g['date'],g.get('time') or '',g['id']))
    ordinal=Counter()
    for game in rows:
        ordinal[game['season']]+=1;game['source_order']=ordinal[game['season']]
        assert all(game.get(k) is None for k in ('corners','yellow','red','yellow_home','yellow_away','red_home','red_away'))
        for side in ('home','away'):
            ht,ft=game.get('ht_'+side),game.get('ft_'+side)
            assert ht is None or ft is not None and 0<=ht<=ft,(game['id'],side,ht,ft)
    summary=[]
    for season in sorted(ordinal):
        selected=[g for g in rows if g['season']==season]
        finished=[g for g in selected if g['status']=='finished']
        eligible=[g for g in finished if not g.get('administrative')]
        known=[g for g in eligible if g['ht_home'] is not None and g['ht_away'] is not None]
        summary.append({'season':season,'records':len(selected),'finished':len(finished),'eligible':len(eligible),'administrative':sum(bool(g.get('administrative')) for g in selected),'statuses':dict(Counter(g['status'] for g in selected)),'rounds':dict(Counter(g['round'] for g in selected)),'teams':sorted({g[k] for g in selected for k in ('home','away')}),'ft_goals':sum(g['ft_home']+g['ft_away'] for g in finished),'ft_btts':sum(min(g['ft_home'],g['ft_away'])>0 for g in finished),'known_ht':len(known),'ht_goals':sum(g['ht_home']+g['ht_away'] for g in known),'ht_btts':sum(min(g['ht_home'],g['ht_away'])>0 for g in known),'missing_ht':[g['id'] for g in eligible if g['ht_home'] is None or g['ht_away'] is None]})
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    qa={'source':'MLSZ Adatbank, official public match centre','captured_at':datetime.now(timezone.utc).isoformat(),'matches':len(rows),'seasons':summary,'report_errors':report_errors,'round_errors':round_errors,'output':str(output)}
    output.with_name('history_import_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2))
    output.with_name('history_source_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print(json.dumps(qa,ensure_ascii=False,indent=2))
    return rows,qa


def main():
    parser=argparse.ArgumentParser(description='Importar resultados HT/FT oficiales de Hungría desde 2018/19')
    parser.add_argument('--start',type=int,default=2018);parser.add_argument('--end',type=int,default=2026)
    parser.add_argument('--cache',type=Path,default=ROOT.parent/'hungary-data')
    parser.add_argument('--output',type=Path,default=ROOT.parent/'hungary-data'/'history_games.json')
    parser.add_argument('--workers',type=int,default=6)
    args=parser.parse_args();import_history(args.start,args.end,args.cache,args.output,args.workers)


if __name__=='__main__':main()
