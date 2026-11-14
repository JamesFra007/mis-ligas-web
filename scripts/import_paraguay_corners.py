"""Read published TotalCorner tables and match observed corners to ESPN IDs.

Only completed matches, exact canonical teams, UTC kickoffs and FT results join.
This script writes evidence and an enrichment file; it does not alter the app DB.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
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
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.paraguay import parse_espn_event, enrich_match
from app.paraguay_names import normalize_team

BASE = 'https://www.totalcorner.com'
START = BASE + '/league/view/302'
STORE = Path('/workspace/paraguay-data/corners-research')
MONTHS = {name:i for i,name in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'),1)}


def plain(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', value or '')).split())


def cell(row, cls):
    for attrs, value in re.findall(r'<td\b([^>]*)>(.*?)</td>', row, re.S|re.I):
        m = re.search(r'\bclass=["\']([^"\']*)["\']', attrs, re.I)
        if m and cls in m[1].split():
            return plain(value)
    return None


def pair(value):
    match = re.fullmatch(r'\s*(\d{1,3})\s*-\s*(\d{1,3})\s*', value or '')
    return tuple(map(int, match.groups())) if match else None


def page_number(url):
    m = re.search(r'/page:(\d+)$', urlsplit(url).path)
    return int(m[1]) if m else 1


def public_links(html):
    pagination = re.search(r'<ul\b[^>]*class=["\'][^"\']*\bpagination\b[^"\']*["\'][^>]*>(.*?)</ul>', html, re.S|re.I)
    result = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', pagination[1] if pagination else ''):
        url = urljoin(START, unescape(href))
        parsed = urlsplit(url)
        if parsed.hostname == 'www.totalcorner.com' and re.fullmatch(r'/league/view/302(?:/page:\d+)?', parsed.path):
            result.add(url)
    return result


def parse_page(html, url):
    rows, year, month = [], None, None
    for attrs, raw in re.findall(r'<tr\b([^>]*)>(.*?)</tr>', html, re.S|re.I):
        if 'league-match-row' not in attrs:
            heading = re.search(r'\b('+'|'.join(MONTHS)+r')\s+(20\d\d)\b', plain(raw))
            if heading:
                month, year = MONTHS[heading[1]], int(heading[2])
            continue
        ident = re.search(r'\bdata-match_id=["\'](\d+)["\']', attrs)
        status = cell(raw, 'league-status')
        if not ident or status not in ('Full', 'FT'):
            continue
        clock = re.fullmatch(r'(\d{2})/(\d{2})\s+(\d{2}):(\d{2})', cell(raw, 'league-time') or '')
        if clock is None or year is None:
            raise ValueError('A completed TotalCorner match lacks its year/kickoff.')
        mm, dd, hh, minute = map(int, clock.groups())
        # Table headings can retain their original timezone month while the
        # public tz preference changes individual kickoff cells at boundaries.
        observed_year = year - 1 if month == 1 and mm == 12 else year + 1 if month == 12 and mm == 1 else year
        stamp = datetime(observed_year, mm, dd, hh, minute, tzinfo=timezone.utc)
        ft = pair(cell(raw, 'league-score'))
        corners = pair(cell(raw, 'league-full-corner'))
        half = pair(cell(raw, 'league-half-corner'))
        home, away = normalize_team(cell(raw, 'league-home')), normalize_team(cell(raw, 'league-away'))
        if not home or not away or ft is None:
            raise ValueError('A completed TotalCorner match is missing its teams or FT score.')
        # The listing's future 0-0 placeholders were excluded by status above.
        # Both-zero corner entries need independent evidence, even if marked FT.
        known = corners is not None and sum(corners) > 0 and (half is None or all(h <= f for h,f in zip(half,corners)))
        stats = re.search(r'href=["\'](/stats/[^"\']+)["\']', raw)
        rows.append({'totalcorner_id':ident[1], 'home':home, 'away':away,
                     'kickoff':stamp.isoformat(), 'date':stamp.date().isoformat(),
                     'ft_home':ft[0], 'ft_away':ft[1],
                     'corners_home':corners[0] if known else None,
                     'corners_away':corners[1] if known else None,
                     'corners':sum(corners) if known else None,
                     'published_corners':list(corners) if corners is not None else None,
                     'published_half_corners':list(half) if half is not None else None,
                     'corners_source':'TotalCorner · estadísticas FT publicadas',
                     'corners_source_url':urljoin(BASE,stats[1]) if stats else url,
                     'list_source_url':url,'timezone':'UTC (requested via public tz preference)',
                     'quarantine_reason':None if known else 'No verified corner observations (empty, both-zero, or inconsistent periods).'})
    return rows


def fetch(url, cache):
    path = cache / f'page-{page_number(url):03d}.html'
    if path.exists():
        return path.read_text(), path
    for attempt in range(3):
        try:
            req = Request(url, headers={'User-Agent':'MisLigas/1.0 (sports archive)',
                                        'Accept':'text/html', 'Cookie':'tz=UTC'})
            with urlopen(req,timeout=25) as response:
                raw = response.read(3*1024*1024+1)
            if len(raw)>3*1024*1024:
                raise ValueError('Source response exceeds the expected size.')
            html = raw.decode('utf-8-sig')
            if 'league-match-row' not in html:
                raise ValueError('TotalCorner did not return a public match table.')
            temp=path.with_suffix('.new.html');temp.write_text(html);temp.replace(path)
            return html, path
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==2:
                raise
            try:pause=float(error.headers.get('Retry-After') or '2')
            except ValueError:pause=3
            if pause>30:
                raise RuntimeError('Source requests a longer pause; retry the cached import later.') from error
            time.sleep(max(1,pause)*(attempt+1))
        except (URLError,TimeoutError):
            if attempt==2:raise
            time.sleep(attempt+1)
    raise RuntimeError('Could not read public corner table.')


def crawl(start_year=2018, end_year=2026):
    cache=STORE/'utc-pages';cache.mkdir(parents=True,exist_ok=True)
    pending={START};visited=set();records={};proofs=[];errors=[];last_page=120
    while pending:
        batch=sorted((u for u in pending if page_number(u)<=last_page),key=page_number)[:4]
        if not batch:break
        pending.difference_update(batch)
        def load(url):
            try:
                html,path=fetch(url,cache)
                return url,html,path,None
            except Exception as error:return url,None,None,str(error)
        with ThreadPoolExecutor(max_workers=min(4,len(batch))) as pool:
            results=list(pool.map(load,batch))
        for url,html,path,error in results:
            visited.add(url)
            if error:
                errors.append({'url':url,'error':error});continue
            rows=parse_page(html,url)
            for row in rows:
                if start_year<=int(row['date'][:4])<=end_year:
                    if row['totalcorner_id'] in records and records[row['totalcorner_id']]!=row:
                        # Same match may overlap listings; ignore its page URL.
                        old={k:v for k,v in records[row['totalcorner_id']].items() if k!='list_source_url'}
                        new={k:v for k,v in row.items() if k!='list_source_url'}
                        if old!=new:errors.append({'id':row['totalcorner_id'],'error':'Conflicting source records.'})
                    records[row['totalcorner_id']]=row
            years=[int(row['date'][:4]) for row in rows]
            if years and min(years)<start_year:last_page=min(last_page,page_number(url))
            pending.update(u for u in public_links(html) if u not in visited and page_number(u)<=last_page)
            proofs.append({'url':url,'timezone_cookie':'UTC','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'completed':len(rows),'oldest':min((r['date'] for r in rows),default=None)})
        print(json.dumps({'pages':len(visited),'matches':len(records),'oldest':min((r['date'] for r in records.values()),default=None),'pending_pages':len(pending),'errors':len(errors)}),flush=True)
    output=sorted(records.values(),key=lambda r:(r['kickoff'],r['totalcorner_id']))
    (STORE/'totalcorner_records.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    (STORE/'source_evidence.json').write_text(json.dumps(proofs,ensure_ascii=False,indent=2)+'\n')
    (STORE/'crawl_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2)+'\n')
    return output,errors


def espn_rows():
    rows=[]
    for year in range(2018,2027):
        path=Path('/workspace/paraguay-data/history-cache')/f'espn-{year}.json'
        if year==2026:path=Path('/workspace/paraguay-data/provider-cache/scoreboard2026.json')
        if not path.exists():continue
        data=json.loads(path.read_text())
        rows.extend(g for event in data['events'] if (g:=parse_espn_event(event,str(year))) is not None)
    return rows


def match_observations(records):
    indexed=defaultdict(list); date_indexed=defaultdict(list)
    games=espn_rows()
    for game in games:
        if game['status']=='finished' and game.get('kickoff'):
            stamp=datetime.fromisoformat(game['kickoff'].replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
            indexed[(game['home'],game['away'],stamp)].append(game)
            date_indexed[(game['home'],game['away'],game['date'],game['ft_home'],game['ft_away'])].append(game)
    matched={};unmatched=[];conflicts=[];quarantined=[]
    for record in records:
        if record['corners'] is None:
            quarantined.append(record);continue
        candidates=indexed.get((record['home'],record['away'],record['kickoff']),[])
        if len(candidates)!=1:
            unmatched.append({'record':record,'candidates':len(candidates)});continue
        game=candidates[0]
        if (game['ft_home'],game['ft_away'])!=(record['ft_home'],record['ft_away']):
            conflicts.append({'id':game['id'],'source':record,'espn_ft':[game['ft_home'],game['ft_away']]});continue
        key=game['id']
        row={'id':key,'espn_id':game['espn_id'],'league':'paraguay','season':game['season'],
             'home':game['home'],'away':game['away'],'date':game['date'],'time':game['time'],'kickoff':game['kickoff'],
             'scope':game['scope'],'ft_home':game['ft_home'],'ft_away':game['ft_away'],
             **{k:record[k] for k in ('corners','corners_home','corners_away','corners_source','corners_source_url','totalcorner_id','list_source_url')},
             'match_validation':'Exact canonical teams, UTC kickoff and FT score.'}
        if key in matched and any(matched[key][k]!=row[k] for k in ('corners','corners_home','corners_away')):
            conflicts.append({'id':key,'first':matched[key],'second':row});continue
        matched[key]=row
    date_candidates=[]
    for entry in unmatched:
        record=entry['record']
        from zoneinfo import ZoneInfo
        local=datetime.fromisoformat(record['kickoff']).astimezone(ZoneInfo('America/Bogota')).date().isoformat()
        choices=date_indexed.get((record['home'],record['away'],local,record['ft_home'],record['ft_away']),[])
        if len(choices)==1:
            game=choices[0]
            candidate={**record,'id':game['id'],'espn_id':game['espn_id'],'season':game['season'],'scope':game['scope'],
                       'espn_kickoff':game['kickoff'],'match_validation':'Exact canonical teams, Colombia date and FT score; kickoff times differ. Not included in strict enrichment.'}
            date_candidates.append(candidate)
    existing_conflicts=[];existing_known=Counter();overlap=0;fills=0
    by_id={g['id']:g for g in games}
    for game in games:
        p=Path('/workspace/paraguay-data/history-cache/summaries')/(game['espn_id']+'.json.gz')
        if game['season']=='2026':p=Path('/workspace/paraguay-data/provider-cache')/('summary'+game['espn_id']+'.json')
        if p.exists():
            raw=gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes()
            enriched=enrich_match(game,json.loads(raw))
            by_id[game['id']]=enriched
            if enriched.get('corners') is not None:existing_known[game['season']]+=1
    for key in list(matched):
        game=by_id[key];record=matched[key]
        bad={name:{'espn':game.get(name),'totalcorner':record.get(name)} for name in ('corners','corners_home','corners_away') if game.get(name) is not None and game[name]!=record[name]}
        if bad:
            existing_conflicts.append({'id':key,'differences':bad,'record':record,'espn_source_url':f"https://site.api.espn.com/apis/site/v2/sports/soccer/par.1/summary?event={game['espn_id']}"})
            del matched[key]
        elif game.get('corners') is not None:overlap+=1
        else:fills+=1
    output=sorted(matched.values(),key=lambda r:(r['season'],r['kickoff'],r['id']))
    (STORE/'corner_enrichment.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    (STORE/'unmatched_records.json').write_text(json.dumps(unmatched,ensure_ascii=False,indent=2)+'\n')
    (STORE/'conflicting_records.json').write_text(json.dumps(conflicts,ensure_ascii=False,indent=2)+'\n')
    (STORE/'quarantined_records.json').write_text(json.dumps(quarantined,ensure_ascii=False,indent=2)+'\n')
    (STORE/'corner_date_match_candidates.json').write_text(json.dumps(date_candidates,ensure_ascii=False,indent=2)+'\n')
    (STORE/'corner_source_conflicts.json').write_text(json.dumps(existing_conflicts,ensure_ascii=False,indent=2)+'\n')
    report={'source':'TotalCorner public league302 tables','timezone':'UTC requested via documented tz cookie preference',
            'published_records':len(records),'matched':len(output),'per_year':dict(Counter(r['season'] for r in output)),
            'unmatched':len(unmatched),'conflicts':len(conflicts),'quarantined':len(quarantined),
            'existing_espn_known_per_year':dict(existing_known),'already_known_and_agrees':overlap,
            'fills_unpublished_espn_corners':fills,'known_corner_source_conflicts':len(existing_conflicts),'same_date_candidates_not_applied':len(date_candidates),
            'validation':'Exact UTC kickoff, canonical team pair and FT score; upcoming placeholders excluded.'}
    (STORE/'corner_enrichment_qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--join-only',action='store_true')
    args=parser.parse_args()
    if args.join_only:records=json.loads((STORE/'totalcorner_records.json').read_text())
    else:records,errors=crawl()
    match_observations(records)
