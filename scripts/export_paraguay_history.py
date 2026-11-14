"""Export Paraguay by calendar year, retaining both tournaments in each sheet.

Only observed HT, FT and corners are exported. Unknown observations stay N/D;
future matches have empty result cells and never become artificial zeroes.
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
from app.stats import btts, known, pair, summarize
from app.streaks import ft_droughts, ht_droughts

NAVY = '172554'
GREEN = '059669'
TEAL = '0F766E'
PURPLE = '7C3AED'
BLUE = '2563EB'
ORANGE = 'EA580C'
HEADER_ROW = 40
HEADERS = ['N.º', 'Fecha · hora COL', 'Jornada', 'Local', 'Visitante', 'HT', 'FT',
           'Córners', 'Torneo', 'Fase', 'Estado', 'Observación']


def _cell(ws, row, col, value, *, color=None, bold=False, size=10, fill=None):
    cell = ws.cell(row, col, value)
    if isinstance(value, str):
        cell.data_type = 's'
    cell.font = Font(name='Arial', size=size, bold=bold, color=color or '334155')
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
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


def _context(game):
    scope = game.get('scope') or ''
    scope = '' if scope == 'all' else scope
    phase = game.get('phase') or ''
    return ' · '.join(value for value in (scope, phase if phase != scope else '') if value) or 'Temporada'


def _rounds(spell):
    groups = {}
    for item in spell.get('rounds', []):
        context = _context(item)
        value = str(item['round']) if item.get('round') is not None else 'fase final' if item.get('phase') not in ('', None, 'Fase regular') else 'pendiente'
        values = groups.setdefault(context, [])
        if value not in values:
            values.append(value)
    return ' | '.join('J ' + ', '.join(values) + ' · ' + context for context, values in groups.items())


def _spell_label(spell):
    if not spell:
        return 'Sin tramo abierto verificado'
    return (('Mín. ' if spell.get('lower_bound') else '') + str(spell['count']) + ' PJ\n' +
            spell['start']['date'] + ' → ' + spell['end']['date'] + '\n' + _rounds(spell))


def _score(game, period):
    if known(pair(game, period)) and (period == 'ft' or not game.get('administrative')):
        return '-'.join(map(str, pair(game, period)))
    return 'N/D' if game['status'] == 'finished' and not game.get('administrative') else ''


def _corners(game):
    if game['status'] == 'finished' and not game.get('administrative'):
        return game['corners'] if game.get('corners') is not None else 'N/D'
    return ''


def _missing(game):
    if game['status'] != 'finished' or game.get('administrative'):
        return []
    return [period.upper() for period in ('ht', 'ft') if not known(pair(game, period))] + (['Córners'] if game.get('corners') is None else [])


def _team_table(ws, start, games):
    _merge(ws, start, 1, 12, 'POR EQUIPO · CÓRNERS TOTALES DE SUS PARTIDOS', fill=NAVY)
    ws.row_dimensions[start].height = 25
    _merge(ws, start + 1, 1, 3, 'Equipo', fill='1F4E78')
    headings = ['PJ', 'G / E / P', 'GF', 'GC', 'Ambos HT', 'Ambos FT',
                'Córners / PJ', 'Base córners', 'PJ con datos pendientes']
    for col, label in enumerate(headings, 4):
        _cell(ws, start + 1, col, label, fill='1F4E78', color='FFFFFF', bold=True, size=9)
    ws.row_dimensions[start + 1].height = 32
    for row, team in enumerate(sorted({g[k] for g in games for k in ('home', 'away')}), start + 2):
        selected = [g for g in games if team in (g['home'], g['away'])]
        data = summarize(selected, team)
        s, own = data['summary'], data['team_stats']
        _merge(ws, row, 1, 3, team)
        values = [own['pj'], f"{own['wins']} / {own['draws']} / {own['losses']}", own['gf'], own['ga'],
                  s['ht_rate'], s['ft_rate'], s['corners_average'], s['corners_base'], s['unresolved']]
        for col, value in enumerate(values, 4):
            cell = _cell(ws, row, col, value if value is not None else 'N/D')
            cell.number_format = '0.0%' if col in (8, 9) else '0.00' if col == 10 else '0'
        ws.row_dimensions[row].height = 25


def export_history(output=None):
    """Write one sheet per available year and a Notes sheet, without fetching."""
    seasons = [s for s in db.all_seasons() if s['league'] == 'paraguay']
    if not seasons:
        return None
    book = Workbook()
    book.remove(book.active)
    notes, sources, source_urls = [], set(), set()
    for season in seasons:
        key = season['season']
        games = db.games('paraguay', key)
        data = summarize(games)
        s = data['summary']
        reports = {'ht':ht_droughts(games), 'ft':ft_droughts(games)}
        ws = book.create_sheet('PARAGUAY ' + key)
        ws.freeze_panes = None
        ws.sheet_view.showGridLines = False
        _merge(ws, 1, 1, 12, 'PARAGUAY · PRIMERA DIVISIÓN · ' + key, fill=NAVY, size=18)
        ws.row_dimensions[1].height = 32
        _merge(ws, 2, 1, 12, 'Temporada completa: Apertura y Clausura en la misma hoja, incluidas sus fases finales. Hora de Colombia.')
        ws.row_dimensions[2].height = 26
        tiles = [
            ('AMBOS MARCAN · HT', s['ht_rate'], '0.0%', GREEN, f"{s['ht_cases']} casos / {s['ht_base']} partidos con HT"),
            ('AMBOS MARCAN · FT', s['ft_rate'], '0.0%', PURPLE, f"{s['ft_cases']} casos / {s['ft_base']} partidos con FT"),
            ('CÓRNERS / PARTIDO', s['corners_average'], '0.00', ORANGE, f"Total de ambos · base {s['corners_base']}"),
            ('GOLES / PARTIDO · FT', s['goals_ft_average'], '0.00', BLUE, 'Promedio de ambos equipos al finalizar'),
            ('GOLES / PARTIDO · HT', s['goals_ht_average'], '0.00', TEAL, 'Promedio de ambos equipos al descanso'),
            ('UN CASO HT CADA', s['ht_every'], '0.00', GREEN, 'Partidos con HT conocido ÷ casos'),
            ('CÓRNERS PENDIENTES', s['corners_missing'], '0', ORANGE, 'Finalizados sin dato; nunca equivalen a cero'),
            ('PARTIDOS ANALIZADOS', s['finished'], '0', BLUE, f"{s['excluded']} administrativos fuera del análisis"),
        ]
        for index, (label, value, fmt, color, subtitle) in enumerate(tiles):
            row = 4 if index < 4 else 8
            first, last = [(1, 3), (4, 6), (7, 9), (10, 12)][index % 4]
            _merge(ws, row, first, last, label, fill=color, size=9)
            _merge(ws, row + 1, first, last, value if value is not None else 'N/D', fill=color, size=24).number_format = fmt
            _merge(ws, row + 2, first, last, subtitle, size=9)
            ws.row_dimensions[row].height = 24
            ws.row_dimensions[row + 1].height = 36
            ws.row_dimensions[row + 2].height = 25
        _merge(ws, 12, 1, 12, f"TEMPORADA COMPLETA · Registros: {s['total']} · Finalizados: {s['finished']} · Programados: {s['scheduled']} · En juego: {s['live']} · Administrativos: {s['excluded']}")
        ws.row_dimensions[12].height = 27
        _merge(ws, 14, 1, 12, 'TEMPORADA COMPLETA · FRECUENCIA Y RACHAS POR FECHA', fill=NAVY)
        ws.row_dimensions[14].height = 25
        for first, last, label in [(1, 2, 'Suceso'), (3, 4, 'Casos / base'), (5, 6, 'Un caso cada N PJ'),
                                    (7, 8, 'Rachas sin caso · TOP 3'), (9, 10, 'Fechas y jornadas del récord'), (11, 12, 'Racha al cierre')]:
            _merge(ws, 15, first, last, label, fill='1F4E78', size=9)
        ws.row_dimensions[15].height = 32
        for row, period in [(16, 'ht'), (17, 'ft')]:
            report = reports[period]
            top = report['top']
            _merge(ws, row, 1, 2, 'Ambos marcan · ' + period.upper(), bold=True)
            _merge(ws, row, 3, 4, f"{s[period + '_cases']} / {s[period + '_base']}")
            _merge(ws, row, 5, 6, s[period + '_every'] if s[period + '_every'] is not None else 'Sin casos').number_format = '0.00'
            _merge(ws, row, 7, 8, '\n'.join(('Mín. ' if item.get('lower_bound') else '') + str(item['count']) + ' PJ' for item in top) if top else 'Sin racha verificada')
            _merge(ws, row, 9, 10, _spell_label(top[0]) if top else 'Sin racha verificada')
            _merge(ws, row, 11, 12, _spell_label(report['current']))
            ws.row_dimensions[row].height = 58
        _merge(ws, 19, 1, 12, f"TEMPORADA COMPLETA · Gana local: {s['home_wins']} · Empate: {s['draws']} · Gana visitante: {s['away_wins']}", bold=True)
        _merge(ws, 20, 1, 12, f"COBERTURA · HT: {s['ht_base']} · FT: {s['ft_base']} · Córners: {s['corners_base']} · Partidos con algún dato pendiente: {s['unresolved']}")
        for row in (19, 20):
            ws.row_dimensions[row].height = 25
        _merge(ws, 22, 1, 12, 'DESGLOSE · CADA ESTADÍSTICA INDICA SU TORNEO Y FASE', fill=NAVY)
        ws.row_dimensions[22].height = 25
        _merge(ws, 23, 1, 3, 'Torneo / fase', fill='1F4E78')
        for col, label in enumerate(['PJ', 'Ambos HT', 'Ambos FT', 'Córners / PJ', 'Base córners', 'Goles / PJ HT', 'Goles / PJ FT', 'Un HT cada', 'Un FT cada'], 4):
            _cell(ws, 23, col, label, fill='1F4E78', color='FFFFFF', bold=True, size=9)
        ws.row_dimensions[23].height = 34
        groups = {}
        for game in games:
            groups.setdefault((game.get('scope') or 'all', game.get('phase') or ''), []).append(game)
        for row, ((scope, phase), records) in enumerate(groups.items(), 24):
            stats = summarize(records)['summary']
            _merge(ws, row, 1, 3, _context({'scope':scope, 'phase':phase}))
            values = [stats['finished'], stats['ht_rate'], stats['ft_rate'], stats['corners_average'], stats['corners_base'],
                      stats['goals_ht_average'], stats['goals_ft_average'], stats['ht_every'], stats['ft_every']]
            for col, value in enumerate(values, 4):
                cell = _cell(ws, row, col, value if value is not None else 'N/D')
                cell.number_format = '0.0%' if col in (5, 6) else '0' if col in (4, 8) else '0.00'
            ws.row_dimensions[row].height = 28
        note_row = max(33, 25 + len(groups))
        header_row = max(HEADER_ROW, note_row + 7)
        _merge(ws, note_row, 1, 12, 'Frecuencia = partidos con dato ÷ casos. Los datos pendientes interrumpen las rachas verificadas; «Mín.» señala un límite incierto. Los simultáneos se agrupan. Describe el historial, no garantiza cuándo ocurrirá el siguiente caso.', size=9)
        ws.row_dimensions[note_row].height = 32
        _merge(ws, header_row - 4, 1, 12, 'PARTIDOS · APERTURA Y CLAUSURA EN LA MISMA LISTA', fill=NAVY)
        ws.row_dimensions[header_row - 4].height = 25
        _merge(ws, header_row - 2, 1, 12, 'Verde: ambos marcaron en HT, desde el número hasta córners. N/D: dato pendiente; los resultados futuros quedan vacíos.', size=9)
        ws.row_dimensions[header_row - 2].height = 25
        for col, title in enumerate(HEADERS, 1):
            _cell(ws, header_row, col, title, fill='1F4E78', color='FFFFFF', bold=True)
        ws.row_dimensions[header_row].height = 28
        states = {'finished':'Finalizado', 'scheduled':'Programado', 'live':'En juego', 'postponed':'Aplazado', 'cancelled':'Cancelado'}
        for number, game in enumerate(games, 1):
            row = header_row + number
            missing = _missing(game)
            observation = game.get('administrative_reason') or ('Fuera del análisis: resultado administrativo.' if game.get('administrative') else 'Pendiente: ' + ', '.join(missing) if missing else '')
            calendar = 'Fecha pendiente' if game.get('date_confirmed') is False else game['date'] + (' ' + game['time'] if game.get('time') and game.get('time_confirmed', True) else ' · Hora pendiente')
            values = [number, calendar, game.get('round') if game.get('round') is not None else 'Fase final' if game.get('round_not_applicable') else 'Pendiente',
                      game['home'], game['away'], _score(game, 'ht'), _score(game, 'ft'), _corners(game),
                      game.get('scope') if game.get('scope') != 'all' else 'Temporada', game.get('phase') or '',
                      'Administrativo' if game.get('administrative') else states.get(game['status'], game['status']), observation]
            highlighted = game['status'] == 'finished' and not game.get('administrative') and btts(pair(game, 'ht'))
            for col, value in enumerate(values, 1):
                fill = 'C6EFCE' if highlighted and col <= 8 else 'F3F6FB' if number % 2 == 0 else None
                cell = _cell(ws, row, col, value, fill=fill)
                if col in (6, 7):
                    cell.number_format = '@'
            ws.row_dimensions[row].height = 25
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
            for field in ('source_url', 'round_source_url', 'ht_source_url', 'ht_verification_url', 'ft_verification_url', 'result_source_url', 'time_source_url', 'corners_source_url'):
                if game.get(field):
                    source_urls.add(game[field])
        ws.auto_filter.ref = f'A{header_row}:L{header_row + len(games)}'
        _team_table(ws, header_row + len(games) + 4, games)
        for col, width in enumerate([7, 26, 12, 27, 27, 10, 10, 12, 25, 27, 18, 48], 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
    ws = book.create_sheet('Notas')
    ws.freeze_panes = None
    ws.sheet_view.showGridLines = False
    for line in [
        'PARAGUAY · PRIMERA DIVISIÓN · Histórico de la aplicación desde 2018.',
        'Cada año conserva Apertura, Clausura y sus fases en una sola hoja; el desglose del resumen identifica cada torneo y fase.',
        'Los marcadores HT y FT son texto para conservar 1-1 sin convertirlo en fecha.',
        'N/D significa dato no encontrado, nunca cero. Los córners conocidos como cero se conservan como cero.',
        'Los futuros quedan sin resultados ni estadísticas de juego; los administrativos se conservan y se excluyen del análisis.',
        'Las medias usan solo partidos finalizados con el dato conocido. Los córners son el total de ambos equipos.',
        'Las rachas se calculan por fecha y hora de Colombia; los datos desconocidos interrumpen la continuidad y los simultáneos se agrupan.',
        'La racha al cierre corresponde al final de los datos de esa temporada. No predice el próximo partido.',
        'Se conserva el orden por jornada/listado original. Ninguna hoja inmoviliza filas ni columnas.',
        'Fuentes de los registros: ' + '; '.join(sorted(sources)),
    ]:
        ws.append([line])
    for url in sorted(source_urls):
        ws.append(['Fuente consultada', url])
    ws.append([])
    ws.append(['Año', 'Fecha COL', 'Partido', 'Dato pendiente / incidencia', 'ID', 'Fuente del registro'])
    for note in notes:
        ws.append(note)
    for row in ws:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = 's'
            cell.font = Font(name='Arial', size=10)
            cell.alignment = Alignment(vertical='center', wrap_text=True)
    for col, width in enumerate([20, 28, 55, 75, 48, 95], 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    filename = LEAGUES.get('paraguay', {}).get('history_file', 'Paraguay_Primera_2018_2026.xlsx')
    target = Path(output) if output else ROOT / 'data/history' / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    book.save(target)
    return target


if __name__ == '__main__':
    db.initialize()
    print(export_history())
