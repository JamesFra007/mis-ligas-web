"""Verified chronological spells without both teams scoring or a red card.

Kickoffs that cannot be ordered are treated as a block. A block containing a
BTTS case ends a spell, but its simultaneous non-cases are never placed on an
invented side of that case. Missing observations break verified continuity.
"""
from collections import defaultdict

from .stats import btts, known, pair


NOTE = ('Orden cronológico de los partidos finalizados de la selección. Los HT pendientes interrumpen la verificación. '
        'Los partidos simultáneos se agrupan; si falta una hora, se agrupa ese día. En bloques con algún ambos marcan, '
        'los demás partidos no se asignan a una racha. «Mínimo» indica un tramo verificado cuyo límite exacto es incierto.')


def _note(metric):
    if metric == 'ht':
        return NOTE
    if metric == 'ft':
        return NOTE.replace('Los HT pendientes', 'Los FT pendientes')
    return NOTE.replace('Los HT pendientes', 'Los datos de tarjetas rojas pendientes').replace('con algún ambos marcan', 'con alguna tarjeta roja')


def _observation(game, metric):
    """None is unknown, False extends a spell, True closes it."""
    if metric == 'red':
        value = game.get('red')
        return value > 0 if type(value) is int and value >= 0 else None
    score = pair(game, metric)
    return btts(score) if known(score) else None


def _date_known(game):
    return bool(game.get('date')) and game.get('date_confirmed', True) is not False


def _time_known(game):
    return bool(game.get('time')) and game.get('time_confirmed', True) is not False


def _ref(game):
    return {key: game.get(key) for key in ('id', 'date', 'round', 'scope', 'phase', 'home', 'away')} | {
        'time': game.get('time') if _time_known(game) else None,
    }


def _key(game):
    return game.get('date') or '', game.get('time') if _time_known(game) else '', str(game.get('id', ''))


def _droughts(rows, metric):
    """Return at most three longest spells and the latest still-open spell.

    ``count`` counts verified non-cases, not the BTTS match closing a spell.
    ``lower_bound`` marks spells touching a missing observation or mixed block.
    A missing date prevents positioning a possible break anywhere in the season,
    so no continuous spells are certified in that selection.
    """
    played = [g for g in rows if g.get('status') == 'finished' and not g.get('administrative')]
    missing = sum(_observation(g, metric) is None for g in played)
    note = _note(metric)
    undated = [g for g in played if not _date_known(g)]
    result = {
        'top': [], 'current': None, 'missing_ht' if metric == 'ht' else 'missing_data': missing,
        'uncertain_order': sum(not _date_known(g) or not _time_known(g) for g in played),
        'eligible': len(played) - missing, 'mixed_blocks': 0, 'note': note,
    }
    if undated:
        result['note'] = ('No se pueden certificar rachas continuas: hay partidos finalizados sin fecha confirmada. ' + note)
        return result

    by_date = defaultdict(list)
    for game in played:
        by_date[game['date']].append(game)
    blocks = []
    for day in sorted(by_date):
        day_games = sorted(by_date[day], key=_key)
        if any(not _time_known(g) for g in day_games):
            blocks.append(day_games)
        else:
            times = defaultdict(list)
            for game in day_games:
                times[game['time']].append(game)
            blocks.extend(times[time] for time in sorted(times))

    spells, active = [], []
    left_uncertain = False

    def finish(state, *, ended_by=None, interrupted_by=None, right_uncertain=False):
        nonlocal active
        if not active:
            return None
        rounds = []
        seen = set()
        for game in active:
            key = (game.get('round'), game.get('scope'), game.get('phase'))
            if key not in seen:
                seen.add(key)
                rounds.append({name: game.get(name) for name in ('round', 'scope', 'phase')})
        spell = {
            'count': len(active), 'start': _ref(active[0]), 'end': _ref(active[-1]),
            'ended_by': _ref(ended_by) if ended_by is not None else None,
            'state': state, 'rounds': rounds,
            'interrupted_by': _ref(interrupted_by) if interrupted_by is not None else None,
            'lower_bound': bool(left_uncertain or right_uncertain),
        }
        spells.append(spell)
        active = []
        return spell

    for block in blocks:
        unknown = [g for g in block if _observation(g, metric) is None]
        hits = [g for g in block if _observation(g, metric) is True]
        if hits and len(hits) != len(block):
            result['mixed_blocks'] += 1
        if unknown:
            finish('interrupted', interrupted_by=unknown[0], right_uncertain=True)
            left_uncertain = True
        elif hits:
            mixed = len(hits) != len(block)
            finish('closed', ended_by=hits[0], right_uncertain=mixed)
            left_uncertain = mixed
        else:
            active.extend(block)
    result['current'] = finish('current')
    result['top'] = sorted(spells, key=lambda s: (-s['count'], s['start']['date'], s['start']['time'] or '', str(s['start']['id'])))[:3]
    return result


def ht_droughts(rows):
    return _droughts(rows, 'ht')


def ft_droughts(rows):
    return _droughts(rows, 'ft')


def red_droughts(rows):
    return _droughts(rows, 'red')
