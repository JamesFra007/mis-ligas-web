"""Serie A statistics and fixtures from Football-Data and the public ESPN feed.

CSV observations remain the authority for corners and discipline. Calendar
metadata is reconciled by canonical fixture, never by date or inferred round.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime, timezone
from html import unescape
import io
import logging
import re
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from .providers import ProviderError, _fetch, _json, _integer, _total, _espn_clock, _espn_status, _stats
from .italy_names import normalize_team

BOGOTA=ZoneInfo('America/Bogota')
LONDON=ZoneInfo('Europe/London')
ESPN_BASE='https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1'
MAX_SUMMARIES=40
LOGGER=logging.getLogger(__name__)
_COVERAGE={}


def season_year(season):
    match=re.fullmatch(r'(20\d\d)-(\d\d)',str(season))
    if not match or int(match[2])!=(int(match[1])+1)%100:
        raise ProviderError('La temporada italiana debe tener el formato 2026-27.')
    return int(match[1])


def parse_football_data(text,season):
    """Decode a real I1 CSV without converting blanks into zero observations."""
    year=season_year(season);code=f'{year%100:02d}{(year+1)%100:02d}'
    source=f'https://www.football-data.co.uk/mmz4281/{code}/I1.csv'
    if isinstance(text,bytes):
        try:text=text.decode('utf-8-sig')
        except UnicodeError as error:raise ProviderError('El CSV italiano no es legible.') from error
    try:
        reader=csv.DictReader(io.StringIO(text))
        required={'Date','HomeTeam','AwayTeam','FTHG','FTAG','HTHG','HTAG'}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ProviderError('El CSV italiano no contiene las columnas de resultados esperadas.')
        rows=[];seen=set()
        for raw in reader:
            home_raw=(raw.get('HomeTeam') or '').strip();away_raw=(raw.get('AwayTeam') or '').strip()
            if not home_raw or not away_raw:continue
            key=(home_raw,away_raw)
            if key in seen:raise ProviderError('El CSV italiano repite un partido.')
            seen.add(key)
            value=raw['Date'].strip();fmt='%d/%m/%Y' if len(value.rsplit('/',1)[-1])==4 else '%d/%m/%y'
            day=datetime.strptime(value,fmt)
            # COVID-delayed 2019/20 genuinely ended in August 2020.
            season_end=date(year+1,9,1) if year==2019 else date(year+1,7,1)
            if not date(year,7,1)<=day.date()<season_end:
                raise ProviderError('El CSV italiano mezcla temporadas distintas.')
            time=(raw.get('Time') or '').strip();stamp=None
            if time:
                stamp=datetime.combine(day.date(),datetime.strptime(time,'%H:%M').time(),LONDON)
                local=stamp.astimezone(BOGOTA)
            else:local=day
            fh,fa=_integer(raw.get('FTHG'),30),_integer(raw.get('FTAG'),30)
            status='finished' if fh is not None and fa is not None else 'scheduled'
            if (stamp is not None and stamp.astimezone(timezone.utc)>datetime.now(timezone.utc)) or (stamp is None and day.date()>datetime.now(BOGOTA).date()):
                status='scheduled'
            hh,ha=_integer(raw.get('HTHG'),30),_integer(raw.get('HTAG'),30)
            if status!='finished':fh=fa=hh=ha=None
            elif hh is not None and ha is not None and (hh>fh or ha>fa):hh=ha=None
            stat=lambda name:_integer(raw.get(name),100) if status=='finished' else None
            yh,ya,rh,ra=[stat(name) for name in ('HY','AY','HR','AR')]
            rows.append({
                'id':f'italy:fd:{code}:{home_raw}:{away_raw}','provider_id':f'fd:{code}:{home_raw}:{away_raw}',
                'league':'italy','season':str(season),'season_label':str(season).replace('-','/'),
                'home':normalize_team(home_raw),'away':normalize_team(away_raw),
                'date':local.date().isoformat(),'time':local.strftime('%H:%M') if stamp else None,
                'kickoff':stamp.astimezone(timezone.utc).isoformat() if stamp else None,
                'date_confirmed':True,'time_confirmed':bool(stamp),
                'round':None,'scope':'all','phase':'Serie A','status':status,
                'ht_home':hh,'ht_away':ha,'ft_home':fh,'ft_away':fa,
                'corners':_total(stat('HC'),stat('AC')),'corners_home':stat('HC'),'corners_away':stat('AC'),'yellow':_total(yh,ya),'red':_total(rh,ra),
                'yellow_home':yh,'yellow_away':ya,'red_home':rh,'red_away':ra,
                'administrative':False,'source':'Football-Data · CSV I1','source_url':source,
            })
    except (ValueError,TypeError,KeyError,csv.Error) as error:
        raise ProviderError('El CSV italiano contiene una fecha o un formato inesperado.') from error
    if not rows:raise ProviderError('La fuente todavía no publica resultados de esta temporada italiana.')
    return rows


def football_data(season):
    year=season_year(season);code=f'{year%100:02d}{(year+1)%100:02d}'
    return parse_football_data(_fetch(f'https://www.football-data.co.uk/mmz4281/{code}/I1.csv'),season)


def parse_espn_event(event,season):
    year=season_year(season)
    comps=event.get('competitions') or []
    if not comps or not event.get('id'):return None
    comp=comps[0]
    sides={str(c.get('homeAway')):c for c in comp.get('competitors',[])}
    if set(sides)!={'home','away'}:return None
    home=normalize_team(sides['home'].get('team',{}).get('displayName'))
    away=normalize_team(sides['away'].get('team',{}).get('displayName'))
    if not home or not away:return None
    state=event.get('status',comp.get('status',{})).get('type',{})
    if state.get('state') not in ('pre','in','post'):
        raise ProviderError('El calendario italiano publicó un estado desconocido.')
    status=_espn_status(state)
    clock=_espn_clock(event,comp,status)
    if clock is None:return None
    if not date(year,7,1)<=date.fromisoformat(clock['date'])<date(year+1,7,1):return None
    slug=str(event.get('season',{}).get('slug','')).lower()
    match=re.search(r'(20\d\d)-(\d\d)',slug)
    if match and match[0]!=str(season):return None
    if clock.get('kickoff') and status=='finished' and datetime.fromisoformat(clock['kickoff'])>datetime.now(timezone.utc):
        status='scheduled'
    name=str(state.get('name','')).upper()
    administrative=any(word in name for word in ('AWARD','ABANDON'))
    notes=' '.join(str(n.get('text','')) for n in comp.get('notes',[]) if isinstance(n,dict)).lower()
    playoff='relegation playoff' in notes or 'relegation-playoff' in slug or 'relegation_playoff' in slug
    round_number=_integer((event.get('week') or comp.get('week') or {}).get('number'),38)
    if round_number==0:round_number=None
    phase='Desempate por la permanencia' if playoff else 'Serie A'
    scope=phase if playoff else 'first' if round_number is not None and round_number<=19 else 'second' if round_number is not None else 'all'
    if playoff:round_number=None
    ft=[_integer(sides[side].get('score'),30) if status=='finished' else None for side in ('home','away')]
    if status=='finished' and None in ft:
        raise ProviderError('El calendario italiano omite un marcador final de un partido terminado.')
    ht=[]
    for side in ('home','away'):
        lines=sides[side].get('linescores') or []
        first=next((x for x in lines if x.get('period')==1),None)
        ht.append(_integer(first.get('displayValue',first.get('value')),30) if first and status=='finished' and not administrative else None)
    if all(h is not None and f is not None for h,f in zip(ht,ft)) and any(h>f for h,f in zip(ht,ft)):
        ht=[None,None]
    stats={side:_stats(sides[side]) for side in ('home','away')}
    corners=[stats[side]['corners'] if status=='finished' and not administrative else None for side in ('home','away')]
    return {
        'id':'italy:espn:'+str(event['id']),'provider_id':'espn:'+str(event['id']),'espn_id':str(event['id']),
        'league':'italy','season':str(season),'season_label':str(season).replace('-','/'),
        **clock,'home':home,'away':away,'round':round_number,'scope':scope,'phase':phase,'status':status,
        'ht_home':ht[0],'ht_away':ht[1],'ft_home':ft[0],'ft_away':ft[1],
        'corners':_total(*corners),'corners_home':corners[0],'corners_away':corners[1],
        'yellow':None,'red':None,'yellow_home':None,'yellow_away':None,'red_home':None,'red_away':None,
        'administrative':administrative,'administrative_reason':'Resultado administrativo o abandono según ESPN.' if administrative else None,
        'source':'ESPN · Serie A','source_url':'https://www.espn.com/soccer/match/_/gameId/'+str(event['id']),
    }


def calendar(season):
    year=season_year(season);games={}
    urls=[f'{ESPN_BASE}/scoreboard?dates={y}&limit=1000' for y in (year,year+1)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(_json,urls))
    for response in responses:
        values=response.get('events')
        if not isinstance(values,list) or len(values)>=1000:
            raise ProviderError('El calendario italiano está incompleto o alcanzó su límite de respuesta.')
        for event in values:
            game=parse_espn_event(event,season)
            if game is not None:games[game['id']]=game
    return sorted(games.values(),key=lambda g:(g['date'],g['time'] or '99:99',g['id']))


def parse_round_tables(tables,season):
    """Read only explicit matchday headings from season-verified archive tables.

    ``tables`` contains ``(html, source_url)`` pairs. Results and fixtures have
    different column layouts, so team anchors are located independently of TDs.
    """
    year=season_year(season);mapping={}
    plain=lambda value:' '.join(unescape(re.sub(r'<[^>]*>',' ',value)).split())
    for html,source_url in tables:
        if isinstance(html,bytes):html=html.decode('utf-8-sig')
        season_meta=re.search(r'<meta\b(?=[^>]*name=["\']keywords["\'])[^>]*content=["\']([^"\']*)["\']',html,re.I)
        if season_meta is None or f'Serie A {year}/{year+1}' not in unescape(season_meta[1]):
            raise ProviderError('La fuente de jornadas italianas no corresponde a esta temporada.')
        table=re.search(r'<table\b[^>]*class=["\'][^"\']*\btable-main\b[^"\']*["\'][^>]*>(.*?)</table>',html,re.S|re.I)
        if table is None:raise ProviderError('La fuente italiana no publicó su tabla de jornadas.')
        rnd=None
        for raw in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table[1],re.S|re.I):
            heading=re.search(r'(\d+)\.\s*Round',plain(raw))
            if heading:
                rnd=int(heading[1])
                if not 1<=rnd<=38:raise ProviderError('La fuente italiana informó una jornada no válida.')
                continue
            anchor=re.search(r'<a\b(?=[^>]*class=["\'][^"\']*\bin-match\b)[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',raw,re.S|re.I)
            if not anchor:continue
            names=[plain(x) for x in re.findall(r'<span\b[^>]*>(.*?)</span>',anchor[2],re.S|re.I)]
            if len(names)!=2 or rnd is None:raise ProviderError('La fuente italiana no identifica ambos equipos y su jornada.')
            home,away=map(normalize_team,names);key=home,away
            if key in mapping and mapping[key]['round']!=rnd:
                raise ProviderError('La fuente italiana asignó dos jornadas al mismo partido.')
            info={'round':rnd,'round_source':'BetExplorer · tabla de jornadas Serie A','round_source_url':source_url,'match_source_url':urljoin(source_url,unescape(anchor[1]))}
            if re.search(r'title=["\']Awarded["\']|\bAWA\.|\bW\.O\.',raw,re.I):
                info.update(administrative=True,administrative_reason='Resultado administrativo publicado en la tabla de Serie A de BetExplorer.')
                awarded=re.search(r'>\s*(\d{1,2})\s*:\s*(\d{1,2})\s*<',raw)
                if awarded:info.update(awarded_ft_home=int(awarded[1]),awarded_ft_away=int(awarded[2]))
            mapping[key]=info
    if len(mapping)!=380 or any(sum(v['round']==rnd for v in mapping.values())!=10 for rnd in range(1,39)):
        raise ProviderError('Las jornadas italianas no contienen los 380 partidos de la temporada.')
    for rnd in range(1,39):
        if len({team for pair,value in mapping.items() if value['round']==rnd for team in pair})!=20:
            raise ProviderError('Las jornadas italianas repiten equipos en una misma jornada.')
    return mapping


def round_calendar(season):
    year=season_year(season);today=datetime.now(BOGOTA)
    current=today.year if today.month>=7 else today.year-1
    slug='serie-a' if year==current else f'serie-a-{year}-{year+1}'
    urls=[f'https://www.betexplorer.com/football/italy/{slug}/{page}/' for page in ('results','fixtures')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pages=list(pool.map(_fetch,urls))
    return parse_round_tables(list(zip(pages,urls)),season)


def enrich_match(game):
    """Add observed summary values only when absent and consistent with FT."""
    if game['status']!='finished' or game.get('administrative') or not game.get('espn_id'):
        return game
    summary=_json(f"{ESPN_BASE}/summary?event={game['espn_id']}")
    comps=summary.get('header',{}).get('competitions') or []
    sides={str(c.get('homeAway')):c for c in comps[0].get('competitors',[])} if comps else {}
    if set(sides)!={'home','away'}:return game
    for side in ('home','away'):
        published=_integer(sides[side].get('score'),30)
        if published is not None and published!=game.get('ft_'+side):return game
    boxes={str(x.get('team',{}).get('id')):x for x in summary.get('boxscore',{}).get('teams',[])}
    observed={}
    for side in ('home','away'):
        lines=sides[side].get('linescores') or []
        first=next((x for x in lines if x.get('period')==1),None) or (lines[0] if lines else {})
        ht=_integer(first.get('displayValue',first.get('value')),30)
        if ht is not None and game.get('ft_'+side) is not None and ht<=game['ft_'+side]:observed['ht_'+side]=ht
        team_id=str(sides[side].get('id',sides[side].get('team',{}).get('id','')))
        box=boxes.get(team_id,{})
        corner=_stats(box)['corners']
        if corner is None:continue
        observed['corners_'+side]=corner
        values={x.get('name'):x.get('displayValue') for x in box.get('statistics',[])}
        observed['yellow_'+side]=_integer(values.get('yellowCards'),100)
        observed['red_'+side]=_integer(values.get('redCards'),100)
    for key,value in observed.items():
        if game.get(key) is None and value is not None:game[key]=value
    for key in ('corners','yellow','red'):
        if game.get(key) is None:game[key]=_total(game.get(key+'_home'),game.get(key+'_away'))
    return game


def get_coverage():
    return dict(_COVERAGE)


def sync_games(season):
    year=season_year(season);warnings=[]
    _COVERAGE.clear()
    try:rows=football_data(season)
    except ProviderError as error:
        rows=[];warnings.append('Football-Data no está disponible; se conservan los datos anteriores y se consulta el calendario de ESPN.')
        LOGGER.warning('CSV italiano no disponible: %s',error)
    try:fixtures=calendar(season)
    except ProviderError as error:
        _COVERAGE.update(fixtures_available=False,warnings=['No se pudo consultar el calendario italiano completo.'])
        raise ProviderError('No se pudo consultar el calendario italiano; se conservan los datos guardados.') from error
    regular=[g for g in fixtures if g['phase']=='Serie A']
    pairs={(g['home'],g['away']) for g in regular}
    teams={g[k] for g in regular for k in ('home','away')}
    if len(regular)!=380 or len(pairs)!=380 or len(teams)!=20:
        _COVERAGE.update(fixtures_available=False,warnings=['La fuente no publicó los 380 encuentros de la Serie A.'])
        raise ProviderError('El calendario italiano no está completo; se conservan los datos guardados.')
    try:rounds=round_calendar(season)
    except ProviderError as error:
        _COVERAGE.update(fixtures_available=False,warnings=['No se pudo verificar el calendario italiano por jornadas.'])
        raise ProviderError('No se pudieron verificar las jornadas italianas; se conservan los datos guardados.') from error
    if set(rounds)!=pairs:
        raise ProviderError('Las fuentes italianas discrepan en el calendario por jornadas; se conservan los datos anteriores.')
    for event in regular:
        info=rounds[(event['home'],event['away'])]
        if event.get('round') is not None and event['round']!=info['round']:
            raise ProviderError('Las fuentes italianas discrepan en la jornada de un partido.')
        event.update(info)
        event['scope']='first' if event['round']<=19 else 'second'
        if event.get('administrative'):
            event['ht_home']=event['ht_away']=None
            if event.get('awarded_ft_home') is not None and event.get('awarded_ft_away') is not None:
                event.update(status='finished',ft_home=event['awarded_ft_home'],ft_away=event['awarded_ft_away'])
    index={(g['home'],g['away'],g['phase']):g for g in rows}
    for event in fixtures:
        key=(event['home'],event['away'],event['phase']);target=index.get(key)
        if target is None:
            rows.append(event);index[key]=event
            continue
        if event.get('administrative'):
            if not target.get('administrative'):
                for name in ('ht_home','ht_away','ft_home','ft_away'):target['played_'+name]=target.get(name)
            target.update(administrative=True,administrative_reason=event.get('administrative_reason'),ht_home=None,ht_away=None)
            if event.get('awarded_ft_home') is not None and event.get('awarded_ft_away') is not None:
                target.update(status='finished',ft_home=event['awarded_ft_home'],ft_away=event['awarded_ft_away'])
        for name in ('espn_id','round','scope'):
            if event.get(name) is not None and (name!='scope' or event[name]!='all'):target[name]=event[name]
        for name in ('round_source','round_source_url','match_source_url'):
            if event.get(name):target[name]=event[name]
        if event.get('time_confirmed'):
            target.update({name:event[name] for name in ('date','time','kickoff','time_confirmed','date_confirmed')})
        if target['status']!='finished' and event['status'] in ('finished','live','postponed','cancelled'):
            target['status']=event['status']
            for name in ('ht_home','ht_away','ft_home','ft_away','corners','corners_home','corners_away'):
                if event.get(name) is not None:target[name]=event[name]
    if any((g['home'],g['away']) not in pairs for g in rows if g['phase']=='Serie A'):
        raise ProviderError('Las fuentes italianas discrepan en los equipos; se conservan los datos anteriores.')
    recent=sorted((g for g in rows if g['status']=='finished' and not g.get('administrative') and g.get('espn_id') and any(g.get(k) is None for k in ('ht_home','ht_away','corners','yellow','red'))),key=lambda g:(g['date'],g['time'] or ''),reverse=True)[:MAX_SUMMARIES]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(enrich_match,g):g for g in recent}
        for future in as_completed(jobs):
            try:future.result()
            except ProviderError as error:
                warnings.append(f"Datos pendientes del partido {jobs[future].get('espn_id')}: {error}")
    _COVERAGE.update(fixtures_available=True,source='Football-Data I1, ESPN y BetExplorer (jornadas)',matches=len(rows),scheduled=sum(g['status']=='scheduled' for g in rows),confirmed_upcoming=sum(g['status']=='scheduled' and g.get('time_confirmed',False) and datetime.fromisoformat(g['kickoff'])>datetime.now(timezone.utc) for g in rows),missing_ht=sum(g['status']=='finished' and not g.get('administrative') and (g.get('ht_home') is None or g.get('ht_away') is None) for g in rows),warnings=warnings)
    return rows
