"""Polish Ekstraklasa results and calendar from the league's public match centre.

The calendar and reports expose server-rendered React Flight JSON. Only scores,
calendar data and the complete, source-published goal timeline are retained.
Unknown observations stay None; corner/card statistics are outside this league.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import logging
import re
from zoneinfo import ZoneInfo

from .providers import ProviderError, _fetch
from .poland_names import normalize_team

BASE = 'https://ekstraklasa.org'
WARSAW = ZoneInfo('Europe/Warsaw')
BOGOTA = ZoneInfo('America/Bogota')
MAX_REPORTS = 40
LOGGER = logging.getLogger(__name__)
_COVERAGE = {}
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)


def _html(url):
    try:
        return _fetch(url).decode('utf-8-sig')
    except UnicodeError as error:
        raise ProviderError('La fuente polaca devolvió una página ilegible.') from error


def _walk(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def flight_records(html):
    """Decode public JSON payloads; never execute the provider's JavaScript."""
    decoder = json.JSONDecoder()
    chunks = []
    for match in re.finditer(r'self\.__next_f\.push\(', html):
        try:
            array, _ = decoder.raw_decode(html, match.end())
        except ValueError:
            continue
        if isinstance(array, list) and len(array) > 1 and array[0] == 1 and isinstance(array[1], str):
            chunks.append(array[1])
    records = []
    for line in ''.join(chunks).splitlines():
        if ':' not in line:
            continue
        body = line.split(':', 1)[1]
        try:
            records.append(json.loads(body))
        except ValueError:
            # Module references and non-JSON Flight records are not sports data.
            continue
    return records


def calendar_props(html):
    for value in _walk(flight_records(html)):
        if isinstance(value, dict) and isinstance(value.get('enrichedMatches'), list):
            return value
    raise ProviderError('No se encontró el calendario oficial de la Ekstraklasa.')


def _year(season):
    match = re.fullmatch(r'(20\d\d)-(\d\d)', str(season))
    if not match or int(match[2]) != (int(match[1]) + 1) % 100:
        raise ProviderError('La temporada polaca debe tener el formato 2026-27.')
    return int(match[1])


def _integer(value):
    if type(value) is int and 0 <= value <= 100:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,2}', value.strip()):
        return int(value)
    return None


def _stamp(value):
    if not isinstance(value, str) or 'T' not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _clock(raw, finished):
    # The official JSON publishes the replacement kickoff separately.
    day = raw.get('postponedDate') or raw.get('localDate') or raw.get('date')
    published_time = raw.get('postponedLocalTime') or raw.get('localTime') or raw.get('time')
    visible_time = isinstance(published_time, str) and re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', published_time)
    # A date-only fixture may carry an ISO midnight placeholder. Honour the
    # source's visible TBD/localTime rather than turning that into a kickoff.
    visible_unknown = any(key in raw for key in ('postponedLocalTime','localTime','time')) and not visible_time
    unconfirmed = raw.get('timeConfirmed') is False or raw.get('dateConfirmed') is False or visible_unknown
    stamp = None if unconfirmed else _stamp(raw.get('postponedDatetime')) or _stamp(raw.get('matchDatetime'))
    if not unconfirmed and stamp is None and isinstance(day, str) and visible_time:
        try:
            stamp = datetime.fromisoformat(day[:10] + 'T' + published_time).replace(tzinfo=WARSAW)
        except ValueError:
            pass
    if stamp is not None:
        local = stamp.astimezone(BOGOTA)
        return {'date':local.date().isoformat(), 'time':local.strftime('%H:%M'), 'kickoff':stamp.astimezone(timezone.utc).isoformat(), 'date_confirmed':True, 'time_confirmed':True}
    try:
        day = datetime.strptime(str(day)[:10], '%Y-%m-%d').date().isoformat()
    except (ValueError, TypeError):
        raise ProviderError('Un partido polaco no tiene una fecha válida.')
    return {'date':day, 'time':None, 'kickoff':None, 'date_confirmed':raw.get('dateConfirmed') is True or bool(finished) and raw.get('dateConfirmed') is not False, 'time_confirmed':False}


