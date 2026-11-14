"""Read independently published Paraguayan results and complete goal logs."""
from html import unescape
import re

from app.paraguay_names import normalize_team


def club(name):
    aliases={'Independiente CG':'Independiente FBC',
             'Independiente (CG)':'Independiente FBC',
             'Gral. Caballero JLM':'General Caballero JLM'}
    return normalize_team(aliases.get(name,name))


def score_line(line):
    if not line or line[0].isspace() or re.match(r'^\d+\.',line):return None
    raw=line.split('[',1)[0].rstrip()
    match=re.match(r'^(.+?)\s+(\d{1,2})-(\d{1,2})\s+(.+?)\s*$',raw)
    if match:return club(match[1]),club(match[4]),int(match[2]),int(match[3]),False
    awarded=re.match(r'^(.+?)\s+awd\s+(.+?)\s*$',raw)
    result=re.search(r'awarded\s+(\d+)-(\d+)',line)
    if awarded and result:return club(awarded[1]),club(awarded[2]),int(result[1]),int(result[2]),True
    return None


def derive_half(record,following):
    if record['administrative']:return None,'administrative'
    home,away=record['ft_home'],record['ft_away']
    if home==away==0:return [0,0],'deduced_from_verified_nil_nil_final'
    for index,line in enumerate(following):
        if not re.match(r'^\s{2}\[',line):continue
        text=line.strip()[1:]
        for more in following[index+1:]:
            if ']' in text:break
            if more.startswith('   '):text+=' '+more.strip()
            else:break
        if ']' not in text:return None,'incomplete_goal_log'
        text=text.split(']',1)[0]
        if '?' in text:return None,'unknown_goal_minute'
        parts=text.split(';')
        if len(parts)==2:left,right=parts
        elif len(parts)==1 and home==0:left,right='',parts[0]
        elif len(parts)==1 and away==0:left,right=parts[0],''
        else:return None,'ambiguous_goal_sides'
        minute=re.compile(r'(?<![A-Za-z0-9])(\d{1,3})(?:\+(\d{1,2}))?(?:pen|og)?(?![A-Za-z0-9])')
        hs,aws=minute.findall(left),minute.findall(right)
        if len(hs)!=home or len(aws)!=away:return None,'goal_tally_mismatch'
        if any(not 0<int(value[0])<=120 for value in hs+aws):return None,'invalid_goal_minute'
        if any(46<=int(value[0])<=49 and not value[1] for value in hs+aws):
            return None,'unqualified_boundary_minute'
        return [sum(int(value[0])<=45 for value in hs),sum(int(value[0])<=45 for value in aws)],'complete_goal_log'
    return None,'missing_goal_log'


def parse_results(html,year):
    if isinstance(html,bytes):
        try:html=html.decode('utf-8')
        except UnicodeDecodeError:html=html.decode('latin-1')
    # Keep the archive's indentation: it distinguishes goals from score lines.
    text=unescape(re.sub(r'<[^>]+>','',html))
    lines=text.splitlines();records=[]
    scope=None;phase='Fase regular';rnd=None;active=False
    for index,line in enumerate(lines):
        tournament=re.fullmatch(r'Torneo (Apertura|Clausura) '+str(year),line)
        if tournament:scope=tournament[1];phase='Fase regular';rnd=None;active=False;continue
        if scope and re.match(r'^Copa Paraguay(?: \d+)?$',line):break
        if scope and line=='Promotion/Relegation Playoff':
            scope='Promoción';phase='Promoción';rnd=None;active=True;continue
        if not scope:continue
        number=re.match(r'^Round (\d+)',line)
        if number:rnd=int(number[1]);phase='Fase regular';active=True;continue
        if line.startswith('Quarterfinals'):phase='Cuartos de final';rnd=None;active=True;continue
        if line.startswith('Semifinals'):phase='Semifinal';rnd=None;active=True;continue
        if line.startswith('Final ['):phase='Final';rnd=None;active=True;continue
        if line in ('Final Table:','Final Table','Relegation Table:'):active=False
        observed=score_line(line) if active else None
        if observed:
            home,away,fh,fa,admin=observed
            record={'season':str(year),'scope':scope,'phase':phase,'round':rnd,
                    'home':home,'away':away,'ft_home':fh,'ft_away':fa,'administrative':admin,
                    'source_url':f'https://www.rsssf.org/tablesp/para{year}.html'}
            stop=next((n for n in range(index+1,len(lines)) if score_line(lines[n]) or re.match(r'^Round |^Torneo |^Copa |^\[',lines[n])),min(len(lines),index+7))
            ht,method=derive_half(record,lines[index+1:stop])
            record.update(ht_from_goal_minutes=ht,ht_method=method)
            records.append(record)
    return records
