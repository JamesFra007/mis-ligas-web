from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import threading
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.drawing.image import Image
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils.units import pixels_to_EMU

from . import db
from .stats import summarize, btts, pair
from .order import order_games

ROOT = Path(__file__).resolve().parents[1]
AUTO_UPDATE = os.environ.get('LIGAS_AUTO_UPDATE', '0') == '1'
INTERVAL = max(5, int(os.environ.get('LIGAS_UPDATE_MINUTES', '30')))
SYNC_LOCK = threading.Lock()
LEAGUES = {'spain': {'name':'LaLiga','country':'España','cards':True}, 'colombia': {'name':'Liga BetPlay','country':'Colombia','cards':False}}


def current_season(league):
    today = datetime.now(ZoneInfo('America/Bogota'))
    year = today.year if today.month >= 7 else today.year - 1
    return f'{year}-{(year+1)%100:02d}' if league == 'spain' else str(today.year)


def check(league, season=None):
    if league not in LEAGUES:
        raise HTTPException(400, 'Liga no válida.')
    if season is not None:
        pattern = r'20\d{2}-\d{2}' if league == 'spain' else r'20\d{2}'
        if not re.fullmatch(pattern, season):
            raise HTTPException(400, 'Temporada no válida.')
        y = int(season[:4])
        if not 2018 <= y <= datetime.now(ZoneInfo('America/Bogota')).year + 1:
            raise HTTPException(400, 'Temporada fuera del intervalo disponible.')
        if league == 'spain' and season[-2:] != f'{(y+1)%100:02d}':
            raise HTTPException(400, 'La temporada debe contener dos años consecutivos.')


def perform_sync(league, season):
    check(league, season)
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, 'Ya hay una actualización en curso. Espera a que termine.')
    try:
        from .providers import sync_games, ProviderError
        try:
            incoming = sync_games(league, season)
            if not incoming:
                return {'added':0,'updated':0,'unchanged':0,'errors':[], 'message':'La fuente todavía no ofrece partidos para esta temporada. Los datos guardados se conservan.'}
            result = db.upsert(incoming)
            from .providers import get_coverage
            coverage=get_coverage(league)
            warnings=coverage.get('warnings',[])
            return {**result,'errors':warnings,'coverage':coverage, 'message':f"{result['added']} nuevos · {result['updated']} actualizados · {result['unchanged']} sin cambios. Los datos ausentes siguen pendientes."}
        except ProviderError as error:
            raise HTTPException(502, str(error)) from error
        except (OSError, ValueError) as error:
            raise HTTPException(502, 'No se pudo completar la actualización. Los datos anteriores se conservan.') from error
    finally:
        SYNC_LOCK.release()


async def scheduled_updates():
    while True:
        await asyncio.sleep(INTERVAL * 60)
        for league in LEAGUES:
            try:
                await asyncio.to_thread(perform_sync, league, current_season(league))
            except Exception:
                import logging
                logging.getLogger('ligas').exception('La actualización programada no se completó para %s', league)


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    task = asyncio.create_task(scheduled_updates()) if AUTO_UPDATE else None
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title='Mis Ligas', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/meta')
def metadata():
    seasons = db.all_seasons()
    leagues = []
    for key, info in LEAGUES.items():
        available = [{'id':s['season'],'label':s['season'].replace('-','/'),'count':s['count']} for s in seasons if s['league']==key]
        current = current_season(key)
        if current not in {s['id'] for s in available}:
            available.append({'id':current,'label':current.replace('-','/') + ' · por importar','count':0})
        leagues.append({'id':key, **info, 'seasons':available})
    last = db.setting('last_sync')
    return {'leagues':leagues, 'updated_at':last['at'] if last else db.setting('imported_at'), 'last_sync':last, 'auto_update':AUTO_UPDATE,'update_interval_minutes':INTERVAL,'timezone':'America/Bogota'}


