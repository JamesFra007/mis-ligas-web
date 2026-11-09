"""Export HT/FT-only league histories without corner or card columns."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import sys

from openpyxl import Workbook
from openpyxl.styles import Alignment,Font,PatternFill
from openpyxl.utils import get_column_letter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app import db
from app.leagues import LEAGUES
from app.stats import summarize,known,pair,btts

NAVY='172554';GREEN='059669';TEAL='0F766E';PURPLE='7C3AED';BLUE='2563EB';PINK='DB2777';ORANGE='EA580C'


def export_history(output=None, league='hungary'):
    seasons=[s for s in db.all_seasons() if s['league']==league]
    if not seasons:
        return None
    w=Workbook();w.remove(w.active);notes=[]
    def merge(ws,r,c1,c2,value,color=None,big=False):
        ws.merge_cells(start_row=r,start_column=c1,end_row=r,end_column=c2)
        cell=ws.cell(r,c1,value);cell.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
        cell.font=Font(name='Arial',size=23 if big else 10,bold=bool(color),color='FFFFFF' if color else '334155')
        if color:
            for rr in ws.iter_rows(min_row=r,max_row=r,min_col=c1,max_col=c2):
                for x in rr:x.fill=PatternFill('solid',fgColor=color)
        return cell
    for season in seasons:
        key=season['season'];games=db.games(league,key)
        result=summarize(games);s=result['summary'];ws=w.create_sheet(('NB I ' if league=='hungary' else 'EKSTRA ')+key[2:])
        ws.sheet_view.showGridLines=False;ws.freeze_panes=None
        merge(ws,1,1,9,LEAGUES[league]['country'].upper()+' · '+LEAGUES[league]['name'].upper()+' · '+key.replace('-','/'),NAVY);ws.row_dimensions[1].height=32
        merge(ws,2,1,9,'Orden por jornada/listado original. Fecha y hora de Colombia. Verde: ambos equipos marcaron en HT.');ws.row_dimensions[2].height=25
        tiles=[(4,1,3,'AMBOS MARCAN · HT',s['ht_rate'],'0.0%',GREEN),(4,4,6,'UN CASO HT CADA',s['ht_every'],'0.00',TEAL),(4,7,9,'AMBOS MARCAN · FT',s['ft_rate'],'0.0%',PURPLE),(9,1,3,'UN CASO FT CADA',s['ft_every'],'0.00',PINK),(9,4,6,'GOLES / PARTIDO · HT',s['goals_ht_average'],'0.00',BLUE),(9,7,9,'GOLES / PARTIDO · FT',s['goals_ft_average'],'0.00',ORANGE)]
        for row,c1,c2,label,value,fmt,color in tiles:
            merge(ws,row,c1,c2,label,color);cell=merge(ws,row+1,c1,c2,value if value is not None else 'Sin casos',color,True);cell.number_format=fmt
            ws.row_dimensions[row].height=24;ws.row_dimensions[row+1].height=35
        merge(ws,14,1,9,f"Registros: {s['total']} · Finalizados jugados: {s['finished']} · HT conocidos: {s['ht_base']} · Administrativos: {s['excluded']} · Programados: {s['scheduled']}");ws.row_dimensions[14].height=27
        merge(ws,16,1,9,'FRECUENCIA HISTÓRICA · BASE DEL LISTADO DE LA TEMPORADA',NAVY);ws.row_dimensions[16].height=24
        heads=['Momento','Casos','Base PJ','1 cada N','Máxima racha sin caso','Racha al cierre','Distancia media']
        # Three text columns reserve readable width while retaining nine match columns.
        merge(ws,17,1,3,heads[0]);
        for col,label in enumerate(heads[1:],4):
            cell=ws.cell(17,col,label);cell.font=Font(name='Arial',size=9,bold=True,color=NAVY);cell.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
        ws.row_dimensions[17].height=35
        for row,period in [(18,'ht'),(19,'ft')]:
            merge(ws,row,1,3,period.upper());f=result['frequency'][period]
            values=[s[period+'_cases'],s[period+'_base'],s[period+'_every'],f['max_without'],f['current_without'],f['mean_gap']]
            for col,value in enumerate(values,4):
                c=ws.cell(row,col,value if value is not None else '—');c.alignment=Alignment(horizontal='center',vertical='center');c.font=Font(name='Arial',size=10);c.number_format='0.00' if col in (6,9) else '0'
            ws.row_dimensions[row].height=24
        merge(ws,21,1,9,'Frecuencia = partidos con dato ÷ casos. Describe el historial, no garantiza cuándo ocurrirá el siguiente caso.');ws.row_dimensions[21].height=27
        headers=['N.º','Fecha · hora COL','Jornada','Local','Visitante','HT','FT','Estado','Observación']
        for col,label in enumerate(headers,1):
            c=ws.cell(23,col,label);c.fill=PatternFill('solid',fgColor='1F4E78');c.font=Font(name='Arial',size=10,bold=True,color='FFFFFF');c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
        ws.row_dimensions[23].height=27
        for number,g in enumerate(games,1):
            row=23+number
            calendar='Fecha pendiente' if g.get('date_confirmed') is False else g['date']+(' '+g['time'] if g.get('time') and g.get('time_confirmed',True) else ' · Hora pendiente')
            score=lambda p:'-'.join(map(str,pair(g,p))) if known(pair(g,p)) else 'N/D' if g['status']=='finished' and not g.get('administrative') else ''
            observation=g.get('administrative_reason') or ('Fuera del análisis: resultado administrativo.' if g.get('administrative') else 'HT pendiente' if g['status']=='finished' and not known(pair(g,'ht')) else g.get('phase','') if league=='poland' and g.get('phase') not in (None,'Ekstraklasa') else '')
            state='Administrativo' if g.get('administrative') else {'finished':'Finalizado','scheduled':'Programado','live':'En juego','postponed':'Aplazado','cancelled':'Cancelado'}.get(g['status'],g['status'])
            values=[number,calendar,g.get('round'),g['home'],g['away'],score('ht'),score('ft'),state,observation]
            highlight=g['status']=='finished' and not g.get('administrative') and btts(pair(g,'ht'))
            for col,value in enumerate(values,1):
                c=ws.cell(row,col,value);c.font=Font(name='Arial',size=10);c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=col==9)
                if isinstance(value,str):c.data_type='s'
                if col in (6,7):c.number_format='@'
                if highlight and col<=7:c.fill=PatternFill('solid',fgColor='C6EFCE')
                elif number%2==0:c.fill=PatternFill('solid',fgColor='F3F6FB')
            ws.row_dimensions[row].height=25
            if g['status']=='finished' and not g.get('administrative') and not known(pair(g,'ht')):
                notes.append([key,g['date'],g['home']+' vs '+g['away'],'HT pendiente',g['id']])
            if g.get('administrative'):
                notes.append([key,g['date'],g['home']+' vs '+g['away'],observation,g['id']])
        widths=[7,26,12,28,28,11,11,18,45]
        for col,width in enumerate(widths,1):ws.column_dimensions[get_column_letter(col)].width=width
        ws.sheet_properties.pageSetUpPr.fitToPage=True;ws.page_setup.orientation='landscape';ws.page_setup.paperSize=ws.PAPERSIZE_A3;ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0
    n=w.create_sheet('Notas');n.freeze_panes=None;n.sheet_view.showGridLines=False
    sources=sorted({g.get('source','') for season in seasons for g in db.games(league,season['season']) if g.get('source')})
    n.append(['Fuentes de los registros: '+ '; '.join(sources)])
    n.append([LEAGUES[league]['country']+' · '+LEAGUES[league]['name']+' desde 2018/19. Resultados HT y FT; datos ausentes se indican como N/D.'])
    n.append(['Administrativos: se conservan en la lista y se excluyen de estadísticas de partidos jugados.'])
    n.append(['Los partidos futuros y horarios pendientes no se convierten en resultados 0-0.'])
    n.append(['Este histórico se genera a partir de los datos guardados de la aplicación.'])
    n.append([]);n.append(['Temporada','Fecha COL','Partido','Observación','ID'])
    for row in notes:n.append(row)
    for col,width in enumerate([24,20,55,65,35],1):n.column_dimensions[get_column_letter(col)].width=width
    for row in n:
        for c in row:
            if isinstance(c.value,str):c.data_type='s'
            c.font=Font(name='Arial',size=10);c.alignment=Alignment(vertical='center',wrap_text=True)
    target=Path(output) if output else ROOT/'data/history'/LEAGUES[league]['history_file']
    target.parent.mkdir(parents=True,exist_ok=True);w.save(target)
    return target


if __name__=='__main__':
    db.initialize();print(export_history())
