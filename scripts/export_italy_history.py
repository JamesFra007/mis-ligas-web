"""Export Serie A seasons with HT/FT, corners and the two card totals.

The workbook is a view of the application's verified records. Unknown values
remain explicit, while a known zero in red cards is deliberately left blank.
"""
from __future__ import annotations

from pathlib import Path
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.leagues import LEAGUES
from app.main import card_icon
from app.stats import btts, known, pair, summarize
from app.streaks import ft_droughts, ht_droughts, red_droughts

NAVY = '172554'
GREEN = '059669'
TEAL = '0F766E'
PURPLE = '7C3AED'
BLUE = '2563EB'
PINK = 'DB2777'
ORANGE = 'EA580C'
RED = 'B91C1C'
HEADER_ROW = 30
HEADERS = ['N.º', 'Fecha · hora COL', 'Jornada', 'Local', 'Visitante', 'HT', 'FT',
           'Córners', 'Amarillas', 'Rojas', 'Vuelta / fase', 'Estado', 'Observación']


def _cell(ws, row, col, value, *, color=None, bold=False, size=10, fill=None, wrap=True):
    cell = ws.cell(row, col, value)
    if isinstance(value, str):
        cell.data_type = 's'
    cell.font = Font(name='Arial', size=size, bold=bold, color=color or '334155')
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=wrap)
    if fill:
        cell.fill = PatternFill('solid', fgColor=fill)
    return cell


def _merge(ws, row, first, last, value, *, fill=None, size=10, bold=False):
    ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    cell = _cell(ws, row, first, value, size=size, bold=bold or bool(fill),
                 color='FFFFFF' if fill else NAVY)
    if fill:
        for col in range(first, last + 1):
            ws.cell(row, col).fill = PatternFill('solid', fgColor=fill)
    return cell


def _scope(game):
    scope = {'first':'Primera vuelta', 'second':'Segunda vuelta', 'all':''}.get(game.get('scope'), game.get('scope') or '')
    phase = game.get('phase') or ''
    return ' · '.join(value for value in (scope, phase if phase not in ('Serie A', scope) else '') if value) or phase


def _rounds(spell):
    groups = {}
    for item in spell.get('rounds', []):
        label = _scope(item)
        groups.setdefault(label, [])
        value = str(item['round']) if item.get('round') is not None else 'adicional' if item.get('phase') == 'Desempate por la permanencia' else 'pendiente'
        if value not in groups[label]:
            groups[label].append(value)
    return ' | '.join(('Partido adicional' if numbers == ['adicional'] else 'J' + ', '.join(numbers)) + (' · ' + context if context else '') for context, numbers in groups.items())


def _spell_label(spell):
    if not spell:
        return 'Sin tramo abierto verificado'
    label = ('Mín. ' if spell.get('lower_bound') else '') + str(spell['count']) + ' PJ'
    return label + '\n' + spell['start']['date'] + ' → ' + spell['end']['date'] + '\n' + _rounds(spell)


def _score(game, period):
    if known(pair(game, period)) and (period == 'ft' or not game.get('administrative')):
        return '-'.join(map(str, pair(game, period)))
    return 'N/D' if game['status'] == 'finished' and not game.get('administrative') else ''


def _stat(game, field):
    if game.get(field) is not None and game['status'] == 'finished' and not game.get('administrative'):
        return game[field]
    return 'N/D' if game['status'] == 'finished' and not game.get('administrative') else ''


def _missing(game):
    fields = []
    if game['status'] == 'finished' and not game.get('administrative'):
        for period in ('ht', 'ft'):
            if not known(pair(game, period)):
                fields.append(period.upper())
        for field, label in [('corners','Córners'), ('yellow','Amarillas'), ('red','Rojas')]:
            if game.get(field) is None:
                fields.append(label)
    return fields


