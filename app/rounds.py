"""Real matchday groups, including repeated numbers across tournaments/phases."""
import json


def round_id(game):
    number = game.get('round')
    return json.dumps([game.get('scope') or 'all', game.get('phase') or '', None if number == '' else number], ensure_ascii=False, separators=(',', ':'))


def round_label(game):
    scope = {'all':'', 'first':'Primera vuelta', 'second':'Segunda vuelta'}.get(game.get('scope'), game.get('scope') or '')
    phase = game.get('phase') or ''
    context = [scope] if scope else []
    if phase and phase not in ('LaLiga', 'NB I', 'Ekstraklasa', 'Liga BetPlay') and phase not in context:
        context.append(phase)
    number = game.get('round')
    context.append('Jornada pendiente' if number is None or number == '' else f'Jornada {number}')
    return ' · '.join(context)


def round_options(rows):
    options = {}
    for game in sorted(rows, key=lambda g: g.get('source_order') or 0):
        key = round_id(game)
        if key not in options:
            options[key] = {'id':key, 'label':round_label(game), 'count':0}
        options[key]['count'] += 1
    return list(options.values())


def select_round(rows, selected='', options=None):
    """Empty preserves the full list; stale selections fall back to first group."""
    if not selected:
        return rows, ''
    options = round_options(rows) if options is None else options
    ids = {item['id'] for item in options}
    key = selected if selected in ids else options[0]['id'] if options else ''
    return [game for game in rows if round_id(game) == key], key