def parse_match(raw, season, round_number=None, now=None):
    year = _year(season)
    ident = str(raw.get('matchId', ''))
    if not _UUID.fullmatch(ident):
        raise ProviderError('La fuente polaca no ofrece un identificador válido.')
    home, away = raw.get('homeTeam') or {}, raw.get('awayTeam') or {}
    if not home.get('name') or not away.get('name'):
        raise ProviderError('Un partido polaco no tiene ambos equipos identificados.')
    state = str(raw.get('status', '')).lower()
    administrative = state in {'awarded', 'walkover', 'abandoned'}
    if state in {'played', 'finished', 'awarded', 'walkover'}:
        status = 'finished'
    elif state in {'live', 'in_progress', 'inprogress', 'playing', 'halftime'}:
        status = 'live'
    elif state in {'cancelled', 'canceled', 'abandoned'}:
        status = 'cancelled'
    elif state in {'postponed', 'suspended'}:
        status = 'postponed'
    elif state in {'fixture', 'scheduled', 'not_started', 'notstarted'}:
        status = 'scheduled'
    else:
        raise ProviderError('La fuente polaca publicó un estado de partido desconocido.')
    clock = _clock(raw, status == 'finished')
    if not f'{year}-07-01' <= clock['date'] < f'{year+1}-07-01':
        raise ProviderError('El calendario polaco mezcla temporadas distintas.')
    clock_stamp=_stamp(clock.get('kickoff'))
    now=now or datetime.now(timezone.utc)
    if clock_stamp is not None and clock_stamp>now and status=='finished' and not administrative:
        status='scheduled'
    rnd = _integer(raw.get('week')) or _integer(round_number)
    if rnd is None or not 1 <= rnd <= 34:
        raise ProviderError('El calendario polaco no informa una jornada válida.')
    slug = home.get('clubSlug', '') + '-' + away.get('clubSlug', '')
    source_url = f'{BASE}/mecz/{ident}/{slug}/przeglad/' if slug != '-' else f'{BASE}/mecz/{ident}/przeglad/'
    ft_home = _integer(raw.get('homeScore')) if status == 'finished' else None
    ft_away = _integer(raw.get('awayScore')) if status == 'finished' else None
    if status == 'finished' and (ft_home is None or ft_away is None):
        raise ProviderError('La fuente polaca no ofrece el resultado final de un partido terminado.')
    return {
        'id':'poland:ek:' + ident, 'provider_id':'ek:' + ident, 'official_id':ident,
        'league':'poland', 'season':str(season), 'season_label':f'{year}/{(year+1)%100:02d}',
        **clock, 'home':normalize_team(home['name']), 'away':normalize_team(away['name']),
        'round':rnd, 'scope':'all', 'phase':'Ekstraklasa', 'status':status,
        'ht_home':None, 'ht_away':None, 'ft_home':ft_home, 'ft_away':ft_away,
        'corners':None, 'yellow':None, 'red':None, 'yellow_home':None, 'yellow_away':None,
        'red_home':None, 'red_away':None, 'administrative':administrative,
        'administrative_reason':'Resultado administrativo o partido abandonado según la fuente oficial.' if administrative else None,
        'source':'Ekstraklasa · sitio oficial', 'source_url':source_url,
    }


def parse_calendar(html, season):
    props = calendar_props(html)
    year = _year(season)
    if props.get('seasonSlug') != f'{year}-{year+1}' or props.get('totalRounds') != 34:
        raise ProviderError('El calendario oficial no corresponde a esta temporada de la Ekstraklasa.')
    main = [parse_match(raw, season, props.get('weekNumber')) for raw in props['enrichedMatches']]
    if len(main) != 9:
        raise ProviderError('La fuente no devolvió los nueve encuentros de la jornada polaca.')
    postponed = [parse_match(raw, season) for raw in props.get('enrichedPostponedMatches', [])]
    return main, postponed


def _direct_half_time(raw):
    for home, away in [('homeHalfTimeScore','awayHalfTimeScore'), ('halfTimeHomeScore','halfTimeAwayScore'), ('homeScoreHalfTime','awayScoreHalfTime')]:
        pair = _integer(raw.get(home)), _integer(raw.get(away))
        if None not in pair:
            return pair
    half = raw.get('halfTimeScore')
    if isinstance(half, dict):
        pair = _integer(half.get('home')), _integer(half.get('away'))
        if None not in pair:
            return pair
    return None


def half_time_from_events(match, events):
    """Reconstruct an observed HT only from a complete official period log.

    Both half-end markers and the entire goal tally must agree with FT. Own-goal
    side conventions are accepted only when the resulting HT is unambiguous.
    """
    if not isinstance(events, list) or match.get('status') != 'played':
        return None
    ident = match.get('matchId')
    filtered = [e for e in events if isinstance(e, dict) and e.get('matchId') == ident]
    if not all(any(e.get('eventType')=='end' and e.get('period')==p for e in filtered) for p in (1,2)):
        return None
    target = _integer(match.get('homeScore')), _integer(match.get('awayScore'))
    if None in target:
        return None
    goals = {}; cancelled = set()
    for event in filtered:
        if event.get('eventType') != 'goal':
            continue
        if event.get('period') not in (1,2) or event.get('teamType') not in ('home','away') or not event.get('eventId'):
            return None
        decision = str(event.get('decision') or '').lower()
        event_id = str(event['eventId'])
        if decision in {'cancelled','canceled','disallowed','no_goal','no goal','overturned','false'}:
            if event_id in goals:
                return None
            cancelled.add(event_id)
            continue
        if decision not in ('','goal','confirmed','valid','true'):
            return None
        subtype = str(event.get('eventSubtype') or 'goal').lower()
        if subtype not in {'goal','own_goal','penalty','penalty_goal'}:
            return None
        if event_id in cancelled:
            return None
        previous = goals.get(event_id)
        if previous is not None and any(previous.get(key)!=event.get(key) for key in ('period','teamType','eventSubtype')):
            return None
        goals[event_id] = event
    candidates = set()
    for invert_own_goal in (False,True):
        final = [0,0]; half = [0,0]
        for event in goals.values():
            side = 0 if event['teamType']=='home' else 1
            if invert_own_goal and event.get('eventSubtype')=='own_goal':
                side = 1-side
            final[side] += 1
            if event['period']==1:
                half[side] += 1
        if tuple(final)==target:
            candidates.add(tuple(half))
    return next(iter(candidates)) if len(candidates)==1 else None


