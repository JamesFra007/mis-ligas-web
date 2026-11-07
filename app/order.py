"""The workbook order stays stable; calendar order uses confirmed local kickoffs."""
from datetime import datetime
from zoneinfo import ZoneInfo

BOGOTA=ZoneInfo('America/Bogota')


def calendar_key(game):
    date=game.get('date') or ''
    date_known=bool(date) and game.get('date_confirmed', True)
    time=game.get('time') or ''
    time_known=bool(time) and game.get('time_confirmed',True)
    # For unconfirmed calendar placeholders we show the uncertainty and sort last.
    return (not date_known, date if date_known else '', not time_known, time if time_known else '', game.get('source_order',0),game['id'])


def order_games(rows, order='round'):
    if order not in ('round','chronological'):
        raise ValueError('Orden no válido. Elige jornada o fecha y hora.')
    return sorted(rows,key=calendar_key) if order=='chronological' else list(rows)
