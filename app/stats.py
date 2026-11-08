"""Statistics computed only from finished, played matches and known observations."""
from collections import Counter
from statistics import mean
from .leagues import LEAGUES


def avg(values):
    return mean(values) if values else None


def pair(game, period):
    return game.get(period + '_home'), game.get(period + '_away')


def known(pair):
    return all(type(x) is int and x >= 0 for x in pair)


def btts(pair):
    return known(pair) and min(pair) > 0


def rate(n, base):
    return n / base if base else None


def frequency(games, period):
    hits = [i for i, g in enumerate(games, 1) if btts(pair(g, period))]
    boundaries = [0, *hits, len(games) + 1]
    return {'max_without': max((b-a-1 for a,b in zip(boundaries,boundaries[1:])), default=0) if games else None, 'current_without': len(games)-hits[-1] if hits else len(games) if games else None, 'mean_gap': avg([b-a for a,b in zip(hits,hits[1:])])}


def summarize(games, team='all'):
    eligible = [g for g in games if g['status'] == 'finished' and not g.get('administrative')]
    ht = [g for g in eligible if known(pair(g, 'ht'))]
    ft = [g for g in eligible if known(pair(g, 'ft'))]
    h = sum(btts(pair(g, 'ht')) for g in ht)
    f = sum(btts(pair(g, 'ft')) for g in ft)
    corners = [g['corners'] for g in ft if g.get('corners') is not None]
    yellow = [g['yellow'] for g in ft if g.get('yellow') is not None]
    red = [g['red'] for g in ft if g.get('red') is not None]
    outcomes = Counter('1' if g['ft_home'] > g['ft_away'] else '2' if g['ft_home'] < g['ft_away'] else 'X' for g in ft)
    unresolved = sum(not known(pair(g,'ht')) or not known(pair(g,'ft')) or (LEAGUES.get(g['league'],{}).get('corners',True) and g.get('corners') is None) or (LEAGUES.get(g['league'],{}).get('cards',False) and (g.get('yellow') is None or g.get('red') is None)) for g in eligible)
    summary = {'total': len(games), 'finished': len(ft), 'scheduled':sum(g['status']=='scheduled' for g in games), 'live':sum(g['status']=='live' for g in games), 'excluded':sum(bool(g.get('administrative')) for g in games), 'ht_base':len(ht), 'ht_cases':h, 'ht_rate':rate(h,len(ht)), 'ht_every':len(ht)/h if h else None, 'ft_base':len(ft), 'ft_cases':f, 'ft_rate':rate(f,len(ft)), 'ft_every':len(ft)/f if f else None, 'goals_ht_average':avg([sum(pair(g,'ht')) for g in ht]), 'goals_ft_average':avg([sum(pair(g,'ft')) for g in ft]), 'corners_average':avg(corners), 'corners_base':len(corners), 'corners_missing':sum(LEAGUES.get(g['league'],{}).get('corners',True) and g.get('corners') is None for g in ft), 'yellow_average':avg(yellow), 'yellow_base':len(yellow), 'red_average':avg(red), 'red_base':len(red), 'red_match_rate':rate(sum(r>0 for r in red),len(red)), 'red_matches':sum(r>0 for r in red), 'yellow_total':sum(yellow) if yellow else None, 'red_total':sum(red) if red else None, 'home_wins':outcomes['1'], 'draws':outcomes['X'], 'away_wins':outcomes['2'], 'unresolved':unresolved}
    scorelines = {}
    for period, items in [('ht',ht),('ft',ft)]:
        counts=Counter(pair(g,period) for g in items)
        scorelines[period]=[{'score':f'{a}-{b}', 'count':n} for (a,b),n in sorted(counts.items(), key=lambda i:(-i[1],i[0]))[:3]]
    result = {'summary':summary,'frequency':{'ht':frequency(ht,'ht'),'ft':frequency(ft,'ft')},'scorelines':scorelines}
    if team != 'all':
        pairs = [(g['ft_home'],g['ft_away']) if g['home']==team else (g['ft_away'],g['ft_home']) for g in ft]
        own_y = [g.get('yellow_home' if g['home']==team else 'yellow_away') for g in ft]
        own_r = [g.get('red_home' if g['home']==team else 'red_away') for g in ft]
        result['team_stats']={'team':team,'pj':len(ft),'wins':sum(a>b for a,b in pairs),'draws':sum(a==b for a,b in pairs),'losses':sum(a<b for a,b in pairs),'gf':sum(a for a,b in pairs),'ga':sum(b for a,b in pairs),'own_yellow_average':avg([n for n in own_y if n is not None]),'own_red_average':avg([n for n in own_r if n is not None])}
    else:
        result['team_stats']=None
    return result
