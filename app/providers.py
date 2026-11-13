"""Public results and fixtures, with explicit kickoff confirmation.

Football-Data is the authority for completed Spanish results/statistics. ESPN
adds the Spanish calendar, new completed matches and Colombian results. A TBD
time is never a midnight kickoff: its time and UTC kickoff remain None. Missing
statistics never become zeroes. The caller retains prior audited observations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime, timezone
import io
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)
BOGOTA = ZoneInfo("America/Bogota")
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_SUMMARIES = 40
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/col.1"
SPAIN_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1"
SPAIN_NAMES = {
    "Ath Bilbao": "Athletic Club", "Ath Madrid": "Atlético de Madrid",
    "Vallecano": "Rayo Vallecano", "Betis": "Real Betis",
    "Sociedad": "Real Sociedad", "Valladolid": "Real Valladolid",
    "Oviedo": "Real Oviedo", "Alaves": "Alavés", "Espanol": "Espanyol",
    "Cadiz": "Cádiz", "Almeria": "Almería", "Leganes": "Leganés",
}
ADMIN_IDS = {"564839", "564831", "626317", "714307", "730479", "750683"}
SPAIN_ESPN_NAMES = {
    "83": "Barcelona", "84": "Mallorca", "85": "Celta", "86": "Real Madrid",
    "87": "Santander", "88": "Espanyol", "89": "Real Sociedad",
    "90": "La Coruna", "92": "Real Oviedo", "93": "Athletic Club",
    "94": "Valencia", "96": "Alavés", "97": "Osasuna", "99": "Malaga",
    "101": "Rayo Vallecano", "102": "Villarreal", "243": "Sevilla",
    "244": "Real Betis", "1068": "Atlético de Madrid", "1538": "Levante",
    "2922": "Getafe", "3751": "Elche", "9812": "Girona",
}
SPAIN_ESPN_ALIASES = {
    "Celta Vigo": "Celta", "Atlético Madrid": "Atlético de Madrid",
    "Racing Santander": "Santander", "Deportivo": "La Coruna",
    "Deportivo La Coruña": "La Coruna", "Málaga": "Malaga",
}
_COVERAGE = {}


def get_coverage(league: str) -> dict:
    """Coverage of the latest fetch, for a visible source warning in the UI."""
    if league == 'italy':
        from .italy import get_coverage as italy_coverage
        return italy_coverage()
    if league == 'poland':
        from .poland import get_coverage as poland_coverage
        return poland_coverage()
    if league == 'hungary':
        from .hungary import get_coverage as hungary_coverage
        return hungary_coverage()
    return dict(_COVERAGE.get(league, {}))


class ProviderError(RuntimeError):
    """A public feed could not be fetched or safely interpreted."""


def _fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "MisLigas/1.0 (results dashboard)", "Accept": "application/json,text/csv;q=0.9,*/*;q=0.5"})
    try:
        # urllib inherits the configured HTTP(S) proxy and system CA trust.
        with urlopen(request, timeout=20) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ProviderError(f"La fuente de resultados respondió HTTP {exc.code}; no se cambiaron los datos guardados.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError("No fue posible conectar con la fuente de resultados. Inténtalo de nuevo; los datos guardados se conservan.") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise ProviderError("La respuesta del proveedor supera el límite permitido.")
    return data


def _json(url: str) -> dict:
    try:
        result = json.loads(_fetch(url))
    except (ValueError, UnicodeError) as exc:
        raise ProviderError("La fuente devolvió una respuesta que no se puede interpretar.") from exc
    if not isinstance(result, dict):
        raise ProviderError("La fuente devolvió un formato de resultados inesperado.")
    return result


def _integer(value, maximum: int = 1000):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.0+)?", text):
        return None
    result = int(float(text))
    return result if result <= maximum else None


def _total(a, b):
    return a + b if a is not None and b is not None else None


def _validate_season(league: str, season: str):
    if league == "spain":
        match = re.fullmatch(r"(20\d\d)-(\d\d)", season)
        if not match or int(match[2]) != (int(match[1]) + 1) % 100:
            raise ProviderError("La temporada española debe tener el formato 2026-27.")
        return int(match[1])
    if league == "colombia":
        if not re.fullmatch(r"20\d\d", season):
            raise ProviderError("La temporada colombiana debe ser un año, por ejemplo 2026.")
        return int(season)
    raise ProviderError("Liga no admitida. Selecciona España o Colombia.")


def _football_data(season: str, year: int) -> list[dict]:
    code = f"{year % 100:02d}{(year + 1) % 100:02d}"
    url = f"https://www.football-data.co.uk/mmz4281/{code}/SP1.csv"
    try:
        reader = csv.DictReader(io.StringIO(_fetch(url).decode("utf-8-sig")))
        required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HTHG", "HTAG"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ProviderError("El CSV español no contiene las columnas de resultados esperadas.")
        result = []
        for row in reader:
            if not row.get("HomeTeam") or not row.get("AwayTeam"):
                continue
            home_raw, away_raw = row["HomeTeam"].strip(), row["AwayTeam"].strip()
            date_text = row["Date"].strip()
            fmt = "%d/%m/%Y" if len(date_text.rsplit("/", 1)[-1]) == 4 else "%d/%m/%y"
            dt = datetime.strptime(date_text, fmt)
            time = row.get("Time", "").strip()
            if time:
                dt = datetime.combine(dt.date(), datetime.strptime(time, "%H:%M").time(), ZoneInfo("Europe/London")).astimezone(BOGOTA)
            fh, fa = _integer(row.get("FTHG"), 30), _integer(row.get("FTAG"), 30)
            ht_h, ht_a = _integer(row.get("HTHG"), 30), _integer(row.get("HTAG"), 30)
            status = "finished" if fh is not None and fa is not None else "scheduled"
            if status != "finished":
                fh = fa = ht_h = ht_a = None
            elif ht_h is not None and ht_a is not None and (ht_h > fh or ht_a > fa):
                ht_h = ht_a = None
            stat = lambda k: _integer(row.get(k), 100) if status == "finished" else None
            yh, ya, rh, ra = [stat(k) for k in ("HY", "AY", "HR", "AR")]
            result.append({
                "id": f"spain:{code}:{home_raw}:{away_raw}",
                "provider_id": f"fd:{code}:{home_raw}:{away_raw}",
                "league": "spain", "season": season, "season_label": f"{year}/{(year + 1) % 100:02d}",
                "date": dt.date().isoformat(), "time": dt.strftime("%H:%M") if time else None,
                "kickoff": dt.astimezone(timezone.utc).isoformat() if time else None,
                "time_confirmed": bool(time), "date_confirmed": True,
                "home": SPAIN_NAMES.get(home_raw, home_raw), "away": SPAIN_NAMES.get(away_raw, away_raw),
                "round": None, "scope": "all", "phase": "LaLiga", "status": status,
                "ht_home": ht_h, "ht_away": ht_a, "ft_home": fh, "ft_away": fa,
                "corners": _total(stat("HC"), stat("AC")), "yellow": _total(yh, ya), "red": _total(rh, ra),
                "yellow_home": yh, "yellow_away": ya, "red_home": rh, "red_away": ra,
                "administrative": False, "source": "Football-Data · CSV SP1",
            })
    except (ValueError, UnicodeError, csv.Error) as exc:
        raise ProviderError("El CSV español contiene una fecha o formato inesperado; se conservó el histórico.") from exc
    if not result:
        raise ProviderError("La fuente todavía no publica resultados para esa temporada española.")
    return result


def _colombia_name(competitor: dict, year: int):
    ident = str(competitor.get("id", competitor.get("team", {}).get("id", "")))
    if ident == "7445" and year <= 2025:
        return "La Equidad"
    if ident == "9761" and year <= 2023:
        return "Alianza Petrolera"
    return {"5480": "Boyacá Chicó", "4815": "Junior", "9773": "Patriotas FC", "9769": "Leones", "4928": "Fortaleza FC"}.get(ident, competitor.get("team", {}).get("displayName"))


def _stats(competitor: dict) -> dict:
    values = {v.get("name"): v.get("displayValue") for v in competitor.get("statistics", [])}
    # Historic ESPN blocks can contain placeholder zeroes for every metric.
    signals = ("possessionPct", "totalShots", "shotsOnTarget", "foulsCommitted", "totalPasses", "wonCorners", "saves")
    def positive(value):
        try:
            return float(str(value).rstrip("%")) > 0
        except (ValueError, TypeError):
            return False
    populated = any(positive(values.get(name)) for name in signals)
    return {"corners": _integer(values.get("wonCorners"), 100) if populated else None}


def _espn_status(state: dict) -> str:
    name = str(state.get("name", ""))
    if "CANCEL" in name:
        return "cancelled"
    if any(word in name for word in ("POSTPON", "ABANDON", "SUSPEND")):
        return "postponed"
    if state.get("completed") and state.get("state") == "post":
        return "finished"
    if state.get("state") == "in":
        return "live"
    return "scheduled"


def _espn_clock(event: dict, comp: dict, status: str):
    """Only a source-confirmed kickoff can order or forecast future matches."""
    try:
        stamp = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        local = stamp.astimezone(BOGOTA)
    except (KeyError, ValueError, TypeError):
        return None
    state = event.get("status", comp.get("status", {})).get("type", {})
    detail = " ".join(str(state.get(key, "")) for key in ("detail", "shortDetail")).upper()
    # A default 20:00Z on an unconfirmed Sunday is a placeholder, not a time.
    confirmed = comp.get("timeValid") is True and "TBD" not in detail and "TBA" not in detail
    return {
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M") if confirmed else None,
        "kickoff": stamp.astimezone(timezone.utc).isoformat() if confirmed else None,
        "time_confirmed": confirmed,
        "date_confirmed": status in ("finished", "live") or confirmed,
    }


def _parse_espn_event(event: dict, year: int, league: str = "colombia"):
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    comp = competitions[0]
    slug = str(event.get("season", {}).get("slug", "")).lower()
    if "superliga" in slug:
        return None
    sides = {v.get("homeAway"): v for v in comp.get("competitors", [])}
    if set(sides) != {"home", "away"}:
        return None
    if league == "spain":
        def spanish_name(side):
            competitor = sides[side]
            ident = str(competitor.get("id", competitor.get("team", {}).get("id", "")))
            name = competitor.get("team", {}).get("displayName")
            return SPAIN_ESPN_NAMES.get(ident, SPAIN_ESPN_ALIASES.get(name, name))
        home, away = spanish_name("home"), spanish_name("away")
    else:
        home, away = _colombia_name(sides["home"], year), _colombia_name(sides["away"], year)
    if not home or not away:
        return None
    status_type = event.get("status", comp.get("status", {})).get("type", {})
    status = _espn_status(status_type)
    clock = _espn_clock(event, comp, status)
    if clock is None or not event.get("id"):
        return None
    if league == "spain":
        # The calendar endpoint is requested by calendar year; retain only the
        # requested July-to-June LaLiga season, not January's previous season.
        if not date(year, 7, 1) <= date.fromisoformat(clock["date"]) < date(year + 1, 7, 1):
            return None
        season_match = re.search(r"(20\d\d)-(\d\d)", slug)
        if season_match and season_match.group(0) != f"{year}-{(year + 1) % 100:02d}":
            return None
    admin = (league == "colombia" and str(event["id"]) in ADMIN_IDS) or "AWARD" in str(status_type.get("name", "")) or "ABANDON" in str(status_type.get("name", ""))
    ft_h = _integer(sides["home"].get("score"), 30) if status == "finished" else None
    ft_a = _integer(sides["away"].get("score"), 30) if status == "finished" else None
    first_h, first_a = None, None
    # Some newer scoreboards publish period scores; old scoreboards do not.
    if status == "finished":
        firsts = []
        for side in ("home", "away"):
            lines = sides[side].get("linescores") or []
            first = next((x for x in lines if x.get("period") == 1), None) or (lines[0] if lines else {})
            firsts.append(_integer(first.get("displayValue", first.get("value")), 30))
        first_h, first_a = firsts
        if all(v is not None for v in (first_h, first_a, ft_h, ft_a)) and (first_h > ft_h or first_a > ft_a):
            first_h = first_a = None
    if league == "spain":
        scope = "all"
    elif "clausura" in slug or "finaliz" in slug:
        scope = "Finalización"
    elif "apertura" in slug:
        scope = "Apertura"
    else:
        scope = "Temporada"
    # Only source-published explicit matchday numbers are accepted.
    round_value = _integer((event.get("week") or {}).get("number"), 100)
    if round_value == 0 or (league == "spain" and round_value is not None and round_value > 38):
        round_value = None
    if league == "spain" and round_value is not None:
        scope = "first" if round_value <= 19 else "second"
    ch, ca = _stats(sides["home"])["corners"], _stats(sides["away"])["corners"]
    return {
        "id": f"{league}:{event['id']}", "provider_id": ("espn:" if league == "spain" else "") + str(event["id"]),
        "espn_id": str(event["id"]),
        "league": league, "season": f"{year}-{(year + 1) % 100:02d}" if league == "spain" else str(year),
        "season_label": f"{year}/{(year + 1) % 100:02d}" if league == "spain" else str(year),
        **clock,
        "home": home, "away": away, "round": round_value, "scope": scope, "phase": "LaLiga" if league == "spain" else "Liga BetPlay",
        "status": status, "ht_home": None if admin else first_h, "ht_away": None if admin else first_a,
        "ft_home": ft_h, "ft_away": ft_a,
        "corners": _total(ch, ca) if status == "finished" and not admin else None,
        "yellow": None, "red": None, "yellow_home": None, "yellow_away": None, "red_home": None, "red_away": None,
        "administrative": admin, "source": "ESPN · LaLiga" if league == "spain" else "ESPN · Primera A",
    }


def _enrich_espn(game: dict, base: str = ESPN_BASE) -> dict:
    event_id = game.get("espn_id") or game['provider_id']
    summary = _json(f"{base}/summary?event={event_id}")
    competitions = summary.get("header", {}).get("competitions") or []
    header_sides = {c.get("homeAway"): c for c in (competitions[0].get("competitors", []) if competitions else [])}
    if set(header_sides) == {"home", "away"}:
        ht = []
        for side in ("home", "away"):
            lines = header_sides[side].get("linescores") or []
            first = next((x for x in lines if x.get("period") == 1), None) or (lines[0] if lines else {})
            ht.append(_integer(first.get("displayValue", first.get("value")), 30))
        if all(v is not None for v in ht) and all(game[k] is not None and h <= game[k] for h, k in zip(ht, ("ft_home", "ft_away"))):
            game["ht_home"], game["ht_away"] = ht
    box = {str(t.get("team", {}).get("id")): t for t in summary.get("boxscore", {}).get("teams", [])}
    corners = []
    for side in ("home", "away"):
        ident = str(header_sides.get(side, {}).get("id", ""))
        corners.append(_stats(box.get(ident, {}))["corners"])
    if all(v is not None for v in corners):
        game["corners"] = sum(corners)
    if game.get("league") == "spain":
        for side in ("home", "away"):
            ident = str(header_sides.get(side, {}).get("id", ""))
            stats = box.get(ident, {})
            # Do not promote all-zero placeholder statistics to observations.
            if _stats(stats)["corners"] is None:
                continue
            values = {v.get("name"): v.get("displayValue") for v in stats.get("statistics", [])}
            game["yellow_" + side] = _integer(values.get("yellowCards"), 100)
            game["red_" + side] = _integer(values.get("redCards"), 100)
        game["yellow"] = _total(game.get("yellow_home"), game.get("yellow_away"))
        game["red"] = _total(game.get("red_home"), game.get("red_away"))
    return game


def _colombia(season: str, year: int) -> list[dict]:
    data = _json(f"{ESPN_BASE}/scoreboard?dates={year}&limit=1000")
    events = data.get("events")
    if not isinstance(events, list):
        raise ProviderError("La fuente colombiana no devolvió una lista de partidos válida.")
    if len(events) >= 1000:
        raise ProviderError("La fuente colombiana devolvió el límite de partidos; se necesita paginar antes de importar.")
    games = [g for e in events if (g := _parse_espn_event(e, year)) is not None]
    if not games:
        raise ProviderError("La fuente todavía no publica partidos para ese año colombiano.")
    recent = sorted((g for g in games if g["status"] == "finished" and not g["administrative"]), key=lambda g: (g["date"], g["time"] or ""), reverse=True)[:MAX_SUMMARIES]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_enrich_espn, g): g for g in recent}
        for future in as_completed(futures):
            try:
                future.result()
            except ProviderError as exc:
                LOGGER.warning("No se pudo completar HT del evento %s: %s", futures[future]["provider_id"], exc)
    _COVERAGE["colombia"] = {
        "fixtures_available": True,
        "scheduled": sum(g["status"] == "scheduled" for g in games),
        "confirmed_upcoming": sum(g["status"] == "scheduled" and g["time_confirmed"] for g in games),
        "warnings": [],
    }
    return games


def _spain_calendar(year: int) -> list[dict]:
    # ESPN rejects some full-season date ranges; two calendar-year requests
    # include both halves without inferring fixture dates or matchdays.
    events = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(_json, [f"{SPAIN_ESPN_BASE}/scoreboard?dates={y}&limit=1000" for y in (year, year + 1)]))
    for data in responses:
        values = data.get("events")
        if not isinstance(values, list):
            raise ProviderError("El calendario español no devolvió una lista de partidos válida.")
        if len(values) >= 1000:
            raise ProviderError("El calendario español alcanzó el límite de partidos; es necesario paginar antes de importarlo.")
        for event in values:
            if row := _parse_espn_event(event, year, "spain"):
                events[row["provider_id"]] = row
    return sorted(events.values(), key=lambda g: (g["date"], g["time"] or "99:99", g["id"]))


def _spain(season: str, year: int) -> list[dict]:
    # Keep the CSV's observed statistics and stable IDs. ESPN supplies fixture
    # metadata and new results until the CSV publishes those observations.
    warnings = []
    try:
        rows = _football_data(season, year)
    except ProviderError as error:
        # New seasons can have published fixtures before the first CSV result.
        # ESPN remains useful then; later CSV observations reconcile by fixture.
        rows = []
        warnings.append("Football-Data no está disponible para esta temporada; se conservan los datos previos y se utiliza el calendario de ESPN.")
        LOGGER.warning("CSV español no disponible: %s", error)
    try:
        calendar = _spain_calendar(year)
    except ProviderError as error:
        if not rows:
            raise ProviderError("No fue posible consultar resultados ni calendario para esta temporada; se conservaron los datos guardados.") from error
        LOGGER.warning("Calendario español no disponible: %s", error)
        _COVERAGE["spain"] = {"fixtures_available": False, "warnings": ["Los resultados se actualizaron con Football-Data, pero no se pudo consultar el calendario de próximos partidos."]}
        return rows
    if not rows and not calendar:
        raise ProviderError("Las fuentes todavía no publican partidos para esta temporada española.")
    by_fixture = {(g["home"], g["away"]): g for g in rows}
    new_completed = []
    for event in calendar:
        target = by_fixture.get((event["home"], event["away"]))
        if target is not None:
            for key in ("espn_id", "round", "scope"):
                if event.get(key) is not None and (key != "scope" or event[key] != "all"):
                    target[key] = event[key]
            # Respect source-confirmed kickoff; never replace a known CSV time
            # with a placeholder date/time. Results stay under CSV authority.
            if event["time_confirmed"]:
                target.update({k: event[k] for k in ("date", "time", "kickoff", "time_confirmed", "date_confirmed")})
            if target["status"] != "finished" and event["status"] in ("finished", "live", "postponed", "cancelled"):
                target["status"] = event["status"]
                for key in ("ht_home", "ht_away", "ft_home", "ft_away", "corners"):
                    if event.get(key) is not None:
                        target[key] = event[key]
                target["source"] = "ESPN · LaLiga (resultado reciente)"
                if target["status"] == "finished":
                    new_completed.append(target)
            continue
        by_fixture[(event["home"], event["away"])] = event
        rows.append(event)
        if event["status"] == "finished" and not event["administrative"]:
            new_completed.append(event)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_enrich_espn, g, SPAIN_ESPN_BASE): g for g in new_completed[-MAX_SUMMARIES:]}
        for future in as_completed(futures):
            try:
                future.result()
            except ProviderError as error:
                LOGGER.warning("No se pudo completar HT del partido español %s: %s", futures[future]["provider_id"], error)
    _COVERAGE["spain"] = {
        "fixtures_available": bool(calendar),
        "scheduled": sum(g["status"] == "scheduled" for g in rows),
        "confirmed_upcoming": sum(g["status"] == "scheduled" and g.get("time_confirmed", False) for g in rows),
        "warnings": warnings + ([] if calendar else ["La fuente no ofrece próximos partidos para esta temporada."]),
    }
    return rows


def sync_games(league: str, season: str) -> list[dict]:
    """Fetch a season snapshot; callers upsert by stable IDs without deleting rows."""
    if league == 'italy':
        from .italy import sync_games as italy_sync
        from . import db
        stored = db.games(league, season) if db.DB_PATH.exists() else []
        return italy_sync(season, saved_rounds=[g for g in stored if g.get('phase') == 'Serie A'])
    if league == 'poland':
        from .poland import sync_games as poland_sync
        from . import db
        stored = db.games(league, season) if db.DB_PATH.exists() else []
        pending = [g['id'] for g in stored if g['status']=='finished' and not g.get('administrative') and (g.get('ht_home') is None or g.get('ht_away') is None)]
        return poland_sync(season, pending_ids=pending)
    if league == 'hungary':
        from .hungary import sync_games as hungary_sync
        return hungary_sync(season)
    year = _validate_season(league, season)
    return _spain(season, year) if league == "spain" else _colombia(season, year)
