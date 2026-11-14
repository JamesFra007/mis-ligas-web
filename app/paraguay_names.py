"""Canonical Paraguayan club names shared by history and current fixtures."""
import re
import unicodedata


def _key(value):
    text=unicodedata.normalize('NFKD',str(value or ''))
    return re.sub(r'[^a-z0-9]+','',text.encode('ascii','ignore').decode().lower())


GROUPS={
    'Olimpia':['Club Olimpia','Olimpia Asuncion','Olimpia Asunción'],
    'Cerro Porteño':['Cerro Porteno','Club Cerro Porteño'],
    'Libertad':['Club Libertad','Libertad Asuncion'],
    'Nacional':['Nacional Asunción','Nacional Asuncion','Club Nacional'],
    'Guaraní':['Guarani','Club Guaraní','Guarani Asuncion','Guaraní Asunción'],
    'Sportivo Luqueño':['Sportivo Luqueno','Luqueno','Luqueño','Sp. Luqueno','Sp.Luqueno'],
    'Sportivo Trinidense':['Trinidense','Club Sportivo Trinidense'],
    'Sportivo Ameliano':['Ameliano'],
    '2 de Mayo':['Club 2 de Mayo','Sportivo 2 de Mayo','2 de Mayo PJC'],
    'Deportivo Recoleta':['Recoleta','Dep. Recoleta'],
    'Rubio Ñú':['Rubio Nu','Rubio Ñu','Club Rubio Ñú'],
    'Sportivo San Lorenzo':['San Lorenzo','Sportivo SanLorenzo'],
    'Sol de América':['Sol de America','Club Sol de América'],
    'Deportivo Capiatá':['Deportivo Capiata','Dep. Capiata','Capiata','Capiatá'],
    'Deportivo Santaní':['Deportivo Santani','Dep. Santani','Santani','Santaní'],
    'General Díaz':['General Diaz','Gral. Diaz','Diaz'],
    'General Caballero JLM':['General Caballero','General Caballero JLM','General Caballero SC','General Caballero de Juan León Mallorquín'],
    'Independiente FBC':['Independiente F.B.C.','Independiente Campo Grande','Independiente'],
    'River Plate':['Club River Plate','River Plate Asuncion','River Plate (Asunción)'],
    '12 de Octubre':['12 de Octubre Itaugua','12 de Octubre Itauguá'],
    'Guaireña':['Guairena','Guaireña FC'],
    'Tacuary':['Tacuary FC','Tacuary FBC'],
    'Resistencia':['Resistencia SC','Resistencia F.C.'],
    'Atlético Tembetary':['Club Atlético Tembetary','Atl. Tembetary','Atletico Tembetary','Tembetary'],
}
ALIASES={_key(alias):name for name,aliases in GROUPS.items() for alias in [name,*aliases]}


def normalize_team(value):
    text=' '.join(str(value or '').split())
    return ALIASES.get(_key(text),text)
