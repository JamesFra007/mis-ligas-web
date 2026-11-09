"""Stable club display names shared by the archive and the official calendar."""
import re
import unicodedata


def _key(value):
    value=unicodedata.normalize('NFKD',str(value).replace('ł','l').replace('Ł','L'))
    return re.sub(r'[^a-z0-9]+','',value.encode('ascii','ignore').decode().lower())


GROUPS={
    'Legia Varsovia':['Legia','Legia Warszawa','Legia Warsaw'],
    'Lech Poznań':['Lech Poznan','Lech'],
    'Pogoń Szczecin':['Pogon Szczecin','Pogon'],
    'Śląsk Wrocław':['Slask Wroclaw','Slask'],
    'Zagłębie Lubin':['Zaglebie','Zaglebie Lubin','KGHM Zagłębie Lubin'],
    'Zagłębie Sosnowiec':['Zaglebie Sosnowiec'],
    'Wisła Kraków':['Wisla','Wisla Krakow'],
    'Wisła Płock':['Wisla Plock'],
    'Górnik Zabrze':['Gornik Z.','Gornik Zabrze'],
    'Górnik Łęczna':['Gornik Leczna','Leczna'],
    'Raków Częstochowa':['Rakow','Rakow Czestochowa'],
    'ŁKS Łódź':['LKS Lodz','LKS'],
    'Widzew Łódź':['Widzew Lodz','Widzew'],
    'Jagiellonia Białystok':['Jagiellonia','Jagiellonia Bialystok'],
    'Lechia Gdańsk':['Lechia Gdansk','Lechia'],
    'Korona Kielce':['Korona'],
    'Stal Mielec':['Stal'],
    'GKS Katowice':['Katowice'],
    'Bruk-Bet Termalica Nieciecza':['Nieciecza','Termalica B-B.','Bruk-Bet T.','Bruk-Bet Termalica','Termalica Nieciecza'],
    'Cracovia':['Cracovia Krakow','Cracovia Kraków'],
    'Piast Gliwice':['Piast'],
    'Miedź Legnica':['Miedz Legnica','Legnica'],
    'Sandecja Nowy Sącz':['Sandecja Nowy S.'],
    'Warta Poznań':['Warta Poznan','Warta'],
    'Ruch Chorzów':['Ruch Chorzow','Ruch'],
    'Puszcza Niepołomice':['Puszcza','Puszcza Niepolomice'],
    'Podbeskidzie Bielsko-Biała':['Podbeskidzie','Podbeskidzie Bielsko-Biala'],
    'Radomiak Radom':['Radomiak'],
    'Arka Gdynia':['Arka'],
    'Motor Lublin':['Motor'],
}
ALIASES={_key(alias):name for name,variants in GROUPS.items() for alias in [name,*variants]}


def normalize_team(value):
    text=' '.join(str(value or '').split())
    return ALIASES.get(_key(text),text)
