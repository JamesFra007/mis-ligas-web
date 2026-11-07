"""A transparent league baseline for the next both-score-in-HT event.

This is a reference model, not a validated prediction for individual teams. It
uses a Jeffreys Beta(1/2, 1/2) prior and the posterior predictive waiting time.
A run without an event does not increase the next match's probability.
All dates/times in stored games are local America/Bogota values.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo('America/Bogota')
MINIMUM_SAMPLE = 100
MAXIMUM_SAMPLE = 1000
SEASONS = 3
RANK_LIMIT = 500
HORIZONS = (1, 3, 5, 10, 20)
QUANTILES = (0.10, 0.50, 0.90)


def _as_local(value: datetime | None) -> datetime:
    value = value or datetime.now(BOGOTA)
    return value.replace(tzinfo=BOGOTA) if value.tzinfo is None else value.astimezone(BOGOTA)


def _kickoff(game: dict) -> datetime | None:
    """Only an explicitly published local date and valid time form a kickoff."""
    raw_date, raw_time = game.get('date'), game.get('time')
    if 'date_confirmed' in game and game['date_confirmed'] is not True:
        return None
    if 'time_confirmed' in game and game['time_confirmed'] is not True:
        return None
    if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
        return None
    if not isinstance(raw_time, str) or not re.fullmatch(r'\d{2}:\d{2}', raw_time.strip()):
        return None
    try:
        day = date.fromisoformat(raw_date)
        hour, minute = map(int, raw_time.strip().split(':'))
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BOGOTA)
    except (ValueError, TypeError):
        return None


def _scores(game: dict, period: str) -> tuple[int, int] | None:
    values = (game.get(period + '_home'), game.get(period + '_away'))
    return values if all(type(x) is int and x >= 0 for x in values) else None


def _season(game: dict) -> str | None:
    value = str(game.get('season', '')).replace('/', '-')
    if re.fullmatch(r'20\d{2}', value):
        return value
    match = re.fullmatch(r'(20\d{2})-(\d{2})', value)
    if match and (int(match[1]) + 1) % 100 == int(match[2]):
        return value
    return None


def _identity(game: dict):
    return ('id', str(game['id'])) if game.get('id') is not None else ('fixture', game.get('date'), game.get('time'), game.get('home'), game.get('away'))


def _wilson(hits: int, n: int) -> dict[str, float] | None:
    if not n:
        return None
    z = 1.959963984540054
    observed = hits / n
    denominator = 1 + z*z/n
    center = (observed + z*z/(2*n)) / denominator
    radius = z * math.sqrt(observed*(1-observed)/n + z*z/(4*n*n)) / denominator
    return {'low': max(0.0, center-radius), 'high': min(1.0, center+radius)}


def _survival(alpha: float, beta: float, k: int) -> float:
    """Posterior probability of zero successes in the next k matches."""
    probability = 1.0
    for i in range(max(0, k)):
        probability *= (beta+i)/(alpha+beta+i)
    return probability


def _waiting_time(alpha: float, beta: float):
    quantiles = {q: None for q in QUANTILES}
    survival = 1.0
    for k in range(1, RANK_LIMIT+1):
        survival *= (beta+k-1)/(alpha+beta+k-1)
        cdf = 1-survival
        for q in QUANTILES:
            if quantiles[q] is None and cdf >= q:
                quantiles[q] = k
        if all(quantiles[q] is not None for q in QUANTILES):
            break
    lower, median, upper = (quantiles[q] for q in QUANTILES)
    inside = _survival(alpha,beta,lower-1)-_survival(alpha,beta,upper) if lower is not None and upper is not None else None
    return {'q10':lower,'q50':median,'q90':upper,'target_central_probability':0.8,'central_probability':inside,'rank_limit':RANK_LIMIT,'upper_truncated':upper is None,'probability_to_limit':1-_survival(alpha,beta,RANK_LIMIT)}


def _scheduled(upcoming: list[dict], now: datetime, league: str | None):
    result, excluded, seen = [], Counter(), set()
    for game in upcoming:
        if league is not None and game.get('league') != league:
            excluded['other_league'] += 1
            continue
        if game.get('status') != 'scheduled' or game.get('administrative'):
            excluded['not_scheduled'] += 1
            continue
        kickoff = _kickoff(game)
        if kickoff is None:
            excluded['unconfirmed_datetime'] += 1
            continue
        if kickoff <= now:
            excluded['not_future'] += 1
            continue
        identity = _identity(game)
        if identity in seen:
            excluded['duplicate'] += 1
            continue
        seen.add(identity)
        result.append((kickoff, game))
    result.sort(key=lambda item: (item[0], str(item[1].get('id', '')), item[1].get('home',''), item[1].get('away','')))
    return result, excluded


def _sample(history: list[dict], cutoff: datetime, league: str | None):
    eligible, excluded, seen = [], Counter(), set()
    for game in history:
        if league is not None and game.get('league') != league:
            excluded['other_league'] += 1
            continue
        if game.get('status') != 'finished' or game.get('administrative'):
            excluded['unfinished_or_administrative'] += 1
            continue
        ht, ft = _scores(game,'ht'), _scores(game,'ft')
        if ht is None or ft is None or any(h > f for h,f in zip(ht,ft)):
            excluded['missing_or_invalid_score'] += 1
            continue
        kickoff = _kickoff(game)
        if kickoff is None:
            excluded['unconfirmed_datetime'] += 1
            continue
        if kickoff >= cutoff:
            excluded['not_before_forecast'] += 1
            continue
        season = _season(game)
        if season is None:
            excluded['invalid_season'] += 1
            continue
        identity = _identity(game)
        if identity in seen:
            excluded['duplicate'] += 1
            continue
        seen.add(identity)
        eligible.append((kickoff, game, season))
    seasons = sorted({s for _,_,s in eligible}, key=lambda s:(int(s[:4]),s))[-SEASONS:]
    selected = [item for item in eligible if item[2] in seasons]
    excluded['outside_seasons'] += len(eligible)-len(selected)
    selected.sort(key=lambda item:(item[0],str(item[1].get('id',''))))
    excluded['sample_limit'] += max(0,len(selected)-MAXIMUM_SAMPLE)
    selected = selected[-MAXIMUM_SAMPLE:]
    hits = sum(min(_scores(g,'ht')) > 0 for _,g,_ in selected)
    seasons = sorted({s for _,_,s in selected},key=lambda s:(int(s[:4]),s))
    return selected, {'n':len(selected),'hits':hits,'seasons':seasons,'from':selected[0][1]['date'] if selected else None,'to':selected[-1][1]['date'] if selected else None,'window':'Últimas 3 temporadas, hasta 1.000 partidos terminados con HT, FT y hora válidos.','minimum':MINIMUM_SAMPLE,'excluded':dict(excluded)}


def _schedule_view(items, probability):
    groups, result = [], []
    for kickoff, game in items:
        if not groups or groups[-1]['kickoff'] != kickoff.isoformat():
            groups.append({'group':len(groups)+1,'kickoff':kickoff.isoformat(),'date':kickoff.date().isoformat(),'time':kickoff.strftime('%H:%M'),'start_rank':len(result)+1,'end_rank':len(result)+1,'ids':[]})
        group = groups[-1]
        group['end_rank'] = len(result)+1
        group['ids'].append(game.get('id'))
        result.append({'id':game.get('id'),'date':kickoff.date().isoformat(),'time':kickoff.strftime('%H:%M'),'home':game.get('home'),'away':game.get('away'),'rank':len(result)+1,'kickoff':kickoff.isoformat(),'group':group['group'],'baseline_probability':probability})
    for group in groups:
        for row in result[group['start_rank']-1:group['end_rank']]:
            row.update(group_start_rank=group['start_rank'],group_end_rank=group['end_rank'],simultaneous=group['start_rank']!=group['end_rank'])
    return result, groups


def _window(waiting, upcoming, groups, alpha, beta):
    low, high = waiting['q10'], waiting['q90']
    start = upcoming[low-1] if low is not None and low <= len(upcoming) else None
    end = upcoming[high-1] if high is not None and high <= len(upcoming) else None
    from_rank = start['group_start_rank'] if start else low
    to_rank = end['group_end_rank'] if end else high
    start_group = groups[start['group']-1] if start else None
    end_group = groups[end['group']-1] if end else None
    relevant = [g for g in groups if g['start_rank']!=g['end_rank'] and (to_rank is None or g['start_rank']<=to_rank) and (from_rank is None or g['end_rank']>=from_rank)]
    inside = _survival(alpha,beta,from_rank-1)-_survival(alpha,beta,to_rank) if from_rank is not None and to_rank is not None else None
    return {'from_rank':from_rank,'to_rank':to_rank,'start':start_group,'end':end_group,'calendar_complete':start is not None and end is not None,'simultaneous_groups':relevant,'probability_inside_group_window':inside}


def forecast(history: list[dict], upcoming: list[dict], now: datetime | None = None) -> dict[str, Any]:
    """Estimate the next HT-BTTS waiting range for one league's future schedule.

    The baseline is identical for every fixture. Wilson's interval describes the
    observed historical frequency, not the accuracy of a team-specific forecast.
    Fixtures sharing a kickoff form a group; their IDs are a display tie-breaker,
    not a claimed temporal ordering. Missing times never become midnight.
    """
    now = _as_local(now)
    future_leagues = {g.get('league') for g in upcoming if g.get('league') and g.get('status')=='scheduled' and not g.get('administrative') and (dt:=_kickoff(g)) is not None and dt > now}
    history_leagues = {g.get('league') for g in history if g.get('league')}
    league = next(iter(future_leagues)) if len(future_leagues)==1 else next(iter(history_leagues)) if len(history_leagues)==1 else None
    base = {'status':'mixed_leagues','league':league,'as_of':now.isoformat(),'valid_until':None,'order':'chronological','timezone':'America/Bogota','model':'beta_bernoulli_league_baseline','sample':{'n':0,'hits':0,'seasons':[],'from':None,'to':None,'window':'Últimas 3 temporadas, hasta 1.000 partidos terminados con HT, FT y hora válidos.','minimum':MINIMUM_SAMPLE,'excluded':{}},'baseline':None,'horizons':[],'range':None,'upcoming':[],'schedule_excluded':{},'notice':'Selecciona una sola liga para calcular una referencia histórica.','assumptions':'Referencia de liga con una probabilidad compartida por partido; no incorpora características de cada equipo ni tiene validación predictiva.'}
    if len(future_leagues)>1 or league is None and len(history_leagues)>1:
        return base
    scheduled, exclusions = _scheduled(upcoming,now,league)
    cutoff = min(now, scheduled[0][0]) if scheduled else now
    _, sample = _sample(history,cutoff,league)
    base.update(sample=sample,schedule_excluded=dict(exclusions),valid_until=scheduled[0][0].isoformat() if scheduled else None)
    n, hits = sample['n'], sample['hits']
    if n < MINIMUM_SAMPLE:
        base.update(status='insufficient_history',notice=f'Se necesitan al menos {MINIMUM_SAMPLE} partidos terminados con HT, FT y hora válidos; hay {n}.')
        base['upcoming'],_ = _schedule_view(scheduled,None)
        return base
    alpha, beta = hits+0.5,n-hits+0.5
    probability = alpha/(alpha+beta)
    base['baseline'] = {'probability':probability,'observed_rate':hits/n,'wilson95':_wilson(hits,n),'alpha':alpha,'beta':beta,'label':'Referencia histórica de la liga, no estimación específica del equipo.','interval_label':'Intervalo 95% de la frecuencia histórica, no de la precisión predictiva.'}
    base['horizons'] = [{'matches':k,'probability_at_least_one':1-_survival(alpha,beta,k)} for k in HORIZONS]
    base['upcoming'], groups = _schedule_view(scheduled,probability)
    if not scheduled:
        base.update(status='no_upcoming',notice='No hay próximos partidos con fecha y hora confirmadas; la frecuencia histórica no puede vincularse todavía a un calendario.')
        return base
    waiting = _waiting_time(alpha,beta)
    waiting['window'] = _window(waiting,base['upcoming'],groups,alpha,beta)
    waiting['calendar_probability_at_least_one'] = 1-_survival(alpha,beta,len(scheduled))
    base.update(status='ok',range=waiting,notice='Rango central 10–90% bajo el modelo histórico; puede ocurrir fuera de él. Una racha sin casos no aumenta por sí sola la probabilidad del siguiente partido.')
    if not waiting['window']['calendar_complete']:
        base['notice'] += ' El calendario publicado no alcanza a cubrir ambos extremos del rango; no se inventan fechas.'
    if waiting['upper_truncated']:
        base['notice'] += f' El percentil 90 supera los {RANK_LIMIT} partidos calculados.'
    if waiting['window']['simultaneous_groups']:
        base['notice'] += ' Los partidos simultáneos se agrupan; el orden entre ellos no indica cuál ocurrirá primero.'
    if hits==0:
        base['notice'] += ' No se observaron casos en la muestra; la probabilidad positiva procede del suavizado estadístico.'
    return base