def select_games(league, season, scope='all', team='all', condition='all', status='all', search='', order='round'):
    check(league, season)
    if condition not in ('all','home','away'):
        raise HTTPException(400, 'Condición no válida.')
    if status not in ('all','finished','scheduled','live','postponed','cancelled'):
        raise HTTPException(400, 'Estado no válido.')
    try:
        all_games = order_games(db.games(league,season),order)
    except ValueError as error:
        raise HTTPException(400,str(error)) from error
    teams = sorted({g[k] for g in all_games for k in ('home','away')})
    if league == 'spain':
        scopes = [{'id':'all','label':'Temporada completa'}, {'id':'first','label':'Primera vuelta'}, {'id':'second','label':'Segunda vuelta'}]
    else:
        values = sorted({g['scope'] for g in all_games if g.get('scope') and g['scope']!='all'},key=lambda x:('Finalización' in x,x))
        scopes = [{'id':'all','label':'Temporada completa'}]+[{'id':s,'label':s} for s in values]
    rows = all_games
    if scope != 'all':
        rows = [g for g in rows if g.get('scope') == scope]
    if team != 'all':
        rows = [g for g in rows if g['home']==team or g['away']==team]
        if condition != 'all':
            rows=[g for g in rows if g['home' if condition=='home' else 'away']==team]
    if status != 'all':
        rows=[g for g in rows if g['status']==status]
    if search.strip():
        value=search.casefold().strip()
        rows=[g for g in rows if value in (g['home']+' '+g['away']).casefold()]
    unknown_rounds = sum(g.get('round') is None for g in all_games) if league == 'spain' else 0
    scope_note = f'La fuente no informa la jornada de {unknown_rounds} partidos; esos encuentros solo aparecen en Temporada completa hasta poder confirmar su vuelta.' if unknown_rounds else None
    return rows, {'scopes':scopes,'teams':teams,'scope_note':scope_note}


@app.get('/api/dashboard')
def dashboard(league: str='spain',season: str='2025-26',scope: str='all',team: str='all',condition: str='all',status: str='all',search: str=Query('',max_length=100),page: int=Query(1,ge=1),page_size: int=Query(40,ge=1,le=200),order: str='round'):
    rows,options=select_games(league,season,scope,team,condition,status,search,order)
    total=len(rows);pages=max(1,math.ceil(total/page_size));page=min(page,pages)
    visible=[{**g,'btts_ht':g['status']=='finished' and not g.get('administrative') and btts(pair(g,'ht'))} for g in rows[(page-1)*page_size:page*page_size]]
    last=db.setting('last_sync')
    return {**summarize(rows,team),'options':options,'games':visible,'pagination':{'page':page,'page_size':page_size,'total':total,'pages':pages},'updated_at':last['at'] if last else db.setting('imported_at')}


@app.get('/api/forecast')
def forecast_view(league: str='spain'):
    check(league)
    from .forecast import forecast
    records=[]
    for season in db.all_seasons():
        if season['league']==league:
            records.extend(db.games(league,season['season']))
    result=forecast(records, records)
    last=db.setting('last_sync')
    result['source_updated_at']=last['at'] if last else db.setting('imported_at')
    return result


class SyncRequest(BaseModel):
    league: str
    season: str


@app.post('/api/sync')
def sync(request: SyncRequest):
    return perform_sync(request.league, request.season)


