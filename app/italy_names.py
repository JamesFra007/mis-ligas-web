"""Stable Serie A club names shared by the CSV archive and live calendar."""
import re
import unicodedata


def _key(value):
    value=unicodedata.normalize('NFKD',str(value or ''))
    return re.sub(r'[^a-z0-9]+','',value.encode('ascii','ignore').decode().lower())


GROUPS={
    'Milan':['AC Milan','A.C. Milan'],
    'Inter':['Internazionale','Inter Milan','FC Internazionale Milano','FC Internazionale'],
    'Juventus':['Juventus FC'], 'Napoli':['SSC Napoli','S.S.C. Napoli'],
    'Roma':['AS Roma','A.S. Roma'], 'Lazio':['SS Lazio','S.S. Lazio'],
    'Atalanta':['Atalanta BC'], 'Bologna':['Bologna FC 1909'],
    'Fiorentina':['ACF Fiorentina'], 'Genoa':['Genoa CFC'],
    'Torino':['Torino FC'], 'Udinese':['Udinese Calcio'],
    'Cagliari':['Cagliari Calcio'], 'Parma':['Parma Calcio 1913','Parma Calcio'],
    'Verona':['Hellas Verona','Hellas Verona FC'], 'Chievo':['Chievo Verona','AC ChievoVerona'],
    'Sampdoria':['UC Sampdoria','U.C. Sampdoria'], 'Sassuolo':['US Sassuolo','Sassuolo Calcio'],
    'Empoli':['Empoli FC'], 'Frosinone':['Frosinone Calcio'], 'Lecce':['US Lecce'],
    'Brescia':['Brescia Calcio'], 'Spal':['SPAL','S.P.A.L.','SPAL Ferrara'],
    'Spezia':['Spezia Calcio'], 'Salernitana':['US Salernitana 1919'],
    'Monza':['AC Monza'], 'Cremonese':['US Cremonese'], 'Como':['Como 1907'],
    'Venezia':['Venezia FC'], 'Pisa':['Pisa SC','Pisa Sporting Club'],
    'Palermo':['Palermo FC'], 'Pescara':['Delfino Pescara 1936'],
    'Bari':['SSC Bari'], 'Cesena':['Cesena FC'], 'Avellino':['US Avellino 1912'],
}
ALIASES={_key(alias):name for name,variants in GROUPS.items() for alias in [name,*variants]}


def normalize_team(value):
    text=' '.join(str(value or '').split())
    return ALIASES.get(_key(text),text)
