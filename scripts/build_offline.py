"""Generate a portable standalone page or a scheduled static hosting artifact."""
from pathlib import Path
import base64
from datetime import datetime
import json
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.main import metadata,forecast_view
from app import db


def safe_json(data):
    return json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')


def build(output=None, hosted=False):
    db.initialize()
    meta=metadata()
    meta.update(mode='hosted_snapshot' if hosted else 'offline',offline=not hosted,auto_update=hosted,update_interval_minutes=30)
    stamp=datetime.fromisoformat(meta['updated_at']).astimezone(ZoneInfo('America/Bogota'))
    months=['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
    snapshot_date=f'{stamp.day} de {months[stamp.month-1]} de {stamp.year}'
    games=[];forecasts={}
    for league in meta['leagues']:
        for season in league['seasons']:
            games.extend(db.games(league['id'],season['id']))
        forecasts[league['id']]=forecast_view(league['id'])
    books={}
    for key,name in [('spain','LaLiga_RESUMEN_DINAMICO.xlsx'),('colombia','Liga_Colombiana_RESUMEN_DINAMICO.xlsx')]:
        books[key]={'name':name,'base64':base64.b64encode((ROOT/'data/history'/name).read_bytes()).decode()}
    bundle={'meta':meta,'games':games,'workbooks':books,'forecasts':forecasts}
    html=(ROOT/'static/index.html').read_text()
    css=(ROOT/'static/styles.css').read_text()
    code=(ROOT/'static/app.js').read_text()
    code=code.replace("history.replaceState(null, '', '?' + query());", "try { history.replaceState(null, '', '?' + query()); } catch (_) { /* Local file viewers can restrict URL changes. */ }")
    html=html.replace('<link rel="stylesheet" href="/static/styles.css">','<style>'+css+'\n.offline-banner{padding:12px 18px;margin:0 0 20px;background:#fff5da;border:1px solid #efd88b;border-radius:10px;color:#765a17;font-size:12px;line-height:1.6}.offline-banner strong{display:block;font-size:13px}.button:disabled{cursor:default;opacity:.6}</style>')
    html=html.replace('  <script src="/static/app.js" defer></script>','')
    html=html.replace(' Descargar Excel</a>',' Exportar CSV (Excel)</a>')
    if not hosted:
        banner=f'<div class="offline-banner" role="note"><strong>Copia para abrir sin conexión</strong>Datos guardados el {snapshot_date}. Puedes usar los filtros y descargar los Excel originales. Esta copia conserva su calendario y estimaciones de esa captura; no incorpora partidos nuevos automáticamente.</div>'
        html=html.replace('<main id="main" tabindex="-1">','<main id="main" tabindex="-1">'+banner)
    inline='<script id="offline-bundle" type="application/json">'+safe_json(bundle)+'</script>\n<script>'+(ROOT/'static/offline.js').read_text()+'</script>\n<script>'+code+'</script>\n'
    assert '</script' not in code.lower()
    html=html.replace('</body>',inline+'</body>')
    target=Path(output) if output else ROOT.parent/'Mis_Ligas_Abrir.html'
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(html)
    print(json.dumps({'output':str(target),'games':len(games),'bytes':target.stat().st_size,'mode':meta['mode'],'forecast':{key:value['status'] for key,value in forecasts.items()}},indent=2))
    return target


if __name__=='__main__':build()
