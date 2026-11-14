"""Import the observed Paraguayan annual championships, without card fields.

ESPN stable event IDs identify every fixture. Archive matchday headings provide
the round; summaries provide half-time scores. Unpublished corners remain None.
This importer only writes its cache/evidence/output, never the application DB.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.paraguay import parse_espn_event, enrich_match, half_time_from_complete_events
from app.paraguay_names import normalize_team
from scripts.paraguay_archive import parse_results

BASE='https://site.api.espn.com/apis/site/v2/sports/soccer/par.1'
BE='https://www.betexplorer.com'
BOGOTA=ZoneInfo('America/Bogota')
EXPECTED={2018:264,2019:264,2020:205,2021:180,2022:264,2023:264,2024:264,2025:264}
CORRECTIONS={
    '501220': {'score':(0,3),'administrative':True,'played':(4,0),'reason':'Resultado administrativo 0-3 por jugador inelegible; en campo fue 4-0.','url':'https://www.rsssf.org/tablesp/para2018.html'},
    '530372': {'score':(3,0),'administrative':True,'played':(3,4),'reason':'Resultado administrativo 3-0: Libertad utilizó cinco extranjeros; en campo fue 3-4.','url':'https://www.rsssf.org/tablesp/para2019.html'},
    '561133': {'score':(0,3),'administrative':True,'played':(4,2),'reason':'Resultado administrativo 0-3 por alineación irregular; en campo fue 4-2.','url':'https://www.rsssf.org/tablesp/para2020.html'},
    '695676': {'score':(0,3),'administrative':True,'played':(0,0),'reason':'Partido abandonado al minuto 55 por incidentes; resultado administrativo 0-3.','url':'https://www.rsssf.org/tablesp/para2024.html'},
    '728904': {'score':(0,1),'administrative':False,'reason':'Marcador 0-1 verificado con RSSSF y BetExplorer; el 0-0 publicado por ESPN era erróneo.','url':'https://www.rsssf.org/tablesp/para2025.html'},
}


def plain(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>',' ',value or '')).split())


def fetch(url,path):
    path=Path(path)
    if path.exists():
        data=path.read_bytes()
        return gzip.decompress(data) if path.suffix=='.gz' else data
    path.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'MisLigas/1.0 (sports archive)','Accept':'application/json,text/html;q=0.9,*/*;q=0.5'}),timeout=25) as response:
                data=response.read(8*1024*1024+1)
            if len(data)>8*1024*1024:raise ValueError('Response too large')
            temporary=path.with_suffix(path.suffix+'.new')
            temporary.write_bytes(gzip.compress(data) if path.suffix=='.gz' else data)
            temporary.replace(path)
            return data
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==2:raise
            retry=error.headers.get('Retry-After') or '1'
            try:seconds=float(retry)
            except ValueError:seconds=3
            if seconds>30:raise RuntimeError('Source requested a pause longer than this importer can apply') from error
            time.sleep(max(1,seconds)*(attempt+1))
        except (URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(attempt+1)
    raise RuntimeError('Could not fetch source')


def archive_stages(html):
    found=[]
    for query,label in re.findall(r'<a\b[^>]*href=["\'](\?stage=[^"\']+)["\'][^>]*>(.*?)</a>',html,re.S|re.I):
        value=unescape(query),plain(label)
        if value not in found:found.append(value)
    return found


def parse_archive(html,year,scope,source_url):
    table=re.search(r'<table\b[^>]*class=["\'][^"\']*\btable-main\b[^"\']*["\'][^>]*>(.*?)</table>',html,re.S|re.I)
    if table is None:raise ValueError(f'No archive result table: {year} {scope}')
    records=[];rnd=None;phase='Fase regular'
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table[1],re.S|re.I):
        text=plain(row)
        if '<th' in row:
            number=re.search(r'(\d+)\.\s*Round',text,re.I)
            if number:rnd=int(number[1]);phase='Fase regular'
            elif re.search(r'quarter.?final|cuartos',text,re.I):rnd=None;phase='Cuartos de final'
            elif re.search(r'semi.?final',text,re.I):rnd=None;phase='Semifinal'
            elif scope=='Promoción/permanencia' and re.search(r'first leg',text,re.I):rnd=None;phase='Ida'
            elif scope=='Promoción/permanencia' and re.search(r'second leg',text,re.I):rnd=None;phase='Vuelta'
            elif re.search(r'\bfinal\b',text,re.I):rnd=None;phase='Final'
            continue
        columns=re.findall(r'<td\b[^>]*>(.*?)</td>',row,re.S|re.I)
        if len(columns)<3:continue
        anchor=re.search(r'<a\b(?=[^>]*class=["\'][^"\']*\bin-match\b)[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',columns[0],re.S|re.I)
        if not anchor:continue
        names=[plain(v) for v in re.findall(r'<span\b[^>]*>(.*?)</span>',anchor[2],re.S|re.I)]
        if len(names)!=2:raise ValueError('Archive has unidentified teams')
        url=urljoin(source_url,unescape(anchor[1]));ident=url.rstrip('/').rsplit('/',1)[-1]
        score=re.match(r'^(\d{1,2})\s*:\s*(\d{1,2})',plain(columns[1]))
        date=re.search(r'(\d{1,2})\.(\d{1,2})\.(20\d\d)?',plain(columns[-1]))
        if date is None:raise ValueError('Archive has no fixture date')
        observed_year=int(date[3]) if date[3] else int(year)
        if observed_year!=int(year):raise ValueError('Archive mixes annual seasons')
        day=datetime(observed_year,int(date[2]),int(date[1])).date().isoformat()
        records.append({'archive_id':ident,'home':normalize_team(names[0]),'away':normalize_team(names[1]),'scope':scope,'phase':phase,'round':rnd,'round_not_applicable':phase!='Fase regular','date':day,'ft_home':int(score[1]) if score else None,'ft_away':int(score[2]) if score else None,'penalties':bool(re.search(r'After\s+Penalties|\bAfP\b|pen\.?|penalties',columns[1],re.I)),'administrative':bool(re.search(r'title=["\']Awarded["\']|\bAWA\.|\bW\.O\.',row,re.I)),'archive_url':url,'round_source_url':source_url})
    return records


def parse_report(html,game=None):
    """Read explicit periods only after checking event identity and final score."""
    if isinstance(html,bytes):html=html.decode('utf-8-sig')
    events=[]
    for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',html,re.S|re.I):
        try:value=json.loads(raw)
        except ValueError:continue
        if isinstance(value,dict) and value.get('@type')=='SportsEvent':events.append(value)
    if len(events)!=1:raise ValueError('Report does not identify one sporting event')
    event=events[0]
    home=normalize_team(event.get('homeTeam',{}).get('name'))
    away=normalize_team(event.get('awayTeam',{}).get('name'))
    stamp=datetime.fromisoformat(event['startDate'].replace('Z','+00:00'))
    if stamp.tzinfo is None:raise ValueError('Report kickoff lacks timezone')
    local=stamp.astimezone(BOGOTA)
    def element(ident):
        match=re.search(r'<[^>]+\bid=["\']'+ident+r'["\'][^>]*>(.*?)</[^>]+>',html,re.S|re.I)
        return plain(match[1]) if match else ''
    periods=[tuple(map(int,p)) for p in re.findall(r'(\d{1,2})\s*:\s*(\d{1,2})',element('js-partial'))]
    if len(periods)<2:raise ValueError('Report does not publish both halves')
    final=tuple(sum(period[side] for period in periods[:2]) for side in (0,1))
    if game is not None:
        if (home,away)!=(game['home'],game['away']):raise ValueError('Report teams do not match')
        if game.get('archive_id') and event.get('url','').rstrip('/').rsplit('/',1)[-1]!=game['archive_id']:
            raise ValueError('Report event ID does not match')
        day=datetime.fromisoformat(game['date']).date()
        if abs((local.date()-day).days)>1 or str(local.year)!=game['season']:
            raise ValueError('Report date does not match')
        if final!=(game['ft_home'],game['ft_away']):raise ValueError('Report periods do not match FT')
    return {'home':home,'away':away,'date':local.date().isoformat(),'time':local.strftime('%H:%M'),
            'kickoff':stamp.astimezone(timezone.utc).isoformat(),'ft_home':final[0],'ft_away':final[1],
            'ht_home':periods[0][0],'ht_away':periods[0][1],
            'ht_source':'BetExplorer · marcador explícito de cada tiempo',
            'ht_source_url':event.get('url'),'time_confirmed':True,'date_confirmed':True}


def import_history(start,end,cache,output,workers=4):
    cache=Path(cache);output=Path(output);proofs=[];rows=[];archives=[];independent=[]
    output.parent.mkdir(parents=True,exist_ok=True)
    for year in range(start,end+1):
        url=f'{BASE}/scoreboard?dates={year}&limit=1000'
        raw=fetch(url,cache/f'espn-{year}.json');events=json.loads(raw).get('events')
        if not isinstance(events,list) or len(events)>=1000:raise ValueError('Invalid ESPN season')
        games=[g for e in events if (g:=parse_espn_event(e,str(year))) is not None]
        if len(games)!=EXPECTED[year]:raise ValueError(f'{year}: unexpected calendar size')
        rows.extend(games)
        proofs.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'events':len(games)})
        slug='copa-de-primera' if year>=2025 else 'primera-division'
        base=f'{BE}/football/paraguay/{slug}-{year}/results/'
        page=fetch(base,cache/f'be-{year}.html').decode('utf-8-sig')
        variants=archive_stages(page)
        if not variants:raise ValueError(f'{year}: missing stage archive')
        for query,label in variants:
            if label in ('Apertura','Clausura'):
                scope=label;filename=f'be-{year}-{query.split("=",1)[1]}.html'
            elif year==2020 and label=='Clausura - Play Offs':
                scope='Clausura';filename='be-2020-playoffs.html'
            elif year==2021 and label=='Relegation':
                scope='Promoción';filename='be-2021-relegation.html'
            else:continue
            stage_url=base+query;raw_table=fetch(stage_url,cache/filename)
            records=parse_archive(raw_table.decode('utf-8-sig'),year,scope,stage_url)
            if scope=='Promoción':
                for record in records:record.update(phase='Promoción',round=None,round_not_applicable=True)
            archives.extend({'season':str(year),**record} for record in records)
            proofs.append({'url':stage_url,'sha256':hashlib.sha256(raw_table).hexdigest()})
        source=f'https://www.rsssf.org/tablesp/para{year}.html'
        audit=cache.parent/'audit'/f'rsssf_{year}.html'
        raw_rsssf=fetch(source,audit if audit.exists() else cache/f'rsssf-{year}.html')
        independent.extend(parse_results(raw_rsssf,year))
        proofs.append({'url':source,'sha256':hashlib.sha256(raw_rsssf).hexdigest()})
        print(f'Season {year}: {len(games)} ESPN matches loaded',flush=True)
    def key(record):return tuple(record[k] for k in ('season','scope','phase','home','away'))
    def index_unique(records):
        indexed={}
        for record in records:
            if key(record) in indexed:raise ValueError('Ambiguous independent archive fixture')
            indexed[key(record)]=record
        return indexed
    archive_index=index_unique(archives);rsssf_index=index_unique(independent)
    for game in rows:
        source=archive_index.get(key(game))
        if source is None:raise ValueError(f'No matchday archive for {game["id"]}')
        game.update({k:source[k] for k in ('round','round_not_applicable','archive_id','archive_url','round_source_url')})
        game['round_source']='BetExplorer · jornadas explícitas'
        if source['administrative'] and not game.get('administrative'):
            raise ValueError(f'Unreviewed administrative result {game["id"]}')
        if not source['penalties'] and (source['ft_home'],source['ft_away'])!=(game['ft_home'],game['ft_away']):
            correction=CORRECTIONS.get(game['espn_id'])
            if not correction or tuple(correction.get('played',()))!=(source['ft_home'],source['ft_away']):
                raise ValueError(f'Unresolved final-score conflict {game["id"]}')
        correction=CORRECTIONS.get(game['espn_id'])
        if correction:
            game['result_source_url']=correction['url']
            game['result_note']=correction['reason']
            if correction.get('played'):
                game['played_ft_home'],game['played_ft_away']=correction['played']
            if correction['administrative']:game['administrative_reason']=correction['reason']
    # These two first-division promotion games have no ESPN event in its annual table.
    for source in (a for a in archives if a['scope']=='Promoción'):
        path=cache/'reports'/(source['archive_id']+'.html.gz')
        legacy=cache.parent/'audit'/('be_report_'+source['archive_id']+'.html')
        raw=fetch(source['archive_url'],legacy if legacy.exists() else path)
        report=parse_report(raw,source)
        rows.append({**source,**report,'id':'paraguay:be:'+source['archive_id'],
                     'provider_id':'be:'+source['archive_id'],'league':'paraguay','season_label':source['season'],
                     'round_source':'BetExplorer · promoción/permanencia','status':'finished',
                     'corners':None,'corners_home':None,'corners_away':None,
                     'yellow':None,'red':None,'yellow_home':None,'yellow_away':None,'red_home':None,'red_away':None,
                     'source':'BetExplorer y RSSSF · promoción/permanencia','source_url':source['archive_url']})
        proofs.append({'url':source['archive_url'],'sha256':hashlib.sha256(raw).hexdigest()})
    conflicts=[];reports=[];report_errors=[];pending=[];round_differences=[]
    for game in rows:
        record=rsssf_index.get(key(game))
        if record is None and game['phase']=='Final':
            reverse=rsssf_index.get((game['season'],game['scope'],game['phase'],game['away'],game['home']))
            if reverse:
                record={**reverse,'ft_home':reverse['ft_away'],'ft_away':reverse['ft_home'],
                        'ht_from_goal_minutes':list(reversed(reverse['ht_from_goal_minutes'])) if reverse['ht_from_goal_minutes'] is not None else None}
        if record is None:raise ValueError(f'No independent FT archive for {game["id"]}')
        if (record['ft_home'],record['ft_away'])!=(game['ft_home'],game['ft_away']):
            raise ValueError(f'Independent FT mismatch {game["id"]}')
        if bool(record['administrative'])!=bool(game.get('administrative')):
            raise ValueError(f'Administrative status mismatch {game["id"]}')
        game['ft_verification_url']=record['source_url']
        if game['phase']=='Fase regular' and record['round']!=game['round']:
            round_differences.append({'id':game['id'],'home':game['home'],'away':game['away'],'scope':game['scope'],'season':game['season'],'archive_round':game['round'],'rsssf_round':record['round']})
        if game.get('administrative'):
            game.update(ht_home=None,ht_away=None,corners=None,corners_home=None,corners_away=None)
            continue
        if game.get('espn_id'):
            url=f"{BASE}/summary?event={game['espn_id']}"
            raw=fetch(url,cache/'summaries'/f"{game['espn_id']}.json.gz")
            game.update(enrich_match(game,json.loads(raw)))
            reports.append({'id':game['id'],'url':url,'sha256':hashlib.sha256(raw).hexdigest()})
        derived=record['ht_from_goal_minutes'];event_half=[game.get('ht_home'),game.get('ht_away')]
        if derived is not None and None not in event_half and derived!=event_half:
            conflicts.append({'id':game['id'],'rsssf':derived,'espn':event_half})
            game['ht_home']=game['ht_away']=None
        elif derived is not None and None in event_half:
            game.update(ht_home=derived[0],ht_away=derived[1],ht_source='RSSSF · '+record['ht_method'],ht_source_url=record['source_url'])
        if game.get('ht_home') is None or game.get('ht_away') is None:pending.append(game)
    print(f'HT: {len(pending)} reports to verify; {len(conflicts)} independent-source differences',flush=True)
    (output.parent/'history_inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    (output.parent/'history_ht_conflicts.json').write_text(json.dumps(conflicts,ensure_ascii=False,indent=2))
    (output.parent/'history_round_differences.json').write_text(json.dumps(round_differences,ensure_ascii=False,indent=2))
    def complete_half(game):
        url=game['archive_url'];raw=fetch(url,cache/'reports'/(game['archive_id']+'.html.gz'))
        report=parse_report(raw,game)
        return game['id'],{k:report[k] for k in ('ht_home','ht_away','ht_source','ht_source_url')},{'id':game['id'],'url':url,'sha256':hashlib.sha256(raw).hexdigest()}
    with ThreadPoolExecutor(max_workers=min(workers,4)) as pool:
        jobs={pool.submit(complete_half,g):g for g in pending};completed={}
        for count,future in enumerate(as_completed(jobs),1):
            game=jobs[future]
            try:
                ident,values,proof=future.result();completed[ident]=values;reports.append(proof)
            except Exception as error:report_errors.append({'id':game['id'],'error':str(error)})
            if count%25==0:print('Explicit HT reports',count,'/',len(jobs),flush=True)
    for game in rows:game.update(completed.get(game['id'],{}))
    rows.sort(key=lambda g:(g['season'],{'Apertura':0,'Clausura':1,'Promoción':2}.get(g['scope'],9),{'Fase regular':0,'Cuartos de final':1,'Semifinal':2,'Final':3,'Promoción':4}.get(g['phase'],9),g.get('round') or 99,g['date'],g.get('time') or '',g['id']))
    counters=Counter()
    for game in rows:counters[game['season']]+=1;game['source_order']=counters[game['season']]
    output.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    played=[g for g in rows if g['status']=='finished' and not g.get('administrative')]
    qa={'captured_at':datetime.now(timezone.utc).isoformat(),'matches':len(rows),'seasons':dict(counters),
        'missing_in_played':{field:sum(g.get(field) is None for g in played) for field in ('ht_home','ht_away','ft_home','ft_away','time','corners')},
        'missing_rounds':sum(g.get('round') is None and not g.get('round_not_applicable') for g in rows),
        'administrative_cases':[{k:g.get(k) for k in ('id','date','home','away','administrative_reason')} for g in rows if g.get('administrative')],
        'report_errors':report_errors,'resolved_ht_source_conflicts':len(conflicts)-sum(e['id'] in {c['id'] for c in conflicts} for e in report_errors)}
    (output.parent/'history_import_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n')
    (output.parent/'history_source_evidence.json').write_text(json.dumps(proofs+reports,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(qa,ensure_ascii=False,indent=2),flush=True)
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--start',type=int,default=2018);parser.add_argument('--end',type=int,default=2025);parser.add_argument('--workers',type=int,default=4);parser.add_argument('--cache',type=Path,default=Path('/workspace/paraguay-data/history-cache'));parser.add_argument('--output',type=Path,default=Path('/workspace/paraguay-data/history_games.json'))
    args=parser.parse_args();import_history(args.start,args.end,args.cache,args.output,args.workers)
