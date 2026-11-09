"""Import real Polish Ekstraklasa HT/FT history from public result archives.

The first two seasons contain a regular stage and two genuine groups. All three
are loaded and deduplicated by the source's stable match identifier. Unknown HT
never becomes 0-0. This script writes evidence/cache/output only, not the app DB.
"""
from __future__ import annotations
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request,urlopen
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.poland_names import normalize_team

BASE='https://www.betexplorer.com'
BOGOTA=ZoneInfo('America/Bogota');WARSAW=ZoneInfo('Europe/Warsaw')
EXPECTED={2018:296,2019:296,2020:240,2021:306,2022:306,2023:306,2024:306,2025:306}


def plain(text):
    return ' '.join(unescape(re.sub(r'<[^>]*>',' ',text or '')).split())


def fetch(url,path):
    if path.exists():return gzip.decompress(path.read_bytes()).decode('utf-8-sig')
    path.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'MisLigas/1.0 (sports archive)'}),timeout=25) as response:
                raw=response.read(3*1024*1024+1)
            if len(raw)>3*1024*1024:raise ValueError('Archive response too large')
            text=raw.decode('utf-8-sig');tmp=path.with_suffix(path.suffix+'.new');tmp.write_bytes(gzip.compress(raw));tmp.replace(path)
            return text
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==2:raise
            retry=error.headers.get('Retry-After') or '1'
            try:seconds=float(retry)
            except ValueError:seconds=3
            if seconds>30:raise RuntimeError(f'Source requested a long pause ({retry}); retry this cached import later') from error
            time.sleep(max(1,seconds)*(attempt+1))
        except (URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(attempt+1)
    raise RuntimeError('Could not fetch '+url)


def stages(html):
    found=[]
    for path,label in re.findall(r'<a\b[^>]*href=["\'](\?stage=[^"\']+)["\'][^>]*>(.*?)</a>',html,re.S|re.I):
        item=(unescape(path),plain(label))
        if item not in found:found.append(item)
    return found


def parse_results(html,season,phase='Fase regular'):
    table=re.search(r'<table\b[^>]*class=["\'][^"\']*\btable-main\b[^"\']*["\'][^>]*>(.*?)</table>',html,re.S|re.I)
    if not table:raise ValueError('No result table for '+season)
    result=[];round_number=None
    for raw in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table[1],re.S|re.I):
        heading=re.search(r'(\d+)\.\s*Round',plain(raw))
        if heading:round_number=int(heading[1]);continue
        cols=re.findall(r'<td\b[^>]*>(.*?)</td>',raw,re.S|re.I)
        if len(cols)<3:continue
        anchor=re.search(r'<a\b(?=[^>]*class=["\'][^"\']*\bin-match\b)[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',cols[0],re.S|re.I)
        if not anchor:continue
        url=urljoin(BASE,unescape(anchor[1]));ident=url.rstrip('/').rsplit('/',1)[-1]
        if not re.fullmatch(r'[A-Za-z0-9]{8}',ident):continue
        names=[plain(x) for x in re.findall(r'<span\b[^>]*>(.*?)</span>',anchor[2],re.S|re.I)]
        if len(names)!=2:raise ValueError('Unknown teams for '+ident)
        score=re.match(r'^\s*(\d{1,2})\s*:\s*(\d{1,2})',plain(cols[1]))
        dt=re.search(r'(\d{1,2})\.(\d{1,2})\.(20\d\d)?',plain(cols[-1]))
        if dt is None:raise ValueError('No date for '+ident)
        start_year=int(season[:4]);year=int(dt[3]) if dt[3] else start_year if int(dt[2])>=7 else start_year+1
        day=datetime(year,int(dt[2]),int(dt[1])).date()
        awarded=bool(re.search(r'title=["\']Awarded["\']|\bAWA\.|\bW\.O\.',raw,re.I))
        status='finished' if score else 'postponed' if re.search(r'\bPOSTP\b',plain(cols[1])) else 'cancelled'
        game={'id':'poland:be:'+ident,'provider_id':'be:'+ident,'league':'poland','season':season,'season_label':season.replace('-','/'),'date':day.isoformat(),'time':None,'kickoff':None,'time_confirmed':False,'date_confirmed':bool(dt[3]),'home':normalize_team(names[0]),'away':normalize_team(names[1]),'round':round_number,'scope':phase if int(season[:4]) in (2018,2019) else 'all','phase':phase if int(season[:4]) in (2018,2019) else 'Ekstraklasa','status':status,'ht_home':None,'ht_away':None,'ft_home':int(score[1]) if score else None,'ft_away':int(score[2]) if score else None,'corners':None,'yellow':None,'red':None,'yellow_home':None,'yellow_away':None,'red_home':None,'red_away':None,'administrative':awarded,'administrative_reason':'Resultado administrativo sin partido jugado (fuente AWA).' if awarded else None,'source':'BetExplorer · Ekstraklasa','source_url':url}
        if season=='2022-23' and game['home']=='Piast Gliwice' and game['away']=='Lechia Gdańsk' and day.isoformat()=='2023-05-27':
            game.update(administrative=True,administrative_reason='Partido abandonado al minuto 80; resultado administrativo 3-0 (contrastado con RSSSF).',ht_home=None,ht_away=None,ft_home=3,ft_away=0)
        result.append(game)
    if not result:raise ValueError('Empty results '+season)
    return result


