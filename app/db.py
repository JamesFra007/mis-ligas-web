from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from .leagues import LEAGUES

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('LIGAS_DATA_DIR', ROOT / 'data'))
DB_PATH = DATA / 'ligas.sqlite3'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=30000')
    try:
        yield con
        con.commit()
    finally:
        con.close()


def initialize(seed_path=None):
    loaded = False
    with connection() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, league TEXT NOT NULL, season TEXT NOT NULL, provider_id TEXT, home TEXT NOT NULL, away TEXT NOT NULL, date TEXT NOT NULL, source_order INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
        con.execute('CREATE INDEX IF NOT EXISTS game_season ON games (league, season, source_order)')
        con.execute('CREATE INDEX IF NOT EXISTS game_provider ON games (league, provider_id)')
        con.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        if not con.execute('SELECT COUNT(*) FROM games').fetchone()[0]:
            path = Path(seed_path or ROOT / 'data' / 'seed.json')
            if path.exists():
                loaded = True
                rows = json.loads(path.read_text())
                if isinstance(rows, dict):
                    rows = rows['games']
                stamp = now()
                for game in rows:
                    validate(game)
                    _insert(con, game, stamp)
                con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('imported_at', stamp))
    # A captured source snapshot makes the reviewed version usable offline.
    snapshot = ROOT / 'data' / 'current.json'
    if loaded and seed_path is None and snapshot.exists():
        current = json.loads(snapshot.read_text())
        counts = upsert(current['games'])
        with connection() as con:
            con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('last_sync', json.dumps({**counts, 'at': current['captured_at'], 'success': True, 'source': 'Captura verificada incluida en esta versión'})))


def validate(game):
    for key in ('id', 'league', 'season', 'home', 'away', 'date'):
        if not game.get(key):
            raise ValueError(f'Falta {key} en un partido')
    if game['league'] not in LEAGUES:
        raise ValueError('Liga desconocida')
    if game.get('status') not in ('finished', 'scheduled', 'live', 'postponed', 'cancelled'):
        raise ValueError('Estado desconocido')
    for key in ('ht_home', 'ht_away', 'ft_home', 'ft_away', 'corners', 'yellow', 'red', 'yellow_home', 'yellow_away', 'red_home', 'red_away'):
        value = game.get(key)
        if value is not None and (type(value) is not int or value < 0 or value > 100):
            raise ValueError(f'Dato inválido: {key}')
    if not game.get('administrative'):
        for side in ('home', 'away'):
            ht, ft = game.get('ht_' + side), game.get('ft_' + side)
            if ht is not None and ft is not None and ht > ft:
                raise ValueError('El marcador HT supera al FT')


def _insert(con, game, stamp):
    con.execute('INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?)', (game['id'], game['league'], game['season'], game.get('provider_id'), game['home'], game['away'], game['date'], game.get('source_order', 0), json.dumps(game, ensure_ascii=False), stamp))


def games(league, season):
    with connection() as con:
        return [json.loads(row['payload']) for row in con.execute('SELECT payload FROM games WHERE league=? AND season=? ORDER BY source_order, date, id', (league, season))]


def all_seasons():
    with connection() as con:
        return [dict(row) for row in con.execute('SELECT league, season, COUNT(*) AS count FROM games GROUP BY league, season ORDER BY season DESC')]


def setting(key):
    with connection() as con:
        row = con.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row['value'])
        except (ValueError, TypeError):
            return row['value']


