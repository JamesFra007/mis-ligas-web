"""Collect audited Serie A HT/FT, corners and discipline without changing app DB.

Football-Data supplies the regular-season statistics. BetExplorer's explicit
round headings verify matchdays and its event reports restore the missing 2018
kickoffs. The official 2023 relegation playoff is retained as a separate match,
using ESPN's populated boxscore and a separately verified period score.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.italy import parse_football_data
from app.italy_names import normalize_team

BOGOTA = ZoneInfo('America/Bogota')
WARSAW = ZoneInfo('Europe/Warsaw')
BASE = 'https://www.betexplorer.com'


def plain(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', value or '')).split())


def fetch(url, path):
    path = Path(path)
    if path.exists():
        return path.read_text(encoding='utf-8-sig')
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent':'MisLigas/1.0 (Serie A archive)'}), timeout=25) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('Archive response too large')
            text = raw.decode('utf-8-sig')
            temporary = path.with_suffix(path.suffix + '.new')
            temporary.write_bytes(raw)
            temporary.replace(path)
            return text
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                delay = float(error.headers.get('Retry-After') or '1')
            except ValueError:
                delay = 3
            if delay > 30:
                raise RuntimeError('Source requested a long pause; retry this cached import later') from error
            time.sleep(max(1, delay) * (attempt + 1))
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError('Could not fetch ' + url)


def parse_results(html, start_year=None):
    table = re.search(r'<table\b[^>]*class=["\'][^"\']*\btable-main\b[^"\']*["\'][^>]*>(.*?)</table>', html, re.S | re.I)
    if table is None:
        raise ValueError('No public result table')
    result = []
    number = None
    for raw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', table[1], re.S | re.I):
        heading = re.search(r'(\d+)\.\s*Round', plain(raw))
        if heading:
            number = int(heading[1])
            continue
        cols = re.findall(r'<td\b[^>]*>(.*?)</td>', raw, re.S | re.I)
        if len(cols) < 3:
            continue
        anchor = re.search(r'<a\b(?=[^>]*class=["\'][^"\']*\bin-match\b)[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', cols[0], re.S | re.I)
        if anchor is None:
            continue
        url = urljoin(BASE, unescape(anchor[1]))
        ident = url.rstrip('/').rsplit('/', 1)[-1]
        names = [normalize_team(plain(x)) for x in re.findall(r'<span\b[^>]*>(.*?)</span>', anchor[2], re.S | re.I)]
        score = re.match(r'^\s*(\d{1,2})\s*:\s*(\d{1,2})', plain(cols[1]))
        day = re.search(r'(\d{1,2})\.(\d{1,2})\.(20\d\d)?', plain(cols[-1]))
        if len(names) != 2 or score is None or day is None or not re.fullmatch(r'[A-Za-z0-9]{8}', ident):
            raise ValueError('Incomplete archive observation: ' + ident)
        year = int(day[3]) if day[3] else start_year if int(day[2]) >= 7 else start_year + 1
        result.append({'home':names[0], 'away':names[1], 'round':number,
                       'date':datetime(year, int(day[2]), int(day[1])).date().isoformat(),
                       'ft_home':int(score[1]), 'ft_away':int(score[2]),
                       'report_url':url, 'archive_id':ident,
                       'awarded':bool(re.search(r'\bAWA\.|\bW\.O\.|title=["\']Awarded["\']', raw, re.I))})
    return result


def enrich_report(game, html):
    result = dict(game)
    score = re.search(r'<[^>]*id=["\']js-score["\'][^>]*>(.*?)</[^>]+>', html, re.S | re.I)
    final = re.fullmatch(r'(\d{1,2})\s*:\s*(\d{1,2})', plain(score[1])) if score else None
    if final is None or tuple(map(int, final.groups())) != (game['ft_home'], game['ft_away']):
        raise ValueError('Independent report final score disagrees: ' + game['id'])
    partial = re.search(r'<[^>]*id=["\']js-partial["\'][^>]*>(.*?)</[^>]+>', html, re.S | re.I)
    halves = re.fullmatch(r'\(?\s*(\d{1,2})\s*:\s*(\d{1,2})\s*,\s*(\d{1,2})\s*:\s*(\d{1,2})\s*\)?', plain(partial[1])) if partial else None
    if halves:
        home, away, home2, away2 = map(int, halves.groups())
        if (home + home2, away + away2) != (game['ft_home'], game['ft_away']):
            raise ValueError('Report periods disagree: ' + game['id'])
        if game.get('ht_home') is not None and (home, away) != (game['ht_home'], game['ht_away']):
            raise ValueError('Independent report HT disagrees: ' + game['id'])
        result.update(ht_home=home, ht_away=away)
        result['ht_verification_url'] = game['archive_url']
    for block in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(unescape(block))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict) or not item.get('startDate'):
                continue
            stamp = datetime.fromisoformat(item['startDate'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=WARSAW)
            local = stamp.astimezone(BOGOTA)
            if game.get('time_confirmed') and game.get('kickoff'):
                previous = datetime.fromisoformat(game['kickoff'].replace('Z', '+00:00'))
                if stamp.astimezone(timezone.utc) != previous.astimezone(timezone.utc):
                    raise ValueError('Independent kickoff disagrees: ' + game['id'])
            result.update(date=local.date().isoformat(), time=local.strftime('%H:%M'),
                          kickoff=stamp.astimezone(timezone.utc).isoformat(),
                          date_confirmed=True, time_confirmed=True, time_source_url=game['archive_url'])
            return result
    return result


def extra_2023(cache):
    url = f'{BASE}/football/italy/serie-a-2022-2023/results/?stage=lh26l7Zn'
    rows = parse_results(fetch(url, cache / '2223_extra.html'), 2022)
    if len(rows) != 1 or (rows[0]['home'], rows[0]['away']) != ('Spezia', 'Verona'):
        raise ValueError('Unexpected relegation playoff archive')
    raw = rows[0]
    source = 'https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/summary?event=670116'
    summary = json.loads(fetch(source, cache / 'reports' / '670116.json'))
    comp = summary['header']['competitions'][0]
    if not comp.get('status', {}).get('type', {}).get('completed'):
        raise ValueError('The playoff report is not finished')
    game = {'id':'italy:be:' + raw['archive_id'], 'provider_id':'be:' + raw['archive_id'],
            'league':'italy', 'season':'2022-23', 'season_label':'2022/23',
            'home':raw['home'], 'away':raw['away'], 'round':None,
            'scope':'Desempate por la permanencia', 'phase':'Desempate por la permanencia',
            'round_not_applicable':True,
            'date':raw['date'], 'time':None, 'kickoff':None, 'date_confirmed':True, 'time_confirmed':False,
            'status':'finished', 'ht_home':None, 'ht_away':None,
            'ft_home':raw['ft_home'], 'ft_away':raw['ft_away'],
            'administrative':False, 'source':'ESPN · Serie A · desempate; BetExplorer · periodos y hora',
            'source_url':source, 'archive_url':raw['report_url'], 'espn_id':'670116',
            'venue':'Mapei Stadium – Città del Tricolore (Reggio Emilia)'}
    sides = {x['homeAway']:x for x in comp['competitors']}
    teams = {x['homeAway']:x for x in summary['boxscore']['teams']}
    for side in ('home', 'away'):
        observed = sides[side]
        if normalize_team(observed['team']['displayName']) != game[side] or int(observed['score']) != game['ft_' + side]:
            raise ValueError('ESPN playoff identity/score disagrees')
        stats = {x['name']:x['displayValue'] for x in teams[side]['statistics']}
        if float(stats.get('totalShots', '0')) <= 0:
            raise ValueError('Unpopulated playoff boxscore')
        for target, name in [('corners','wonCorners'), ('yellow','yellowCards'), ('red','redCards')]:
            value = stats.get(name)
            game[target + '_' + side] = int(value) if value is not None and str(value).isdigit() else None
    for target in ('corners', 'yellow', 'red'):
        values = [game[target + '_' + side] for side in ('home', 'away')]
        game[target] = sum(values) if None not in values else None
    game = enrich_report(game, fetch(raw['report_url'], cache / 'reports' / (raw['archive_id'] + '.html')))
    return game


def import_history(cache, output, workers=4):
    cache = Path(cache); output = Path(output)
    games = []; evidence = []; errors = []
    def load(year):
        season = f'{year}-{(year+1)%100:02d}'; code = f'{year%100:02d}{(year+1)%100:02d}'
        csv_url = f'https://www.football-data.co.uk/mmz4281/{code}/I1.csv'
        archive = f'{BASE}/football/italy/serie-a-{year}-{year+1}/results/'
        csv_text = fetch(csv_url, cache / (code + '.csv'))
        table_text = fetch(archive, cache / (code + '.html'))
        records = parse_football_data(csv_text, season); observed = parse_results(table_text, year)
        if len(records) != 380 or len(observed) != 380:
            raise ValueError(f'{season}: regular season does not contain 380 observations')
        lookup = {(g['home'], g['away']):g for g in observed}
        if len(lookup) != 380 or set(lookup) != {(g['home'], g['away']) for g in records}:
            raise ValueError(f'{season}: unmatched clubs or duplicate archive fixture')
        if Counter(g['round'] for g in observed) != Counter({n:10 for n in range(1,39)}):
            raise ValueError(f'{season}: incomplete published matchdays')
        for game in records:
            seen = lookup[game['home'], game['away']]
            awarded_2020 = year == 2020 and (game['home'], game['away'], game['date']) == ('Verona', 'Roma', '2020-09-19') and seen['awarded'] and (seen['ft_home'],seen['ft_away']) == (3,0)
            if awarded_2020:
                game.update(administrative=True, administrative_reason='Resultado administrativo 3-0 por alineación irregular de Roma; el partido disputado terminó 0-0. Fuera del análisis.',
                            played_ft_home=game['ft_home'], played_ft_away=game['ft_away'],
                            played_ht_home=game['ht_home'], played_ht_away=game['ht_away'],
                            ft_home=3, ft_away=0, ht_home=None, ht_away=None,
                            administrative_source_url=seen['report_url'])
            if (seen['ft_home'], seen['ft_away']) != (game['ft_home'], game['ft_away']) or seen['date'] != game['date']:
                raise ValueError('Independent result/date disagrees: ' + game['id'])
            if seen['awarded'] and not awarded_2020:
                raise ValueError('An awarded result requires separate manual verification: ' + game['id'])
            game.update(round=seen['round'], scope='first' if seen['round'] <= 19 else 'second',
                        round_source='BetExplorer · jornada explícita', round_source_url=archive,
                        archive_url=seen['report_url'], archive_id=seen['archive_id'])
        proofs = [{'url':url, 'sha256':hashlib.sha256(text.encode()).hexdigest(), 'records':380}
                  for url,text in [(csv_url,csv_text),(archive,table_text)]]
        return records, proofs
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(load,year):year for year in range(2018,2026)}
        for future in as_completed(futures):
            try:
                records, proofs = future.result(); games.extend(records); evidence.extend(proofs)
                print('Regular season', futures[future], len(records), flush=True)
            except Exception as error:
                errors.append({'year':futures[future], 'error':str(error)})
    output.parent.mkdir(parents=True, exist_ok=True)
    if errors:
        (output.parent / 'history_table_errors.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2))
        raise RuntimeError('Incomplete history tables: ' + str(errors))
    games.append(extra_2023(cache))
    (output.parent / 'history_inventory.json').write_text(json.dumps(games, ensure_ascii=False, indent=2))
    # All missing kickoffs plus a small independent HT/clock sample per later season.
    selected = [g for g in games if not g.get('time_confirmed')]
    for season in sorted({g['season'] for g in games}):
        known = [g for g in sorted(games, key=lambda g:g['id']) if g['season']==season and g.get('time_confirmed') and g['phase']=='Serie A' and not g.get('administrative')]
        selected.extend(known[::max(1,len(known)//5)][:5])
    updated = {g['id']:g for g in games}
    def report(game):
        text = fetch(game['archive_url'], cache / 'reports' / (game['archive_id'] + '.html'))
        return enrich_report(game,text), {'id':game['id'],'url':game['archive_url'],'sha256':hashlib.sha256(text.encode()).hexdigest()}
    report_errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(report,g):g for g in selected}
        for index,future in enumerate(as_completed(futures),1):
            original=futures[future]
            try:
                game,proof=future.result();updated[game['id']]=game;evidence.append(proof)
            except Exception as error:
                report_errors.append({'id':original['id'],'url':original['archive_url'],'error':str(error)})
            if index % 50 == 0:
                print('Independent reports',index,'/',len(selected),flush=True)
    games=sorted(updated.values(),key=lambda g:(g['season'],g['round'] if g['round'] is not None else 99,g['date'],g.get('time') or '',g['id']))
    positions=Counter()
    for game in games:
        positions[game['season']]+=1;game['source_order']=positions[game['season']]
        assert game['ht_home'] is None or game['ht_home']<=game['ft_home']
        assert game['ht_away'] is None or game['ht_away']<=game['ft_away']
    summaries=[]
    fields=('ht_home','ht_away','ft_home','ft_away','corners','yellow','red','corners_home','corners_away','yellow_home','yellow_away','red_home','red_away')
    for season in sorted(positions):
        items=[g for g in games if g['season']==season]
        eligible=[g for g in items if not g.get('administrative')]
        summaries.append({'season':season,'records':len(items),'regular':sum(g['phase']=='Serie A' for g in items),
                          'extras':sum(g['phase']!='Serie A' for g in items),
                          'administrative':sum(bool(g.get('administrative')) for g in items),
                          'ft_goals':sum(g['ft_home']+g['ft_away'] for g in items),
                          'analyzed_ft_goals':sum(g['ft_home']+g['ft_away'] for g in eligible),
                          'ht_goals':sum(g['ht_home']+g['ht_away'] for g in items if g['ht_home'] is not None and g['ht_away'] is not None),
                          'corners_total':sum(g['corners'] for g in items if g['corners'] is not None),
                          'yellow_total':sum(g['yellow'] for g in items if g['yellow'] is not None),
                          'red_total':sum(g['red'] for g in items if g['red'] is not None),
                          'missing':{field:sum(g.get(field) is None for g in eligible) for field in fields},
                          'missing_time':[g['id'] for g in items if not g.get('time_confirmed')],
                          'teams':sorted({g[k] for g in items for k in ('home','away')})})
    qa={'source':'Football-Data I1; explicit BetExplorer matchdays/reports; ESPN official playoff boxscore',
        'captured_at':datetime.now(timezone.utc).isoformat(),'matches':len(games),'seasons':summaries,
        'administrative_cases':[{'id':g['id'],'home':g['home'],'away':g['away'],'date':g['date'],'reason':g.get('administrative_reason'),'source_url':g.get('administrative_source_url')} for g in games if g.get('administrative')],
        'table_errors':errors,'report_errors':report_errors}
    output.write_text(json.dumps(games,ensure_ascii=False,indent=2)+'\n')
    (output.parent/'history_import_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n')
    (output.parent/'history_source_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(qa,ensure_ascii=False,indent=2),flush=True)
    return games,qa


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,default=ROOT.parent/'italy-data/history-cache')
    parser.add_argument('--output',type=Path,default=ROOT.parent/'italy-data/history_games.json')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();import_history(args.cache,args.output,args.workers)