def enrich_report(game,html):
    result=dict(game)
    if result.get('administrative') or result['status']!='finished':return result
    score=re.search(r'<[^>]*id=["\']js-score["\'][^>]*>(.*?)</[^>]+>',html,re.S|re.I)
    ft=re.fullmatch(r'(\d{1,2})\s*:\s*(\d{1,2})',plain(score[1])) if score else None
    if ft:
        result['ft_home'],result['ft_away']=int(ft[1]),int(ft[2])
    partial=re.search(r'<[^>]*id=["\']js-partial["\'][^>]*>(.*?)</[^>]+>',html,re.S|re.I)
    if partial:
        halves=re.fullmatch(r'\(?\s*(\d{1,2})\s*:\s*(\d{1,2})\s*,\s*(\d{1,2})\s*:\s*(\d{1,2})\s*\)?',plain(partial[1]))
        if halves:
            h,a,h2,a2=map(int,halves.groups())
            if (h+h2,a+a2)==(result['ft_home'],result['ft_away']):result['ht_home'],result['ht_away']=h,a
    for block in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',html,re.S|re.I):
        try:data=json.loads(unescape(block))
        except ValueError:continue
        objects=data if isinstance(data,list) else [data]
        for item in objects:
            if not isinstance(item,dict) or not item.get('startDate'):continue
            try:
                stamp=datetime.fromisoformat(item['startDate'].replace('Z','+00:00'))
                if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=WARSAW)
                local=stamp.astimezone(BOGOTA)
                result.update(date=local.date().isoformat(),time=local.strftime('%H:%M'),kickoff=stamp.astimezone(timezone.utc).isoformat(),time_confirmed=True,date_confirmed=True)
            except (ValueError,TypeError):continue
    return result