def upsert(incoming):
    """Atomic incremental update; missing provider values never erase audited data."""
    counts = dict(added=0, updated=0, unchanged=0)
    stamp = now()
    with connection() as con:
        for candidate in incoming:
            new = dict(candidate)
            row = con.execute('SELECT * FROM games WHERE id=?', (new['id'],)).fetchone() if new.get('id') else None
            if row is None and new.get('provider_id'):
                row = con.execute('SELECT * FROM games WHERE league=? AND provider_id=?', (new['league'], str(new['provider_id']))).fetchone()
            if row is None and new['league'] == 'hungary' and new.get('fixture_key'):
                # MLSZ assigns report IDs only after a fixture has been played.
                # Reconcile a provisional fixture, but retain two genuine reports
                # for an abandoned match and its replay in the same round.
                matches = con.execute("SELECT * FROM games WHERE league=? AND season=? AND json_extract(payload,'$.fixture_key')=?", ('hungary',new['season'],new['fixture_key'])).fetchall()
                incoming_provisional = str(new.get('provider_id','')).startswith('fixture:')
                if not incoming_provisional:
                    matches = [r for r in matches if str(r['provider_id'] or '').startswith('fixture:') and not json.loads(r['payload']).get('administrative')]
                else:
                    matches = [r for r in matches if not json.loads(r['payload']).get('administrative')]
                if len(matches)==1:
                    row = matches[0]
                    if incoming_provisional and str(row['provider_id'] or '').startswith('mlsz:'):
                        # An old provisional calendar must not replace an actual report.
                        counts['unchanged'] += 1
                        continue
            if row is None and new['league'] == 'spain':
                row = con.execute('SELECT * FROM games WHERE league=? AND season=? AND home=? AND away=?', (new['league'], new['season'], new['home'], new['away'])).fetchone()
            if row is None:
                new['id'] = new.get('id') or f"{new['league']}:{new['season']}:{new.get('provider_id') or new['date'] + ':' + new['home'] + ':' + new['away']}"
                latest = con.execute('SELECT MAX(source_order) FROM games WHERE league=? AND season=?', (new['league'], new['season'])).fetchone()[0]
                new['source_order'] = (latest or 0) + 1
                validate(new)
                _insert(con, new, stamp)
                counts['added'] += 1
                continue
            if new['league']=='hungary' and str(new.get('provider_id','')).startswith('fixture:') and str(row['provider_id'] or '').startswith('mlsz:'):
                counts['unchanged'] += 1
                continue
            old = json.loads(row['payload'])
            merged = dict(old)
            # Actual dates/status can change, but absent stats cannot replace observations.
            for key, value in new.items():
                if key == 'scope' and old.get('scope') and old.get('scope') != 'all' and value in ('all', 'Temporada'):
                    continue
                if key == 'phase' and old.get('phase') and value in ('Liga BetPlay', 'LaLiga'):
                    continue
                if value is not None and key not in ('id', 'source_order', 'administrative'):
                    merged[key] = value
            # A source can withdraw a future kickoff. Missing statistics remain
            # protected, but an explicitly unconfirmed future time must be cleared.
            if old.get('status') != 'finished' and new.get('status') != 'finished' and new.get('time_confirmed') is False:
                merged['time'] = None
                merged['kickoff'] = None
                merged['time_confirmed'] = False
            if old.get('status') == 'finished' and (new.get('date_confirmed') is False or (new.get('time_confirmed') is False and new.get('time') is None)):
                for key in ('date','time','kickoff'):
                    merged[key] = old.get(key)
                merged['date_confirmed'] = old.get('date_confirmed',True)
                merged['time_confirmed'] = old.get('time_confirmed',bool(old.get('time')))
            merged['administrative'] = bool(old.get('administrative') or new.get('administrative'))
            if new['league']=='hungary' and new.get('administrative'):
                merged['ht_home']=None
                merged['ht_away']=None
            if old.get('administrative'):
                for key in ('status', 'ht_home', 'ht_away', 'ft_home', 'ft_away'):
                    merged[key] = old.get(key)
            if old.get('status') == 'finished' and merged.get('status') != 'finished':
                merged['status'] = 'finished'
            validate(merged)
            if merged == old:
                counts['unchanged'] += 1
            else:
                con.execute('UPDATE games SET season=?, provider_id=?, home=?, away=?, date=?, payload=?, updated_at=? WHERE id=?', (merged['season'], merged.get('provider_id'), merged['home'], merged['away'], merged['date'], json.dumps(merged, ensure_ascii=False), stamp, old['id']))
                counts['updated'] += 1
        con.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('last_sync', json.dumps({**counts, 'at': stamp, 'success': True})))
    return counts