def _team_table(ws, start, games):
    _merge(ws, start, 1, 13, 'POR EQUIPO · RESULTADOS Y TARJETAS PROPIAS', fill=NAVY)
    ws.row_dimensions[start].height = 24
    _merge(ws, start + 1, 1, 3, 'Equipo', fill='1F4E78')
    headers = ['PJ', 'G / E / P', 'GF', 'GC', 'Ambos HT', 'Ambos FT',
               'Amarillas / PJ', 'Rojas / PJ', 'Base amarillas', 'Base rojas']
    for col, label in enumerate(headers, 4):
        _cell(ws, start + 1, col, label, fill='1F4E78', color='FFFFFF', bold=True, size=9)
    ws.row_dimensions[start + 1].height = 30
    for row, team in enumerate(sorted({g[key] for g in games for key in ('home','away')}), start + 2):
        selected = [g for g in games if g['home'] == team or g['away'] == team]
        result = summarize(selected, team)
        stats, own = result['summary'], result['team_stats']
        eligible = [g for g in selected if g['status'] == 'finished' and not g.get('administrative') and known(pair(g,'ft'))]
        yellow_base = sum(g.get('yellow_home' if g['home'] == team else 'yellow_away') is not None for g in eligible)
        red_base = sum(g.get('red_home' if g['home'] == team else 'red_away') is not None for g in eligible)
        _merge(ws, row, 1, 3, team)
        values = [own['pj'], f"{own['wins']} / {own['draws']} / {own['losses']}", own['gf'], own['ga'],
                  stats['ht_rate'], stats['ft_rate'], own['own_yellow_average'], own['own_red_average'], yellow_base, red_base]
        for col, value in enumerate(values, 4):
            cell = _cell(ws, row, col, value if value is not None else 'N/D')
            if col in (8,9):
                cell.number_format = '0.0%'
            elif col in (10,11):
                cell.number_format = '0.00'
        ws.row_dimensions[row].height = 24


