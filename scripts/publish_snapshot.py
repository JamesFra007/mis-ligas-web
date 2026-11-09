"""Build a public static snapshot and optionally refresh its persisted source data.

This command never publishes to a remote host. A GitHub Actions workflow uploads
only the output directory after a successful build. Source credentials, SQLite
files, Python sources and temporary files are not placed in that directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.leagues import LEAGUES
from app.providers import ProviderError, get_coverage, sync_games


def current_seasons(now=None):
    stamp = now or datetime.now(ZoneInfo('America/Bogota'))
    first = stamp.year if stamp.month >= 7 else stamp.year - 1
    return {key:f'{first}-{(first+1)%100:02d}' if info['split_season'] else str(stamp.year) for key,info in LEAGUES.items()}


def persisted_games():
    rows = []
    for season in db.all_seasons():
        rows.extend(db.games(season['league'], season['season']))
    return rows


def refresh_sources():
    """Fail closed before changing the persisted snapshot if a source fails."""
    incoming, sources, errors = [], [], []
    for league, season in current_seasons().items():
        try:
            records = sync_games(league, season)
            if not records:
                raise ProviderError('La fuente no devolvió partidos.')
            coverage = get_coverage(league)
            if coverage.get('fixtures_available') is False:
                raise ProviderError('No está disponible el calendario de próximos partidos; se conserva la publicación anterior.')
            incoming.extend(records)
            sources.append({'league': league, 'season': season, 'received': len(records), 'ok': True, 'coverage': coverage})
        except (ProviderError, OSError, ValueError) as error:
            errors.append(f'{league} {season}: {error}')
    if errors:
        # In Actions the previous website remains deployed. The retained timestamp
        # is preferable to marking only one successfully queried league as fresh.
        raise RuntimeError('No se publicó una actualización parcial: ' + ' | '.join(errors))
    changes = db.upsert(incoming)
    for source in sources:
        if 'missing_ht' in source['coverage']:
            stored = db.games(source['league'], source['season'])
            source['coverage']['missing_ht'] = sum(g['status']=='finished' and not g.get('administrative') and (g.get('ht_home') is None or g.get('ht_away') is None) for g in stored)
    captured = datetime.now(timezone.utc).isoformat()
    snapshot = {'captured_at': captured, 'sources': sources, 'games': persisted_games()}
    target = ROOT / 'data/current.json'
    temporary = target.with_suffix('.new.json')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(target)
    return changes


def build_site(output_dir, *, refresh=False):
    db.initialize()
    changes = refresh_sources() if refresh else None
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Prevent accidentally uploading a database/source directory as the website.
    protected = [ROOT, db.DATA.resolve(), (ROOT / 'data').resolve(), (ROOT / 'app').resolve()]
    if output == ROOT or any(output == p or p in output.parents for p in protected[1:]):
        raise ValueError('La salida debe ser una carpeta exclusiva del sitio, como site/.')
    from scripts.export_hungary_history import export_history
    for key,info in LEAGUES.items():
        if not info['corners'] and not info['cards']:
            export_history(league=key)
    from scripts.build_offline import build
    build(output=output / 'index.html', hosted=True)
    rows = db.all_seasons()
    last = db.setting('last_sync')
    manifest = {
        'schema': 1,
        'application': 'Mis Ligas',
        'hosting_mode': 'static-scheduled',
        'captured_at': last['at'] if last else db.setting('imported_at'),
        'built_at': datetime.now(timezone.utc).isoformat(),
        'matches': sum(s['count'] for s in rows),
        'seasons': rows,
        'refresh_changes': changes,
        'sources': json.loads((ROOT / 'data/current.json').read_text()).get('sources', []) if (ROOT / 'data/current.json').exists() else [],
    }
    (output / '.nojekyll').touch()
    (output / 'health.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    # The output folder is dedicated. Unexpected files can expose deployment data.
    allowed = {'index.html', '.nojekyll', 'health.json'}
    unexpected = [str(p.relative_to(output)) for p in output.rglob('*') if p.is_file() and p.relative_to(output).as_posix() not in allowed]
    if unexpected:
        raise ValueError('La carpeta pública contiene archivos inesperados: ' + ', '.join(unexpected))
    return manifest


def main():
    parser = argparse.ArgumentParser(description='Preparar la versión gratuita de Mis Ligas para GitHub Pages')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'site')
    parser.add_argument('--refresh', action='store_true', help='Consultar las ligas y persistir el estado antes de construir')
    args = parser.parse_args()
    try:
        report = build_site(args.output_dir, refresh=args.refresh)
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
