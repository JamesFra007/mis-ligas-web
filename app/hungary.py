"""NB I results and fixtures from the Hungarian federation's public match centre.

Only HT/FT and the published calendar are collected. All corner/card fields stay
None. The official IDs identify fixtures even when their dates or rounds move.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import gzip
from html import unescape
from html.parser import HTMLParser
import logging
import re
from zoneinfo import ZoneInfo

from .providers import ProviderError, _fetch

BASE='https://adatbank.mlsz.hu'
BUDAPEST=ZoneInfo('Europe/Budapest')
BOGOTA=ZoneInfo('America/Bogota')
LOGGER=logging.getLogger(__name__)
MAX_REPORTS=40
# Corroborated as an awarded 3-0 in BetExplorer's archive. The federation's
# report displays a template (0-0) half-time for this unplayed fixture.
ADMINISTRATIVE_REPORTS={'1544830':'Resultado administrativo 3-0; partido no disputado (AWA).'}
_COVERAGE={}
# IDs are published in the federation's season and league selectors.
CONTEXTS={2018:(49,18936),2019:(52,20850),2020:(54,22649),2021:(56,23704),2022:(59,25412),2023:(61,27254),2024:(63,29213),2025:(65,31362),2026:(67,33586)}
NAMES={
 'FERENCVÁROSI TC':'Ferencváros','ÚJPEST FC':'Újpest','DVSC':'Debrecen','DEBRECENI VSC':'Debrecen',
 'ETO FC':'Győr','ETO FC GYŐR':'Győr','GYŐRI ETO FC':'Győr','MTK BUDAPEST':'MTK Budapest',
 'ZTE FC':'Zalaegerszeg','ZALAEGERSZEGI TE FC':'Zalaegerszeg','PAKSI FC':'Paks',
 'PUSKÁS AKADÉMIA FC':'Puskás Akadémia','KISVÁRDA MASTER GOOD':'Kisvárda',
 'KISPEST–HONVÉD FC':'Honvéd','KISPEST-HONVÉD FC':'Honvéd','BUDAPEST HONVÉD':'Honvéd','BUDAPEST HONVÉD FC':'Honvéd',
 'VASAS FC':'Vasas','NYÍREGYHÁZA SPARTACUS FC':'Nyíregyháza','DVTK':'Diósgyőr','DIÓSGYŐRI VTK':'Diósgyőr',
 'MOL FEHÉRVÁR FC':'Fehérvár','FEHÉRVÁR FC':'Fehérvár','MOL VIDI FC':'Fehérvár','VIDEOTON FC':'Fehérvár',
 'MEZŐKÖVESD ZSÓRY FC':'Mezőkövesd','MEZŐKÖVESD ZSÓRY':'Mezőkövesd','KECSKEMÉTI TE':'Kecskemét',
 'SOROKSÁR SC':'Soroksár','SZOMBATHELYI HALADÁS':'Haladás','BALMAZ KAMILLA GYÓGYFÜRDŐ':'Balmazújváros',
 'KAPOSVÁRI RÁKÓCZI FC':'Kaposvár','BUDAFOKI MTE':'Budafok','GYIRMÓT FC GYŐR':'Gyirmót',
 'KOLORCITY KAZINCBARCIKA SC':'Kazincbarcika','KAZINCBARCIKAI SC':'Kazincbarcika',
}
VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

class Node:
 def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
 def text(self):return ' '.join(' '.join(c.text() if isinstance(c,Node) else c for c in self.children).split())
 def descendants(self):
  for child in self.children:
   if isinstance(child,Node):yield child;yield from child.descendants()
 def find(self,*,cls=None,ident=None,tag=None):
  return next((n for n in self.descendants() if (tag is None or n.tag==tag) and (ident is None or n.attrs.get('id')==ident) and (cls is None or cls in (n.attrs.get('class') or '').split())),None)

class Document(HTMLParser):
 def __init__(self,html):
  super().__init__(convert_charrefs=True);self.root=Node('root');self.stack=[self.root];self.feed(html)
 def handle_starttag(self,tag,attrs):
  node=Node(tag,attrs);self.stack[-1].children.append(node)
  if tag not in VOID:self.stack.append(node)
 def handle_startendtag(self,tag,attrs):self.stack[-1].children.append(Node(tag,attrs))
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==tag:del self.stack[i:];break
 def handle_data(self,data):self.stack[-1].children.append(data)


def _html(url):
 raw=_fetch(url)
 if raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
 try:return raw.decode('utf-8-sig')
 except UnicodeDecodeError:
  try:return raw.decode('cp1250')
  except UnicodeDecodeError as error:raise ProviderError('El calendario húngaro devolvió un texto que no se puede interpretar.') from error


def _season(season):
 match=re.fullmatch(r'(20\d\d)-(\d\d)',str(season))
 if not match or int(match[2])!=(int(match[1])+1)%100:raise ProviderError('La temporada húngara debe tener el formato 2026-27.')
 return int(match[1])


def _score(text):
 match=re.fullmatch(r'\s*\(?\s*(\d{1,2})\s*[-–:]\s*(\d{1,2})\s*\)?\s*',text or '')
 return tuple(map(int,match.groups())) if match else None


def _name(text):
 text=unescape(' '.join(text.split())).replace('\u00a0',' ')
 return NAMES.get(text.upper(),text)


def _clock(text,finished=False):
 match=re.search(r'(20\d\d)\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?',text or '')
 if not match:return None
 try:day=date(*map(int,match.groups()))
 except ValueError:return None
 hour=re.search(r'(\d{1,2}):(\d{2})',text[match.end():])
 if not hour:return {'date':day.isoformat(),'time':None,'kickoff':None,'time_confirmed':False,'date_confirmed':bool(finished)}
 try:stamp=datetime(day.year,day.month,day.day,int(hour[1]),int(hour[2]),tzinfo=BUDAPEST)
 except ValueError:return None
 local=stamp.astimezone(BOGOTA)
 return {'date':local.date().isoformat(),'time':local.strftime('%H:%M'),'kickoff':stamp.astimezone(timezone.utc).isoformat(),'time_confirmed':True,'date_confirmed':True}


def parse_round(html,season,now=None):
 """Parse the main round's published fixtures, excluding related team widgets."""
 year=_season(season);now=now or datetime.now(timezone.utc)
 if now.tzinfo is None:now=now.replace(tzinfo=BOGOTA)
 doc=Document(html).root;panel=doc.find(ident='match_panel')
 if panel is None:raise ProviderError('No se encontró el calendario oficial de la NB I.')
 rows=[]
 for block in panel.descendants():
  if 'schedule' not in (block.attrs.get('class') or '').split():continue
  home=block.find(cls='home_team');away=block.find(cls='away_team');dt=block.find(cls='team_sorsolas_date')
  links=[n.attrs.get('href','') for n in block.descendants() if n.tag=='a']
  match=next((m for link in links if (m:=re.search(r'/match/(\d+)/0/(\d+)/(\d+)/(\d+)\.html',link))),None)
  if home is None or away is None or dt is None:continue
  def club_path(node):
   return next((m for n in node.descendants() if n.tag=='a' and (m:=re.search(r'/club/(\d+)/0/(\d+)/(\d+)/(\d+)\.html',n.attrs.get('href','')))),None)
  home_path,away_path=club_path(home),club_path(away)
  context=match or home_path
  if context is None:continue
  season_id,league_id,round_number=context[1],context[2],int(context[3])
  home_club_id=home_path[4] if home_path else None
  away_club_id=away_path[4] if away_path else None
  fixture_suffix=f'{season_id}:{league_id}:{round_number}:{home_club_id}:{away_club_id}' if home_club_id and away_club_id else None
  if match is None and fixture_suffix is None:continue
  identity='hungary:mlsz:'+match[4] if match else 'hungary:fixture:'+fixture_suffix
  provider_id='mlsz:'+match[4] if match else 'fixture:'+fixture_suffix
  source_url=f'{BASE}/match/{season_id}/0/{league_id}/{round_number}/{match[4]}.html' if match else f'{BASE}/league/{season_id}/0/{league_id}/{round_number}.html'
  score_node=block.find(cls='schedule-points');ft=_score(score_node.text() if score_node else '')
  clock=_clock(dt.text(),finished=ft is not None)
  if clock is None:continue
  if not date(year,7,1)<=date.fromisoformat(clock['date'])<date(year+1,7,1):continue
  stamp=datetime.fromisoformat(clock['kickoff']) if clock['kickoff'] else None
  text=block.text().lower()
  status='finished' if ft is not None else 'scheduled'
  admin='félbeszakadt' in text or 'felbeszakadt' in text or 'játék nélkül' in text or 'jatek nelkul' in text
  administrative_reason=ADMINISTRATIVE_REPORTS.get(match[4]) if match else None
  admin=admin or administrative_reason is not None
  if 'félbeszakadt' in text or 'felbeszakadt' in text:status='cancelled'
  elif 'halaszt' in text:status='postponed'
  elif 'töröl' in text or 'torol' in text:status='cancelled'
  elif stamp is not None and stamp>now:status='scheduled'
  elif ft is not None and stamp is not None and now-stamp<timedelta(hours=2):status='live'
  if status!='finished':ft=None
  game={'id':identity,'provider_id':provider_id,'mlsz_id':match[4] if match else None,'fixture_key':'mlsz:'+fixture_suffix if fixture_suffix else None,'home_club_id':home_club_id,'away_club_id':away_club_id,
        'league':'hungary','season':str(season),'season_label':f'{year}/{(year+1)%100:02d}',
        **clock,'home':_name(home.text()),'away':_name(away.text()),'round':round_number,'scope':'all','phase':'NB I','status':status,
        'ht_home':None,'ht_away':None,'ft_home':ft[0] if ft else None,'ft_away':ft[1] if ft else None,
        'corners':None,'yellow':None,'red':None,'yellow_home':None,'yellow_away':None,'red_home':None,'red_away':None,
        'administrative':admin,'administrative_reason':administrative_reason or ('Partido abandonado o resuelto sin jugar según la fuente oficial.' if admin else None),'source':'MLSZ · NB I','source_url':source_url}
  rows.append(game)
 if not rows:raise ProviderError('El calendario oficial no contiene partidos válidos de la temporada solicitada.')
 return rows


