"""Paraguayan Primera División observations, with no card data collected."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from .providers import ProviderError, _fetch, _json, _integer, _total, _espn_clock, _espn_status, _stats
from .paraguay_names import normalize_team

BASE='https://site.api.espn.com/apis/site/v2/sports/soccer/par.1'
BOGOTA=ZoneInfo('America/Bogota')
MAX_SUMMARIES=40
_COVERAGE={}
EXPECTED_COUNTS={2018:264,2019:264,2020:205,2021:180,2022:264,2023:264,2024:264,2025:264,2026:264}
KNOCKOUT_PHASES={'Cuartos de final','Semifinal','Final'}
# These are source-verified exceptions, not a rule inferred from a 3–0 score.
VERIFIED_RESULTS={
    '501220':(2018,'Independiente FBC','Nacional',0,3,True,'https://www.rsssf.org/tablesp/para2018.html'),
    '530372':(2019,'Sportivo San Lorenzo','Libertad',3,0,True,'https://www.rsssf.org/tablesp/para2019.html'),
    '561133':(2020,'Guaraní','Olimpia',0,3,True,'https://www.rsssf.org/tablesp/para2020.html'),
    '695676':(2024,'Cerro Porteño','2 de Mayo',0,3,True,'https://www.rsssf.org/tablesp/para2024.html'),
    '728904':(2025,'2 de Mayo','General Caballero JLM',0,1,False,'https://www.rsssf.org/tablesp/para2025.html'),
    '401848258':(2026,'Olimpia','Cerro Porteño',3,0,True,'https://www.betexplorer.com/football/paraguay/copa-de-primera/olimpia-asuncion-cerro-porteno/reIT83X7/'),
}
VERIFIED_PLAYED_SCORES={'501220':(4,0),'530372':(3,4),'561133':(4,2),'695676':(0,0)}


def year_number(season):
    if not re.fullmatch(r'20\d\d',str(season)):
        raise ProviderError('La temporada paraguaya debe ser un año, por ejemplo 2026.')
    return int(season)


def corner_pair(sides):
    values=[_stats(sides.get(side,{}))['corners'] for side in ('home','away')]
    if values==[0,0]:
        stats=[{item.get('name'):item.get('displayValue') for item in sides.get(side,{}).get('statistics',[])} for side in ('home','away')]
        # Old scoreboards generate shots from goals, leaving all other counters
        # at zero. Those blocks do not establish that zero corners were taken.
        def amount(stats,name):return _integer(stats.get(name),1000) or 0
        tracking=any(amount(s,'foulsCommitted')>0 or amount(s,'totalPasses')>0 or amount(s,'saves')>0 for s in stats)
        if not tracking:return [None,None]
    return values


def half_time_from_complete_events(summary, game):
    """Observe HT only from a populated goal log agreeing with the final score.

    Historic ESPN line scores can contain synthetic first-half zeroes. Their
    presence or period labels alone therefore do not establish an observed HT.
    """
    if game.get('status')!='finished' or game.get('administrative'):
        return None
    header=summary.get('header',{})
    if str(header.get('id',''))!=str(game.get('espn_id')):
        return None
    events=summary.get('keyEvents')
    if not isinstance(events,list) or not events:
        return None
    comps=header.get('competitions') or []
    sides={str(c.get('homeAway')):c for c in comps[0].get('competitors',[])} if comps else {}
    if set(sides)!={'home','away'}:
        return None
    teams={str(sides[side].get('id',sides[side].get('team',{}).get('id',''))):index for index,side in enumerate(('home','away'))}
    if len(teams)!=2 or '' in teams:
        return None
    target=tuple(game.get('ft_'+side) for side in ('home','away'))
    if any(type(value) is not int or value<0 for value in target):
        return None
    goals={}
    for event in events:
        if not isinstance(event,dict):return None
        if event.get('scoringPlay') is not True or event.get('shootout') is True:
            continue
        kind=(event.get('type') or {}).get('type')
        period=(event.get('period') or {}).get('number')
        team=str((event.get('team') or {}).get('id',''))
        ident=str(event.get('id',''))
        if kind not in ('goal','penalty---scored','own-goal') or type(period) is not int or period not in (1,2,3,4) or team not in teams or not ident:
            return None
        observation=(period,teams[team],kind=='own-goal')
        if ident in goals and goals[ident]!=observation:
            return None
        goals[ident]=observation
    candidates=set()
    for invert_own_goal in (False,True):
        final=[0,0];half=[0,0]
        for period,side,own in goals.values():
            if invert_own_goal and own:side=1-side
            final[side]+=1
            if period==1:half[side]+=1
        if tuple(final)==target:candidates.add(tuple(half))
    return next(iter(candidates)) if len(candidates)==1 else None


def _verified_result(game):
    result=VERIFIED_RESULTS.get(str(game.get('espn_id')))
    if result is None or game.get('status')!='finished':return game
    year,home,away,fh,fa,administrative,url=result
    if (game.get('season'),game.get('home'),game.get('away'))!=(str(year),home,away):return game
    if (game.get('ft_home'),game.get('ft_away'))!=(fh,fa):
        game['original_source_ft_home'],game['original_source_ft_away']=game.get('ft_home'),game.get('ft_away')
    if str(game.get('espn_id')) in VERIFIED_PLAYED_SCORES:
        game['played_ft_home'],game['played_ft_away']=VERIFIED_PLAYED_SCORES[str(game['espn_id'])]
    game.update(ft_home=fh,ft_away=fa,result_source='Resultado contrastado con archivo independiente',result_source_url=url)
    if administrative:
        game.update(administrative=True,administrative_reason='Resultado administrativo contrastado con el archivo publicado; excluido del análisis.',ht_home=None,ht_away=None,corners=None,corners_home=None,corners_away=None)
    return game


def parse_espn_event(event, season):
    year=year_number(season)
    comps=event.get('competitions') or []
    if not comps or not event.get('id'):
        return None
    comp=comps[0]
    sides={str(c.get('homeAway')):c for c in comp.get('competitors',[])}
    if set(sides)!={'home','away'}:
        return None
    home=normalize_team(sides['home'].get('team',{}).get('displayName'))
    away=normalize_team(sides['away'].get('team',{}).get('displayName'))
    if not home or not away:
        return None
    slug=str(event.get('season',{}).get('slug','')).lower()
    if 'apertura' in slug:
        scope='Apertura'
    elif 'clausura' in slug:
        scope='Clausura'
    else:
        return None
    published_year=event.get('season',{}).get('year')
    if published_year is not None and str(published_year)!=str(year):
        return None
    state=event.get('status',comp.get('status',{})).get('type',{})
    if state.get('state') not in ('pre','in','post'):
        raise ProviderError('El calendario paraguayo publicó un estado desconocido.')
    status=_espn_status(state)
    clock=_espn_clock(event,comp,status)
    if clock is None or not clock['date'].startswith(str(year)+'-'):
        return None
    if clock.get('kickoff') and status=='finished' and datetime.fromisoformat(clock['kickoff'])>datetime.now(timezone.utc):
        status='scheduled'
    name=str(state.get('name','')).upper()
    administrative='AWARD' in name and status=='finished'
    notes=' '.join(str(n.get('text','')) for n in comp.get('notes',[]) if isinstance(n,dict)).lower()
    detail=slug+' '+notes
    if 'quarterfinal' in detail or 'quarter-final' in detail or 'cuartos' in detail:
        phase='Cuartos de final'
    elif 'semifinal' in detail or 'semi-final' in detail:
        phase='Semifinal'
    elif 'finals' in slug or re.search(r'\bfinal\b',notes):
        phase='Final'
    else:
        phase='Fase regular'
    rnd=_integer((event.get('week') or comp.get('week') or {}).get('number'),100)
    if rnd==0:rnd=None
    ft=[_integer(sides[side].get('score'),30) if status=='finished' else None for side in ('home','away')]
    if status=='finished' and None in ft:
        raise ProviderError('Un partido paraguayo terminado no publica el marcador final.')
    # The scoreboard's first line can be a placeholder, even for regular games.
    # A populated and consistent event log or another verified source fills HT.
    ht=[None,None]
    corners=corner_pair(sides) if status=='finished' and not administrative else [None,None]
    result={'id':'paraguay:espn:'+str(event['id']),'provider_id':'espn:'+str(event['id']),'espn_id':str(event['id']),
            'league':'paraguay','season':str(year),'season_label':str(year),**clock,
            'home':home,'away':away,'round':rnd,'scope':scope,'phase':phase,'status':status,
            'ht_home':ht[0],'ht_away':ht[1],'ft_home':ft[0],'ft_away':ft[1],
            'corners':_total(*corners),'corners_home':corners[0],'corners_away':corners[1],
            'yellow':None,'red':None,'yellow_home':None,'yellow_away':None,'red_home':None,'red_away':None,
            'administrative':administrative,'administrative_reason':'Resultado administrativo o abandono según la fuente.' if administrative else None,
            'source':'ESPN · Primera División de Paraguay','source_url':'https://www.espn.com/soccer/match/_/gameId/'+str(event['id'])}
    if result['corners'] is not None:
        result['corners_source']='ESPN · estadísticas del partido'
        result['corners_source_url']=f'{BASE}/scoreboard?dates={year}&limit=1000'
    return _verified_result(result)


def enrich_match(game, summary=None):
    """Return a copy with verified half-time and corner observations only."""
    result=dict(game)
    result['ht_home']=result['ht_away']=None
    if game['status']!='finished' or game.get('administrative'):
        return result
    if summary is None:
        summary=_json(f"{BASE}/summary?event={game['espn_id']}")
    header=summary.get('header',{})
    if str(header.get('id',''))!=str(game['espn_id']):
        return result
    comps=header.get('competitions') or []
    sides={str(c.get('homeAway')):c for c in comps[0].get('competitors',[])} if comps else {}
    if set(sides)!={'home','away'}:
        return result
    half=half_time_from_complete_events(summary,game)
    if half is not None:
        result['ht_home'],result['ht_away']=half
        result['ht_source']='ESPN · registro poblado y completo de goles por periodo, contrastado con FT'
        result['ht_source_url']=f"{BASE}/summary?event={game['espn_id']}"
    for side in ('home','away'):
        ft=_integer(sides[side].get('score'),30)
        if ft is not None and ft!=game.get('ft_'+side):
            return result
    boxes={str(t.get('team',{}).get('id')):t for t in summary.get('boxscore',{}).get('teams',[])}
    side_boxes={side:boxes.get(str(sides[side].get('id',sides[side].get('team',{}).get('id',''))),{}) for side in ('home','away')}
    summary_corners=dict(zip(('home','away'),corner_pair(side_boxes)))
    observed_corners=False
    for side in ('home','away'):
        corner=summary_corners[side]
        if corner is not None:
            result['corners_'+side]=corner
            observed_corners=True
    result['corners']=_total(result.get('corners_home'),result.get('corners_away'))
    if observed_corners and result['corners'] is not None:
        result['corners_source']='ESPN · estadísticas del partido'
        result['corners_source_url']=f"{BASE}/summary?event={game['espn_id']}"
    return result


def get_coverage():
    return dict(_COVERAGE)


def parse_round_table(html, season, scope, source_url):
    """Parse the explicitly published current tournament's matchday headings."""
    year=year_number(season)
    if scope not in ('Apertura','Clausura'):raise ProviderError('Torneo paraguayo no válido.')
    if isinstance(html,bytes):html=html.decode('utf-8-sig')
    meta=re.search(r'<meta\b(?=[^>]*name=["\']keywords["\'])[^>]*content=["\']([^"\']+)["\']',html,re.I)
    if meta is None or not re.search(r'\b'+str(year)+r'\b',unescape(meta[1])):
        raise ProviderError('La tabla de jornadas paraguayas no corresponde al año consultado.')
    table=re.search(r'<table\b[^>]*class=["\'][^"\']*\btable-main\b[^"\']*["\'][^>]*>(.*?)</table>',html,re.S|re.I)
    if table is None:raise ProviderError('La fuente no publica su tabla de jornadas paraguayas.')
    plain=lambda value:' '.join(unescape(re.sub(r'<[^>]*>',' ',value)).split())
    mapping={};rnd=None
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table[1],re.S|re.I):
        heading=re.search(r'(\d+)\.\s*Round',plain(row),re.I)
        if heading:
            rnd=int(heading[1])
            if not 1<=rnd<=22:raise ProviderError('La fuente paraguaya publicó una jornada desconocida.')
            continue
        anchor=re.search(r'<a\b(?=[^>]*class=["\'][^"\']*\bin-match\b)[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',row,re.S|re.I)
        if anchor is None:continue
        names=[plain(value) for value in re.findall(r'<span\b[^>]*>(.*?)</span>',anchor[2],re.S|re.I)]
        if len(names)!=2 or rnd is None:raise ProviderError('Un encuentro no tiene equipos y jornada identificados.')
        home,away=map(normalize_team,names);url=urljoin(source_url,unescape(anchor[1]));key=scope,home,away
        if key in mapping and mapping[key]['round']!=rnd:raise ProviderError('La tabla asigna dos jornadas a un partido paraguayo.')
        mapping[key]={'round':rnd,'round_source':'BetExplorer · jornadas explícitas','round_source_url':source_url,'archive_url':url,'archive_id':url.rstrip('/').rsplit('/',1)[-1]}
    return mapping