def import_history(start,end,cache,output,workers=6):
    game_map={};proofs=[];errors=[]
    phase_names={'Main':'Fase regular','Championship Group':'Grupo por el título','Relegation Group':'Grupo por la permanencia'}
    def load_year(year):
        season=f'{year}-{(year+1)%100:02d}';url=f'{BASE}/football/poland/ekstraklasa-{year}-{year+1}/results/'
        text=fetch(url,cache/'results'/f'{season}.html.gz');variants=stages(text)
        pages=[]
        if variants:
            for query,name in variants:
                stage_url=url+query;ident=query.split('=',1)[1]
                page=fetch(stage_url,cache/'results'/f'{season}_{ident}.html.gz')
                pages.append((page,phase_names.get(name,name),stage_url))
        else:pages=[(text,'Fase regular',url)]
        rows=[];evidence=[]
        for page,name,source in pages:
            items=parse_results(page,season,name);rows.extend(items);evidence.append({'url':source,'sha256':hashlib.sha256(page.encode()).hexdigest(),'records':len(items)})
        by_id={g['id']:g for g in rows}
        count=sum(g['status']=='finished' for g in by_id.values())
        if count!=EXPECTED[year]:raise ValueError(f'{season}: {count} FT, expected {EXPECTED[year]}')
        return list(by_id.values()),evidence
    with ThreadPoolExecutor(max_workers=min(workers,4)) as pool:
        jobs={pool.submit(load_year,y):y for y in range(start,end+1)}
        for future in as_completed(jobs):
            try:
                games,evidence=future.result();proofs.extend(evidence)
                game_map.update({g['id']:g for g in games});print('Season',jobs[future],'records',len(games),flush=True)
            except Exception as error:errors.append({'year':jobs[future],'error':str(error)})
    output.parent.mkdir(parents=True,exist_ok=True)
    if errors:
        (output.parent/'table_errors.json').write_text(json.dumps(errors,indent=2));raise RuntimeError('Incomplete seasons: '+str(errors))
    # Keep a resumable inventory before starting the longer report pass.
    (output.parent/'history_inventory.json').write_text(json.dumps(list(game_map.values()),ensure_ascii=False,indent=2))
    def enrich(game):
        ident=game['provider_id'].split(':',1)[1];path=cache/'reports'/f'{ident}.html.gz';text=fetch(game['source_url'],path)
        return enrich_report(game,text),{'id':game['id'],'url':game['source_url'],'sha256':hashlib.sha256(text.encode()).hexdigest()}
    eligible=[g for g in game_map.values() if g['status']=='finished' and not g['administrative']]
    report_errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(enrich,g):g for g in eligible}
        for index,future in enumerate(as_completed(jobs),1):
            game=jobs[future]
            try:
                updated,proof=future.result();game_map[game['id']]=updated;proofs.append(proof)
            except Exception as error:report_errors.append({'id':game['id'],'url':game['source_url'],'error':str(error)})
            if index%100==0:print('HT reports',index,'/',len(eligible),flush=True)
    games=sorted(game_map.values(),key=lambda g:(g['season'],g['round'] or 99,g['date'],g['time'] or '',g['id']))
    order=Counter()
    for game in games:
        order[game['season']]+=1;game['source_order']=order[game['season']]
        assert all(game.get(k) is None for k in ('corners','yellow','red','yellow_home','yellow_away','red_home','red_away'))
        if game['ht_home'] is not None:assert 0<=game['ht_home']<=game['ft_home'] and 0<=game['ht_away']<=game['ft_away']
    summaries=[]
    for season in sorted(order):
        items=[g for g in games if g['season']==season];played=[g for g in items if g['status']=='finished' and not g['administrative']];ht=[g for g in played if g['ht_home'] is not None and g['ht_away'] is not None]
        summaries.append({'season':season,'records':len(items),'finished':sum(g['status']=='finished' for g in items),'played':len(played),'known_ht':len(ht),'administrative':sum(g['administrative'] for g in items),'ft_goals':sum(g['ft_home']+g['ft_away'] for g in items if g['status']=='finished'),'ft_btts':sum(min(g['ft_home'],g['ft_away'])>0 for g in items if g['status']=='finished'),'ht_goals':sum(g['ht_home']+g['ht_away'] for g in ht),'ht_btts':sum(min(g['ht_home'],g['ht_away'])>0 for g in ht),'rounds':dict(Counter(g['round'] for g in items)),'teams':sorted({g[k] for g in items for k in ('home','away')}),'missing_ht':[g['id'] for g in played if g['ht_home'] is None or g['ht_away'] is None],'missing_time':[g['id'] for g in played if not g['time_confirmed']]})
    output.write_text(json.dumps(games,ensure_ascii=False,indent=2))
    qa={'source':'BetExplorer public result tables and explicit period scores','captured_at':datetime.now(timezone.utc).isoformat(),'matches':len(games),'seasons':summaries,'report_errors':report_errors,'table_errors':errors}
    (output.parent/'history_import_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2));(output.parent/'history_source_evidence.json').write_text(json.dumps(proofs,ensure_ascii=False,indent=2));print(json.dumps(qa,ensure_ascii=False,indent=2))
    return games,qa


def main():
    p=argparse.ArgumentParser();p.add_argument('--start',type=int,default=2018);p.add_argument('--end',type=int,default=2025);p.add_argument('--workers',type=int,default=6);p.add_argument('--cache',type=Path,default=Path(__file__).resolve().parents[2]/'poland-data');p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[2]/'poland-data/history_games.json');a=p.parse_args();import_history(a.start,a.end,a.cache,a.output,a.workers)

if __name__=='__main__':main()