def enrich_match(game,html):
 """Add only the report's explicit FT and half-time, never derive missing HT."""
 result=dict(game);doc=Document(html).root;score=doc.find(cls='match-result')
 if score is None:return result
 ft_node=score.find(tag='span');ht_node=score.find(tag='p')
 ft=_score(ft_node.text() if ft_node else '');ht=_score(ht_node.text() if ht_node else '')
 if result.get('administrative') or str(result.get('mlsz_id','')) in ADMINISTRATIVE_REPORTS:
  result['administrative']=True
  result['administrative_reason']=ADMINISTRATIVE_REPORTS.get(str(result.get('mlsz_id','')),result.get('administrative_reason'))
  result['ht_home']=result['ht_away']=None
  return result
 if result.get('status')!='finished':return result
 if ft is not None:result['ft_home'],result['ft_away']=ft
 if ht is not None and ft is not None and all(h<=f for h,f in zip(ht,ft)):result['ht_home'],result['ht_away']=ht
 return result


def season_context(season):
 year=_season(season)
 context=CONTEXTS.get(year)
 if context is None:
  root=_html(BASE+'/')
  match=re.search(r'href=["\']https?://[^/]+/league/(\d+)/0/(\d+)/(\d+)\.html["\'][^>]*title=["\'](?:OTP Bank Liga|Fizz Liga|NB I)',root)
  if not match:raise ProviderError('La federación todavía no publica esa temporada de la NB I.')
  page=_html(f'{BASE}/league/{match[1]}/0/{match[2]}/1.html');doc=Document(page).root;selector=doc.find(ident='evad')
  option=next((n for n in selector.descendants() if n.tag=='option' and n.text()==f'{year}/{year+1}'),None) if selector else None
  if option is None:raise ProviderError('La federación todavía no publica esa temporada de la NB I.')
  season_id=int(option.attrs['value']);page=_html(f'{BASE}/league/{season_id}/0/0/1.html');doc=Document(page).root
  selector=doc.find(ident='leagues');option=next((n for n in selector.descendants() if n.tag=='option' and n.text() in ('OTP Bank Liga','Fizz Liga','NB I')),None) if selector else None
  if option is None:raise ProviderError('No se encontró la primera división húngara para esa temporada.')
  context=(season_id,int(option.attrs['value']))
 season_id,league_id=context
 return {'season_id':season_id,'league_id':league_id,'rounds':33}