def _saved_metadata(game, old):
    if not isinstance(old,dict) or old.get('league')!='paraguay' or old.get('season')!=game['season']:
        return None
    if any(old.get(key)!=game.get(key) for key in ('id','home','away','scope')):
        return None
    phase=old.get('phase')
    if phase not in KNOCKOUT_PHASES|{'Fase regular'}:return None
    if game.get('phase')!='Fase regular' and phase!=game.get('phase'):return None
    number=old.get('round')
    if phase in KNOCKOUT_PHASES and old.get('round_not_applicable') is True:
        if number is not None:return None
    elif type(number) is not int or not 1<=number<=22:
        return None
    url=old.get('round_source_url')
    if not isinstance(url,str) or not old.get('round_source'):return None
    try:
        parsed=urlsplit(url);host=(parsed.hostname or '').lower()
        if parsed.scheme not in ('http','https') or parsed.username or parsed.password or not any(host==name or host.endswith('.'+name) for name in ('betexplorer.com','apf.org.py','espn.com')):
            return None
    except ValueError:return None
    return {key:old[key] for key in ('round','phase','round_source','round_source_url','round_not_applicable','archive_url','archive_id') if key in old}


def _retain_verified_half(game,old):
    """A raw provider refresh must not erase a separately audited half-time."""
    if not old or old.get('ht_verified') is not True or game.get('administrative'):
        return
    fields=('id','league','season','home','away','scope','status','ft_home','ft_away')
    if any(game.get(key)!=old.get(key) for key in fields) or game['status']!='finished':
        return
    half=[old.get('ht_home'),old.get('ht_away')]
    if any(type(value) is not int or not 0<=value<=game['ft_'+side] for side,value in zip(('home','away'),half)):
        return
    source=old.get('ht_source_url') or ''
    try:parsed=urlsplit(source)
    except ValueError:return
    if parsed.scheme!='https' or parsed.hostname not in ('www.betexplorer.com','betexplorer.com') or parsed.username or parsed.password:
        return
    for key in ('ht_home','ht_away','ht_source','ht_source_url','ht_verified'):
        if key in old:game[key]=old[key]