@app.get('/api/history.xlsx')
def history(league: str):
    check(league)
    name='LaLiga_RESUMEN_DINAMICO.xlsx' if league=='spain' else 'Liga_Colombiana_RESUMEN_DINAMICO.xlsx'
    file=ROOT/'data'/'history'/name
    if not file.exists():
        raise HTTPException(404, 'El Excel histórico no está disponible en esta instalación.')
    return FileResponse(file,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',filename=name)


def card_icon(ws, row, col, color):
    file=ROOT/'static'/f'card-{color}.png'
    if not file.exists():
        return
    image=Image(str(file));image.width=9;image.height=13
    image.anchor=OneCellAnchor(_from=AnchorMarker(col=col-1,row=row-1,colOff=pixels_to_EMU(28),rowOff=pixels_to_EMU(3)),ext=XDRPositiveSize2D(cx=pixels_to_EMU(9),cy=pixels_to_EMU(13)))
    ws.add_image(image)


@app.get('/api/export.xlsx')
def export(league: str='spain',season: str='2025-26',scope: str='all',team: str='all',condition: str='all',status: str='all',search: str=Query('',max_length=100),order: str='round'):
    rows,_=select_games(league,season,scope,team,condition,status,search,order)
    stats=summarize(rows,team)['summary'];w=Workbook();ws=w.active;ws.title='Partidos';ws.sheet_view.showGridLines=False
    ws.merge_cells('A1:J1');ws['A1']=f"{LEAGUES[league]['name']} · {season.replace('-','/')} · {scope if scope!='all' else 'Temporada completa'}"
    ws['A1'].font=Font(size=17,bold=True,color='FFFFFF');ws['A1'].fill=PatternFill('solid',fgColor='172554');ws.row_dimensions[1].height=30
    lines=[f"Partidos finalizados: {stats['finished']} | Ambos marcan HT: {stats['ht_cases']}/{stats['ht_base']} | Ambos marcan FT: {stats['ft_cases']}/{stats['ft_base']}", 'Frecuencia histórica; no predice el siguiente partido. Solo se analizan encuentros terminados y no administrativos.','Verde: ambos equipos marcaron en HT. N/D: dato pendiente. Celda vacía en rojas: cero.','Filtros: equipo '+('Todos' if team=='all' else team)+' · condición '+condition+' · estado '+status+(f' · búsqueda {search}' if search else '')]
    for i,line in enumerate(lines,2):
        ws.merge_cells(start_row=i,start_column=1,end_row=i,end_column=10);ws.cell(i,1,line);ws.cell(i,1).font=Font(size=10,color='475569');ws.cell(i,1).alignment=Alignment(wrap_text=True,vertical='center');ws.row_dimensions[i].height=27
    headers=['N.º','Fecha · hora (COL)','Jornada','Local','Visitante','HT','FT','Córners']
    if LEAGUES[league]['cards']:headers+=['Amarillas','Rojas']
    headers+=['Torneo / vuelta','Fase','Estado']
    row=7
    for c,name in enumerate(headers,1):
        cell=ws.cell(row,c,name);cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='1F4E78');cell.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
    ws.row_dimensions[row].height=27
    state_names={'finished':'Finalizado','scheduled':'Programado','live':'En juego','postponed':'Aplazado','cancelled':'Cancelado'}
    for i,game in enumerate(rows,1):
        row+=1
        score=lambda p:f"{game[p+'_home']}-{game[p+'_away']}" if game.get(p+'_home') is not None and game.get(p+'_away') is not None else 'N/D' if game['status']=='finished' else ''
        calendar='Fecha pendiente' if game.get('date_confirmed') is False else game['date']+(' '+game['time'] if game.get('time') and game.get('time_confirmed',True) else ' · Hora pendiente')
        values=[i,calendar,game.get('round'),game['home'],game['away'],score('ht'),score('ft'),game.get('corners') if game.get('corners') is not None else 'N/D' if game['status']=='finished' else '']
        if LEAGUES[league]['cards']:
            values += [game.get('yellow') if game.get('yellow') is not None else 'N/D' if game['status']=='finished' else '', None if game.get('red')==0 else game.get('red') if game.get('red') is not None else 'N/D' if game['status']=='finished' else '']
        values += [{'first':'Primera vuelta','second':'Segunda vuelta','all':'Temporada'}.get(game.get('scope'),game.get('scope')),game.get('phase'), 'Administrativo' if game.get('administrative') else state_names[game['status']]]
        for c,value in enumerate(values,1):
            cell=ws.cell(row,c,value);cell.font=Font(name='Arial',size=10);cell.alignment=Alignment(horizontal='center',vertical='center')
            # Treat provider text as plain values, never spreadsheet formulas.
            if isinstance(value,str):cell.data_type='s'
            if c<=8 and game['status']=='finished' and not game.get('administrative') and btts(pair(game,'ht')):
                cell.fill=PatternFill('solid',fgColor='C6EFCE')
        ws.row_dimensions[row].height=18
        if LEAGUES[league]['cards']:
            if game.get('yellow') is not None:card_icon(ws,row,9,'yellow')
            if game.get('red',0) is not None and game.get('red',0)>0:card_icon(ws,row,10,'red')
    for key,width in {'A':7,'B':15,'C':11,'D':28,'E':28,'F':10,'G':10,'H':13,'I':14,'J':13,'K':25,'L':22,'M':18}.items():ws.column_dimensions[key].width=width
    ws.freeze_panes=None
    notes=w.create_sheet('Notas')
    for line in ['Exportación de la vista filtrada de Mis Ligas.','El Excel histórico original conserva sus paneles interactivos y puede descargarse por separado.','NULL / N/D significa dato no encontrado; nunca se sustituye por cero.','Los partidos administrativos se conservan, pero se excluyen del análisis.','La tabla sigue el orden seleccionado: '+('fecha y hora confirmadas; los horarios pendientes se indican.' if order=='chronological' else 'jornada / listado original; los partidos nuevos se añaden al final.')]:
        notes.append([line])
    notes.column_dimensions['A'].width=110;notes.freeze_panes=None
    output=BytesIO();w.save(output);output.seek(0)
    return StreamingResponse(output,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="MisLigas_{league}_{season}.xlsx"'})