def get_coverage():return dict(_COVERAGE)


def sync_games(season):
 context=season_context(season);year=_season(season);_COVERAGE.clear()
 games={};failures=[]
 def load_round(number):
  url=f"{BASE}/league/{context['season_id']}/0/{context['league_id']}/{number}.html"
  return parse_round(_html(url),season)
 with ThreadPoolExecutor(max_workers=4) as pool:
  jobs={pool.submit(load_round,n):n for n in range(1,context['rounds']+1)}
  for future in as_completed(jobs):
   try:
    for game in future.result():games[game['id']]=game
   except ProviderError as error:failures.append(f"Jornada {jobs[future]}: {error}")
 if failures:
  _COVERAGE.update(fixtures_available=False,warnings=failures)
  raise ProviderError('No se pudo consultar todo el calendario húngaro; se conservan los datos guardados.')
 rows=sorted(games.values(),key=lambda g:(g['round'],g['date'],g['time'] or '',g['id']))
 if context['rounds']==33 and year in CONTEXTS and len(rows)<198:
  _COVERAGE.update(fixtures_available=False,warnings=['La fuente no devolvió los 198 encuentros del calendario.'])
  raise ProviderError('El calendario húngaro está incompleto; se conservan los datos guardados.')
 recent=sorted((g for g in rows if g['status']=='finished'),key=lambda g:(g['date'],g['time'] or ''),reverse=True)[:MAX_REPORTS]
 warnings=[]
 def enrich(game):return enrich_match(game,_html(game['source_url']))
 with ThreadPoolExecutor(max_workers=4) as pool:
  jobs={pool.submit(enrich,g):g for g in recent}
  for future in as_completed(jobs):
   try:games[jobs[future]['id']]=future.result()
   except ProviderError as error:warnings.append(f"HT pendiente del partido {jobs[future]['mlsz_id']}: {error}")
 rows=[games[g['id']] for g in rows]
 now=datetime.now(timezone.utc)
 _COVERAGE.update(fixtures_available=True,scheduled=sum(g['status']=='scheduled' for g in rows),confirmed_upcoming=sum(g['status']=='scheduled' and g['time_confirmed'] and datetime.fromisoformat(g['kickoff'])>now for g in rows),warnings=warnings,source='MLSZ',matches=len(rows),missing_ht=sum(g['status']=='finished' and g['ht_home'] is None for g in rows))
 return rows