def sync_games(season, *, saved_rounds=None, enrich_limit=MAX_SUMMARIES):
    """Fetch an annual calendar without conflating the two tournaments."""
    year=year_number(season);_COVERAGE.clear()
    data=_json(f'{BASE}/scoreboard?dates={year}&limit=1000')
    events=data.get('events')
    if not isinstance(events,list) or len(events)>=1000:
        raise ProviderError('El calendario paraguayo está incompleto o alcanzó el límite de respuesta.')
    games=[g for event in events if (g:=parse_espn_event(event,str(year))) is not None]
    if not games or len({g['id'] for g in games})!=len(games):
        raise ProviderError('El calendario paraguayo no publicó una lista válida de partidos.')
    if year in EXPECTED_COUNTS and {g['scope'] for g in games}!={'Apertura','Clausura'}:
        raise ProviderError('El calendario paraguayo no contiene ambos torneos del año.')
    if year in EXPECTED_COUNTS and len(games)!=EXPECTED_COUNTS[year]:
        raise ProviderError('El calendario paraguayo no contiene todos los partidos del año; se conservan los datos guardados.')
    if year==2026 and (len({g[k] for g in games for k in ('home','away')})!=12 or any(sum(g['scope']==scope for g in games)!=132 for scope in ('Apertura','Clausura'))):
        raise ProviderError('El calendario paraguayo no publica los dos torneos completos de doce equipos.')
    saved={g['id']:g for g in (saved_rounds or []) if g.get('league')=='paraguay' and g.get('season')==str(year)}
    for game in games:
        old=_saved_metadata(game,saved.get(game['id']))
        if old:
            if game.get('round') is None:
                game.update(old)
            elif game.get('round')==old.get('round'):
                game.update({key:value for key,value in old.items() if key not in ('round',)})
        if game['phase'] in KNOCKOUT_PHASES:game['ht_home']=game['ht_away']=None
    regular=[g for g in games if g['phase']=='Fase regular']
    if len({(g['scope'],g['home'],g['away']) for g in regular})!=len(regular):
        raise ProviderError('El calendario paraguayo repite un partido regular del mismo torneo.')
    recent=sorted((g for g in games if g['status']=='finished' and not g.get('administrative')),key=lambda g:(g['date'],g.get('time')or''),reverse=True)
    recent=recent if enrich_limit is None else recent[:max(0,enrich_limit)]
    warnings=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(enrich_match,g):g for g in recent}
        enriched={}
        for future in as_completed(jobs):
            g=jobs[future]
            try:enriched[g['id']]=future.result()
            except ProviderError as error:warnings.append(f"Datos pendientes de {g['espn_id']}: {error}")
    games=[enriched.get(g['id'],g) for g in games]
    for game in games:_retain_verified_half(game,saved.get(game['id']))
    now=datetime.now(timezone.utc)
    _COVERAGE.update(fixtures_available=True,source='ESPN · Primera División de Paraguay',matches=len(games),rounds_verified=sum(g.get('round') is not None or g.get('round_not_applicable') is True for g in games),scheduled=sum(g['status']=='scheduled' for g in games),confirmed_upcoming=sum(bool(g['status']=='scheduled' and g.get('kickoff') and datetime.fromisoformat(g['kickoff'])>now) for g in games),missing_ht=sum(g['status']=='finished' and not g.get('administrative') and (g.get('ht_home') is None or g.get('ht_away') is None) for g in games),corners_missing=sum(g['status']=='finished' and not g.get('administrative') and g.get('corners') is None for g in games),warnings=warnings)
    return games