def export_history(output=None):
    """Write one sheet per available Italian season plus a separate Notes sheet.

    The caller initializes the application's DB before invoking this function,
    just as for the other historical workbook exporters. No source is fetched.
    """
    seasons = [s for s in db.all_seasons() if s['league'] == 'italy']
    if not seasons:
        return None
    book = Workbook()
    book.remove(book.active)
    notes, sources, source_urls = [], set(), set()
    for season in seasons:
        key = season['season']
        games = db.games('italy', key)
        result = summarize(games)
        s = result['summary']
        reports = {'ht':ht_droughts(games), 'ft':ft_droughts(games), 'red':red_droughts(games)}
        ws = book.create_sheet('SERIE A ' + key[2:])
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = None
        _merge(ws, 1, 1, 13, 'ITALIA · SERIE A · ' + key.replace('-','/'), fill=NAVY, size=18)
        ws.row_dimensions[1].height = 32
        _merge(ws, 2, 1, 13, 'Resumen de la temporada. Orden por jornada/listado original. Fecha y hora de Colombia.')
        ws.row_dimensions[2].height = 24
        tiles = [
            ('AMBOS MARCAN · HT', s['ht_rate'], '0.0%', GREEN, f"{s['ht_cases']} casos / {s['ht_base']} partidos con HT"),
            ('AMBOS MARCAN · FT', s['ft_rate'], '0.0%', PURPLE, f"{s['ft_cases']} casos / {s['ft_base']} partidos con FT"),
            ('CÓRNERS / PARTIDO', s['corners_average'], '0.00', ORANGE, f"Total de ambos · {s['corners_base']} partidos con dato"),
            ('GOLES / PARTIDO · FT', s['goals_ft_average'], '0.00', BLUE, 'Promedio de los dos equipos al finalizar'),
            ('GOLES / PARTIDO · HT', s['goals_ht_average'], '0.00', TEAL, 'Promedio de los dos equipos al descanso'),
            ('AMARILLAS / PARTIDO', s['yellow_average'], '0.00', ORANGE, f"{s['yellow_total'] if s['yellow_total'] is not None else 'N/D'} tarjetas · base {s['yellow_base']}"),
            ('PARTIDOS CON ROJA', s['red_match_rate'], '0.0%', RED, f"{s['red_matches']} partidos · base {s['red_base']}"),
            ('PARTIDOS ANALIZADOS', s['finished'], '0', BLUE, f"{s['excluded']} administrativos fuera del análisis"),
        ]
        for index, (label, value, fmt, color, subtitle) in enumerate(tiles):
            row = 4 if index < 4 else 8
            first, last = [(1,3), (4,6), (7,9), (10,13)][index % 4]
            _merge(ws, row, first, last, label, fill=color, size=9)
            cell = _merge(ws, row + 1, first, last, value if value is not None else 'N/D', fill=color, size=24)
            cell.number_format = fmt
            _merge(ws, row + 2, first, last, subtitle, size=9)
            ws.row_dimensions[row].height = 24
            ws.row_dimensions[row + 1].height = 36
            ws.row_dimensions[row + 2].height = 23
        _merge(ws, 12, 1, 13, f"Registros: {s['total']} · Finalizados: {s['finished']} · Programados: {s['scheduled']} · En juego: {s['live']} · Administrativos: {s['excluded']}")
        ws.row_dimensions[12].height = 26
        _merge(ws, 14, 1, 13, 'FRECUENCIA Y MAYORES RACHAS · PARTIDOS TERMINADOS EN ORDEN CRONOLÓGICO', fill=NAVY)
        ws.row_dimensions[14].height = 25
        for first, last, label in [(1,2,'Suceso'), (3,4,'Casos / base'), (5,6,'Un caso cada N PJ'),
                                    (7,8,'Rachas sin caso · TOP 3'), (9,11,'Fechas y jornadas del récord'), (12,13,'Racha al cierre')]:
            _merge(ws, 15, first, last, label, fill='1F4E78', size=9)
        ws.row_dimensions[15].height = 30
        for row, period, title in [(16,'ht','Ambos marcan · HT'), (17,'ft','Ambos marcan · FT'), (18,'red','Al menos una roja')]:
            report = reports[period]
            top = report['top']
            cases = s[period + '_cases'] if period != 'red' else s['red_matches']
            base = s[period + '_base'] if period != 'red' else s['red_base']
            every = base / cases if cases else None
            _merge(ws, row, 1, 2, title, bold=True)
            _merge(ws, row, 3, 4, f'{cases} / {base}')
            _merge(ws, row, 5, 6, every if every is not None else 'Sin casos').number_format = '0.00'
            _merge(ws, row, 7, 8, '\n'.join(('Mín. ' if item.get('lower_bound') else '') + str(item['count']) + ' PJ' for item in top) if top else 'Sin racha verificada')
            record = top[0] if top else None
            _merge(ws, row, 9, 11, _spell_label(record) if record else 'Sin racha verificada')
            _merge(ws, row, 12, 13, _spell_label(report['current']))
            ws.row_dimensions[row].height = 54
        _merge(ws, 20, 1, 13, f"RESULTADOS FT · Gana local: {s['home_wins']} · Empate: {s['draws']} · Gana visitante: {s['away_wins']}", bold=True)
        _merge(ws, 21, 1, 13, 'MARCADOR MÁS HABITUAL · ' + ' | '.join(period.upper() + ': ' + ', '.join(item['score'] + ' (' + str(item['count']) + ')' for item in result['scorelines'][period]) for period in ('ht','ft')))
        _merge(ws, 22, 1, 13, f"COBERTURA · HT conocidos: {s['ht_base']} · FT conocidos: {s['ft_base']} · Córners: {s['corners_base']} · Amarillas: {s['yellow_base']} · Rojas: {s['red_base']} · Partidos con algún dato pendiente: {s['unresolved']}")
        for row in (20,21,22):
            ws.row_dimensions[row].height = 25
        _merge(ws, 24, 1, 13, 'Frecuencia = partidos con dato ÷ casos; describe el historial y no garantiza el siguiente partido. Los datos pendientes interrumpen las rachas verificadas. «Mín.» señala un límite incierto; los simultáneos se agrupan.', size=9)
        ws.row_dimensions[24].height = 30
        _merge(ws, 27, 1, 13, 'PARTIDOS · LISTADO DE LA TEMPORADA', fill=NAVY)
        ws.row_dimensions[27].height = 25
        _merge(ws, 28, 1, 13, 'Verde: ambos marcaron en HT, desde el número hasta córners. Rojas vacías = cero confirmado. N/D = dato pendiente. Las tarjetas son el total de ambos equipos.', size=9)
        ws.row_dimensions[28].height = 25
        for col, title in enumerate(HEADERS, 1):
            _cell(ws, HEADER_ROW, col, title, fill='1F4E78', color='FFFFFF', bold=True)
        ws.row_dimensions[HEADER_ROW].height = 28
        state_names = {'finished':'Finalizado', 'scheduled':'Programado', 'live':'En juego', 'postponed':'Aplazado', 'cancelled':'Cancelado'}
        for number, game in enumerate(games, 1):
            row = HEADER_ROW + number
            finished = game['status'] == 'finished' and not game.get('administrative')
            missing = _missing(game)
            observation = game.get('administrative_reason') or ('Fuera del análisis: resultado administrativo.' if game.get('administrative') else 'Pendiente: ' + ', '.join(missing) if missing else '')
            calendar = 'Fecha pendiente' if game.get('date_confirmed') is False else game['date'] + (' ' + game['time'] if game.get('time') and game.get('time_confirmed',True) else ' · Hora pendiente')
            red = None if game.get('red') == 0 else _stat(game, 'red')
            values = [number, calendar, game.get('round') if game.get('round') is not None else 'Adicional' if game.get('round_not_applicable') else 'Pendiente',
                      game['home'], game['away'], _score(game,'ht'), _score(game,'ft'),
                      _stat(game,'corners'), _stat(game,'yellow'), red, _scope(game),
                      'Administrativo' if game.get('administrative') else state_names.get(game['status'],game['status']), observation]
            highlighted = finished and btts(pair(game,'ht'))
            for col, value in enumerate(values, 1):
                blank_red = col == 10 and red is None
                fill = 'C6EFCE' if highlighted and col <= 8 else 'F3F6FB' if number % 2 == 0 and not blank_red else None
                cell = _cell(ws, row, col, value, fill=fill, size=10)
                if col in (6,7):
                    cell.number_format = '@'
            ws.row_dimensions[row].height = 25
            if finished and game.get('yellow') is not None:
                card_icon(ws, row, 9, 'yellow')
            if finished and game.get('red') is not None and game['red'] > 0:
                card_icon(ws, row, 10, 'red')
            notices = list(missing)
            if game.get('round') is None and not game.get('round_not_applicable'):
                notices.append('Jornada pendiente')
            if game.get('date_confirmed') is False:
                notices.append('Fecha pendiente')
            elif not game.get('time') or game.get('time_confirmed') is False:
                notices.append('Hora pendiente')
            if game.get('administrative'):
                notices.append(observation)
            if notices:
                notes.append([key, game['date'], game['home'] + ' vs ' + game['away'], '; '.join(notices), game['id'], game.get('source_url') or ''])
            if game.get('source'):
                sources.add(game['source'])
            for field in ('source_url','round_source_url','ht_source_url','ht_verification_url','time_source_url'):
                if game.get(field):
                    source_urls.add(game[field])
        ws.auto_filter.ref = f'A{HEADER_ROW}:M{HEADER_ROW + len(games)}'
        _team_table(ws, HEADER_ROW + len(games) + 4, games)
        for col, width in enumerate([7,25,12,26,26,10,10,12,13,12,26,18,48], 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
    note_sheet = book.create_sheet('Notas')
    note_sheet.freeze_panes = None
    note_sheet.sheet_view.showGridLines = False
    for line in [
        'ITALIA · SERIE A · Histórico de la aplicación desde 2018/19.',
        'HT y FT son texto para conservar marcadores como 1-1 sin transformarlos en fechas.',
        'Las tarjetas son el total de los dos equipos. Rojas vacías en un partido finalizado = cero confirmado; N/D = dato no encontrado.',
        'Los partidos futuros no tienen resultados ni estadísticas de juego. Los administrativos se conservan y se excluyen del análisis.',
        'La tabla por equipo muestra sus amarillas y rojas propias; cada media utiliza solo observaciones conocidas.',
        'Las rachas se calculan por fecha y hora de Colombia, agrupando simultáneos. Los datos desconocidos interrumpen la continuidad verificada.',
        'La racha al cierre corresponde al último encuentro de esa temporada, no necesariamente al día de apertura del archivo.',
        'Orden por jornada/listado original de la temporada. Ninguna hoja inmoviliza filas ni columnas.',
        'Fuentes de los registros: ' + '; '.join(sorted(sources)),
    ]:
        note_sheet.append([line])
    for url in sorted(source_urls):
        note_sheet.append(['Fuente consultada', url])
    note_sheet.append([])
    note_sheet.append(['Temporada','Fecha COL','Partido','Dato pendiente / incidencia','ID','Fuente del registro'])
    for note in notes:
        note_sheet.append(note)
    for row in note_sheet:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = 's'
            cell.font = Font(name='Arial', size=10)
            cell.alignment = Alignment(vertical='center', wrap_text=True)
    for col, width in enumerate([24,28,55,70,45,95], 1):
        note_sheet.column_dimensions[get_column_letter(col)].width = width
    filename = LEAGUES.get('italy', {}).get('history_file', 'SerieA_Italia_2018_2027.xlsx')
    target = Path(output) if output else ROOT / 'data/history' / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    book.save(target)
    return target


if __name__ == '__main__':
    db.initialize()
    print(export_history())