def enrich_match(game, html):
    result = dict(game)
    if result.get('status')!='finished' or result.get('administrative'):
        return result
    props = next((v for v in _walk(flight_records(html)) if isinstance(v,dict) and isinstance(v.get('initialMatch'),dict) and v['initialMatch'].get('matchId')==game.get('official_id')),None)
    if props is None:
        return result
    match = props['initialMatch']
    if match.get('status')!='played':
        return result
    final = _integer(match.get('homeScore')), _integer(match.get('awayScore'))
    if None in final:
        return result
    half = _direct_half_time(match) or half_time_from_events(match,props.get('initialEvents'))
    result['ft_home'],result['ft_away'] = final
    if half is not None and all(h<=f for h,f in zip(half,final)):
        result['ht_home'],result['ht_away'] = half
        result['ht_source'] = 'Marcador de descanso publicado o registro completo de goles por periodo del centro oficial.'
    return result


def get_coverage():
    return dict(_COVERAGE)


def sync_games(season, *, enrich_limit=MAX_REPORTS, pending_ids=()):
    """Fetch the calendar and recent reports, plus previously missing HTs.

    pending_ids contains application IDs (``poland:ek:<UUID>``) supplied by the
    caller. Only matching completed, non-administrative fixtures are retried.
    """
    year = _year(season)
    # Historical results use separately audited archive IDs. This adapter keeps
    # the active calendar current without duplicating those finished seasons.
    today = datetime.now(BOGOTA)
    current = today.year if today.month >= 7 else today.year-1
    if year != current:
        raise ProviderError('Este proveedor actualiza la temporada en curso; el histórico polaco se conserva de su archivo verificado.')
    _COVERAGE.clear()
    games = {}; errors = []
    def load_round(rnd):
        return parse_calendar(_html(f'{BASE}/terminarz/{year}-{year+1}/kolejka-{rnd}/'),season)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(load_round,r):r for r in range(1,35)}
        for future in as_completed(jobs):
            try:
                main, postponed = future.result()
                for g in postponed:
                    games.setdefault(g['id'],g)
                for g in main:
                    games[g['id']] = g
            except ProviderError as error:
                errors.append(f'Jornada {jobs[future]}: {error}')
    if errors:
        _COVERAGE.update(fixtures_available=False,warnings=errors)
        raise ProviderError('No se pudo consultar todo el calendario polaco; se conservan los datos guardados.')
    rows = sorted(games.values(),key=lambda g:(g['round'],g['date'],g.get('time') or '',g['id']))
    rounds = {r:sum(g['round']==r for g in rows) for r in range(1,35)}
    if len(rows)!=306 or any(n!=9 for n in rounds.values()) or len({g[k] for g in rows for k in ('home','away')})!=18 or len({(g['home'],g['away']) for g in rows})!=306:
        _COVERAGE.update(fixtures_available=False,warnings=['La fuente no publicó un calendario completo de 306 encuentros.'])
        raise ProviderError('El calendario polaco está incompleto; se conservan los datos guardados.')
    completed = sorted((g for g in rows if g['status']=='finished' and not g.get('administrative')),key=lambda g:(g['date'],g.get('time') or ''),reverse=True)
    recent = completed if enrich_limit is None else completed[:max(0,enrich_limit)]
    requested = {g['id'] for g in recent} | set(pending_ids)
    recent = [g for g in completed if g['id'] in requested]
    warnings = []
    def enrich(game):
        return enrich_match(game,_html(game['source_url']))
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(enrich,g):g for g in recent}
        for future in as_completed(jobs):
            game = jobs[future]
            try:
                games[game['id']] = future.result()
            except ProviderError as error:
                warnings.append(f"HT pendiente del partido {game['official_id']}: {error}")
    rows = [games[g['id']] for g in rows]
    now = datetime.now(timezone.utc)
    _COVERAGE.update(fixtures_available=True,source='Ekstraklasa oficial',matches=len(rows),scheduled=sum(g['status']=='scheduled' for g in rows),confirmed_upcoming=sum(g['status']=='scheduled' and g.get('time_confirmed') and datetime.fromisoformat(g['kickoff'])>now for g in rows),missing_ht=sum(g['status']=='finished' and not g.get('administrative') and g['ht_home'] is None for g in rows),warnings=warnings)
    return rows
