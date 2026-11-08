'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const initial = new URLSearchParams(window.location.search);
  const state = {league: initial.get('league') || 'spain', season: initial.get('season') || '', scope: initial.get('scope') || 'all', team: initial.get('team') || 'all', condition: initial.get('condition') || 'all', status: initial.get('status') || 'all', search: initial.get('search') || '', order: initial.get('order') === 'chronological' ? 'chronological' : 'round', page: 1, page_size: 40};
  let meta = null, dashboard = null, controller = null, requestNumber = 0, searchTimer = null, syncing = false, forecastExpiryTimer = null;
  const number = new Intl.NumberFormat('es-CO', {maximumFractionDigits: 0});
  const decimal = new Intl.NumberFormat('es-CO', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const percentage = new Intl.NumberFormat('es-CO', {style: 'percent', maximumFractionDigits: 1, minimumFractionDigits: 1});
  const integer = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : number.format(Number(value));
  const average = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : decimal.format(Number(value));
  const percent = value => value === null || value === undefined || !Number.isFinite(Number(value)) ? '—' : percentage.format(Number(value));
  const element = (tag, className, text) => {const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node;};
  const currentLeague = () => meta?.leagues?.find(league => league.id === state.league);
  const currentSeason = () => currentLeague()?.seasons?.find(season => String(season.id) === state.season);
  const hasCards = () => Boolean(currentLeague()?.cards);
  const hasCorners = () => currentLeague()?.corners !== false;
  const visibleColumnCount = () => 7 + (hasCorners() ? 1 : 0) + (hasCards() ? 2 : 0);
  const query = () => {const params = new URLSearchParams(); Object.entries(state).forEach(([key, value]) => {if (value !== '') params.set(key, String(value));}); return params;};
  const safeCount = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const isOffline = () => meta?.mode === 'offline' || Boolean(meta?.offline);
  const isHostedSnapshot = () => meta?.mode === 'hosted_snapshot';
  const syncLabel = () => isOffline() ? 'Copia sin conexión' : isHostedSnapshot() ? 'Recargar datos' : 'Actualizar datos';

  function notice(message, error = false) {
    const node = $('notice'); node.textContent = message; node.className = 'notice' + (error ? ' error' : ''); node.hidden = !message;
  }
  function apiError(body, fallback) {
    if (typeof body?.detail === 'string') return body.detail;
    if (typeof body?.message === 'string') return body.message;
    return fallback;
  }
  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, {...options, headers: {Accept: 'application/json', ...(options.headers || {})}});
    let body;
    try {body = await response.json();} catch {throw new Error('El servidor no devolvió una respuesta válida. Intenta de nuevo.');}
    if (!response.ok) throw new Error(apiError(body, `No se pudieron consultar los datos (${response.status}).`));
    return body;
  }
  function formatDate(value) {
    if (!value) return 'Fecha pendiente';
    const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    return new Intl.DateTimeFormat('es-CO', {day: '2-digit', month: 'short', year: 'numeric'}).format(date);
  }
  function formatTimestamp(value) {
    if (!value) return 'Archivo histórico importado';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Archivo histórico importado';
    return (meta?.last_sync ? 'Última consulta: ' : 'Última carga: ') + new Intl.DateTimeFormat('es-CO', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota'}).format(date) + ' COL';
  }
  function setOptions(select, options, selected) {
    select.replaceChildren();
    for (const item of options) {const option = element('option', '', item.label); option.value = String(item.id); select.append(option);}
    if (options.some(option => String(option.id) === selected)) select.value = selected;
    else if (options.length) select.value = String(options[0].id);
    select.disabled = false;
  }
  function bestSeason(league) {
    const seasons = league?.seasons || [];
    return String((seasons.find(season => safeCount(season.count) > 0) || seasons[0] || {}).id || '');
  }
  function renderMeta(keepSeason = true) {
    const leagues = meta?.leagues || [];
    if (!leagues.some(league => league.id === state.league)) state.league = leagues[0]?.id || 'spain';
    const league = currentLeague();
    if (!keepSeason || !league?.seasons?.some(season => String(season.id) === state.season)) state.season = bestSeason(league);
    $('league-nav').replaceChildren();
    leagues.forEach(item => {
      const button = element('button', 'league-button' + (item.id === state.league ? ' active' : ''));
      button.type = 'button'; button.setAttribute('aria-pressed', String(item.id === state.league));
      button.append(element('span', 'league-flag', item.flag || ({spain: '🇪🇸', colombia: '🇨🇴', hungary: '🇭🇺'}[item.id] || '⚽')));
      const label = element('span'); label.append(element('strong', '', item.name), element('small', '', item.country));
      button.append(label, element('span', 'league-chevron', '›'));
      button.addEventListener('click', () => changeLeague(item.id)); $('league-nav').append(button);
    });
    setOptions($('season-select'), (league?.seasons || []).map(season => ({id: season.id, label: season.label})), state.season);
    $('breadcrumb-league').textContent = league?.name || 'Resultados';
    document.body.classList.toggle('hide-cards', !hasCards());
    document.body.classList.toggle('hide-corners', !hasCorners());
    $('archive-tag').textContent = leagues.map(item => item.country || item.name).join(' · ');
    $('history-link').href = '/api/history.xlsx?' + new URLSearchParams({league: state.league});
    $('history-link').removeAttribute('aria-disabled');
    $('sync-button').disabled = syncing || isOffline();
    $('sync-button').querySelector('span').textContent = syncLabel();
    const mode = isOffline()
      ? 'Copia sin conexión. Los partidos y horarios corresponden a la última captura; para incorporar resultados nuevos se necesita la versión publicada o con servidor.'
      : isHostedSnapshot()
        ? `Actualización programada aproximadamente cada ${integer(meta.update_interval_minutes)} minutos; puede retrasarse. «Recargar datos» abre la última versión publicada.`
        : meta.auto_update
          ? `Actualización programada cada ${integer(meta.update_interval_minutes)} minutos mientras el servidor esté activo.`
          : 'Actualización manual: usa «Actualizar datos». La actualización continua requiere mantener el servidor activo.';
    $('update-mode').textContent = mode;
    $('data-timestamp').textContent = formatTimestamp(meta.updated_at);
    updateHeading();
  }
  function updateHeading() {
    const league = currentLeague(); const season = currentSeason();
    $('page-title').textContent = `${league?.name || 'Resultados'} · ${season?.label || state.season}`;
    document.title = `${league?.name || 'Mis Ligas'} ${season?.label || state.season} · Mis Ligas`;
    $('page-description').textContent = `${league?.country || ''} · Resultados, frecuencia y estadísticas de cada partido.`;
    const exportParams = query(); exportParams.delete('page'); exportParams.delete('page_size');
    $('export-link').href = '/api/export.xlsx?' + exportParams;
    $('export-link').removeAttribute('aria-disabled');
  }
  function updateControls() {
    $('condition-select').value = state.condition; $('condition-select').disabled = state.team === 'all';
    $('status-select').value = state.status; $('search-input').value = state.search;
    document.querySelectorAll('[data-order]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.order === state.order)));
    $('order-note').textContent = state.order === 'chronological' ? 'Fecha y hora de Colombia. Los horarios pendientes se identifican sin inventar una hora.' : 'Se conserva el orden de las jornadas del archivo; los encuentros nuevos mantienen la jornada informada por la fuente.';
  }
  function resetFilters() {
    Object.assign(state, {scope: 'all', team: 'all', condition: 'all', status: 'all', search: '', page: 1});
    updateControls(); loadDashboard();
  }
  function changeLeague(id) {
    if (id === state.league) return;
    state.league = id; state.season = bestSeason(currentLeague());
    Object.assign(state, {scope: 'all', team: 'all', condition: 'all', status: 'all', search: '', page: 1});
    renderMeta(); updateControls(); renderForecast(null, 'Consultando el calendario de la liga…'); loadDashboard();
  }
  function kpi(label, value, subtitle, tone, icon, featured = false) {
    const box = element('article', 'kpi ' + tone + (featured ? ' featured' : ''));
    const heading = element('div', 'kpi-heading');
    heading.append(element('div', 'kpi-label', label));
    const marker = element('span', 'kpi-icon'); marker.setAttribute('aria-hidden', 'true'); marker.append(kpiIcon(icon)); heading.append(marker);
    box.append(heading, element('div', 'kpi-value', value), element('div', 'kpi-sub', subtitle));
    if (featured) box.append(element('div', 'kpi-underline'));
    return box;
  }
  function kpiIcon(kind) {
    const svg = svgNode('svg', {viewBox: '0 0 24 24'});
    const paths = {'↗': 'm4 17 7-7 4 4 5-9M15 5h5v5', '⇄': 'M4 7h16l-4-4M20 17H4l4 4', '⚑': 'M6 22V3h12l-2 4 2 4H6', '✓': 'm4 12 5 5L20 6'};
    if (paths[kind]) svg.append(svgNode('path', {d: paths[kind]}));
    else if (kind === '▯') svg.append(svgNode('rect', {x: 7, y: 3, width: 10, height: 18, rx: 1}));
    else if (kind === '◷') {svg.append(svgNode('circle', {cx: 12, cy: 12, r: 8}), svgNode('path', {d: 'M12 6v6h5'}));}
    else if (kind === '◎') {svg.append(svgNode('circle', {cx: 12, cy: 12, r: 8}), svgNode('circle', {cx: 12, cy: 12, r: 3}));}
    else svg.append(svgNode('circle', {cx: 12, cy: 12, r: 2}));
    return svg;
  }
  function renderKPIs(summary) {
    const n = summary || {};
    const cards = [
      kpi('Ambos marcan · HT', percent(n.ht_rate), `${integer(n.ht_cases)} de ${integer(n.ht_base)} partidos con HT`, 'green', '↗', true),
      kpi('Ambos marcan · FT', percent(n.ft_rate), `${integer(n.ft_cases)} de ${integer(n.ft_base)} partidos con FT`, 'purple', '⇄')
    ];
    if (hasCorners()) cards.push(kpi('Córners por partido', average(n.corners_average), `Total de ambos · ${integer(n.corners_base)} con dato`, 'gold', '⚑'));
    cards.push(kpi('Goles por partido · FT', average(n.goals_ft_average), 'Promedio al finalizar el partido', 'blue', '◎'));
    cards.push(kpi('Goles por partido · HT', average(n.goals_ht_average), 'Promedio al descanso', 'teal', '◷'));
    if (hasCards()) {
      cards.push(kpi('Amarillas por partido', average(n.yellow_average), `${integer(n.yellow_total)} tarjetas · ${integer(n.yellow_base)} con dato`, 'gold', '▯'));
      cards.push(kpi('Partidos con roja', percent(n.red_match_rate), `${integer(n.red_matches)} partidos · media ${average(n.red_average)} rojas`, 'red', '▯'));
    } else {
      if (hasCorners()) cards.push(kpi('Córners pendientes', integer(n.corners_missing), 'Los datos ausentes no cuentan como cero', 'gold', '⚑'));
      cards.push(kpi('Partidos por jugar', integer(safeCount(n.scheduled) + safeCount(n.live)), `${integer(n.live)} en juego · ${integer(n.scheduled)} programados`, 'blue', '◷'));
    }
    cards.push(kpi('Partidos analizados', integer(n.finished), n.excluded ? `${integer(n.excluded)} administrativos fuera del análisis` : 'Finalizados con resultado deportivo', 'teal', '✓'));
    $('kpi-grid').classList.toggle('compact-kpis', cards.length === 6);
    $('kpi-grid').replaceChildren(...cards); $('kpi-grid').setAttribute('aria-busy', 'false');
  }
  function svgNode(tag, attributes, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function renderOutcomes(summary) {
    const data = [
      {label: 'Gana local', count: safeCount(summary.home_wins), color: '#39ac84'},
      {label: 'Empate', count: safeCount(summary.draws), color: '#e3bb6e'},
      {label: 'Gana visitante', count: safeCount(summary.away_wins), color: '#809de0'}
    ];
    const total = data.reduce((sum, value) => sum + value.count, 0);
    $('outcome-chart').replaceChildren(); $('outcome-legend').replaceChildren();
    if (!total) {$('outcome-chart').append(element('div', 'empty-chart', 'Sin partidos finalizados para esta selección.')); return;}
    const svg = svgNode('svg', {viewBox: '0 0 300 84', role: 'img', 'aria-label': data.map(value => `${value.label}: ${value.count}`).join('. ')});
    svg.append(svgNode('text', {x: '0', y: '15', fill: '#a1aebd', 'font-size': '9'}, `${integer(total)} partidos finalizados`));
    const defs = svgNode('defs'); const clip = svgNode('clipPath', {id: 'outcome-clip'}); clip.append(svgNode('rect', {x: '0', y: '30', width: '300', height: '29', rx: '7'})); defs.append(clip); svg.append(defs);
    const bars = svgNode('g', {'clip-path': 'url(#outcome-clip)'}); let left = 0;
    data.forEach(value => {
      const width = value.count / total * 300;
      bars.append(svgNode('rect', {x: left, y: 30, width: width + .1, height: 29, fill: value.color}));
      if (width > 32) bars.append(svgNode('text', {x: left + width / 2, y: 49, 'text-anchor': 'middle', fill: '#fff', 'font-size': '10', 'font-weight': '600'}, `${Math.round(value.count / total * 100)}%`));
      left += width;
      const legend = element('div', 'legend-item'); const dot = element('i'); dot.style.backgroundColor = value.color;
      legend.append(dot, element('span', '', value.label), element('b', '', `${integer(value.count)} partidos`)); $('outcome-legend').append(legend);
    });
    svg.append(bars); $('outcome-chart').append(svg);
  }
  function renderFrequency(data) {
    const root = $('frequency-content'); root.replaceChildren();
    const head = element('div', 'frequency-head'); head.append(element('span', '', 'Momento'), element('span', '', '1 cada N'), element('span', '', 'Racha sin caso')); root.append(head);
    for (const period of ['ht', 'ft']) {
      const row = element('div', 'frequency-row ' + period); const label = element('span'); label.append(element('span', 'period-chip', period.toUpperCase()));
      const rate = element('span'); rate.append(element('b', '', average(data.summary?.[period + '_every'])));
      const streak = element('span', '', integer(data.frequency?.[period]?.current_without));
      const max = data.frequency?.[period]?.max_without;
      streak.title = max === null || max === undefined ? 'Sin base suficiente' : `Máxima racha sin caso: ${integer(max)} partidos`;
      row.append(label, rate, streak); root.append(row);
    }
  }
  function renderScorelines(data) {
    const root = $('scorelines-content'); root.replaceChildren();
    ['ht', 'ft'].forEach(period => {
      const column = element('div', 'score-period'); const title = element('div', 'score-period-title', period.toUpperCase()); title.append(element('span', '', period === 'ht' ? 'DESCANSO' : 'FINAL')); column.append(title);
      const scores = data?.[period] || [];
      if (!scores.length) column.append(element('div', 'scoreline-row', 'Sin resultados'));
      scores.slice(0, 3).forEach(score => {const row = element('div', 'scoreline-row'); row.append(element('span', 'scoreline-score', score.score), element('span', 'scoreline-count', `${integer(score.count)} partidos`)); column.append(row);});
      root.append(column);
    });
  }
  function formatForecastBoundary(value) {
    if (!value) return '';
    if (typeof value === 'object' && value.date) return formatDate(value.date) + (value.time ? ` · ${value.time} COL` : '');
    const stamp = typeof value === 'object' ? value.kickoff : value;
    if (!stamp) return '';
    const date = new Date(stamp);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('es-CO', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota'}).format(date) + ' COL';
  }
  function renderForecast(data, error = '') {
    if (forecastExpiryTimer) {clearTimeout(forecastExpiryTimer); forecastExpiryTimer = null;}
    const root = $('forecast-content'); root.replaceChildren();
    const league = currentLeague();
    $('forecast-context').textContent = `${league?.name || 'Liga'} · todos los equipos · próximos encuentros con horario confirmado · hora de Colombia`;
    if (!data) {
      root.append(element('p', 'forecast-empty', error || 'No está disponible la estimación ni el calendario en esta consulta. Los resultados históricos siguen disponibles.'));
      return;
    }
    const now = Date.now(), validUntil = data.valid_until ? new Date(data.valid_until).getTime() : NaN;
    const stale = Number.isFinite(validUntil) && now >= validUntil;
    if (Number.isFinite(validUntil) && validUntil > now) forecastExpiryTimer = setTimeout(() => renderForecast(data), Math.min(validUntil - now + 100, 2147480000));
    const upcoming = (Array.isArray(data.upcoming) ? data.upcoming : []).filter(game => {
      const kickoff = game.kickoff ? new Date(game.kickoff).getTime() : NaN;
      return !Number.isFinite(kickoff) || kickoff > now;
    });
    const estimate = element('div', 'forecast-estimate');
    if (stale) estimate.append(element('p', 'forecast-schedule-note', 'La captura ya alcanzó el primer horario del calendario. Se necesita una actualización de resultados y próximos partidos antes de usar un nuevo rango.'));

    const sample = data.sample || {}, baseline = data.baseline;
    if (baseline && baseline.probability !== null && baseline.probability !== undefined) {
      const main = element('div', 'forecast-probability');
      main.append(element('span', 'forecast-label', 'REFERENCIA POR PARTIDO · AMBOS HT'), element('strong', '', percent(baseline.probability)), element('span', 'forecast-sample', `${integer(sample.hits)} casos en ${integer(sample.n)} partidos con HT conocido`));
      estimate.append(main);
      if (baseline.wilson95 && baseline.wilson95.low !== null && baseline.wilson95.high !== null) estimate.append(element('p', 'forecast-interval', `Frecuencia histórica: intervalo del 95% entre ${percent(baseline.wilson95.low)} y ${percent(baseline.wilson95.high)}.`));
      const seasons = Array.isArray(sample.seasons) ? sample.seasons.map(String).join(' · ') : '';
      if (seasons) estimate.append(element('p', 'forecast-interval', `Temporadas de referencia: ${seasons.replaceAll('-', '/')}.`));
    }
    if (data.status === 'ok' && data.range && !stale) {
      const range = data.range;
      const rangeBox = element('div', 'forecast-range');
      const rangeLabel = range.q10 !== null && range.q90 !== null ? `Partidos ${integer(range.q10)}–${integer(range.q90)}` : range.q10 !== null ? `Partidos ${integer(range.q10)}–más de ${integer(range.rank_limit || 500)}` : 'Rango sin acotar con esta muestra';
      rangeBox.append(element('span', 'forecast-label', 'SIGUIENTE CASO ENTRE ESTOS PRÓXIMOS ENCUENTROS'), element('strong', '', rangeLabel), element('p', '', range.central_probability !== null && range.central_probability !== undefined ? `El modelo asigna ${percent(range.central_probability)} a este rango; también puede ocurrir fuera.` : 'Rango central orientativo del modelo. También puede ocurrir fuera de este rango.'));
      estimate.append(rangeBox);
      const horizons = element('div', 'forecast-horizons');
      (data.horizons || []).filter(item => [3,5,10].includes(item.matches)).forEach(item => {const card = element('div'); card.append(element('b', '', percent(item.probability_at_least_one)), element('span', '', `al menos un caso en los próximos ${integer(item.matches)} partidos`)); horizons.append(card);});
      if (horizons.childElementCount) estimate.append(horizons);
      const window = range.window || {};
      const start = formatForecastBoundary(window.start), end = formatForecastBoundary(window.end);
      if (window.from_rank !== range.q10 || window.to_rank !== range.q90) {
        estimate.append(element('p', 'forecast-schedule-note', `Por partidos simultáneos, el bloque de horarios abarca los encuentros ${integer(window.from_rank)}–${integer(window.to_rank)}${window.probability_inside_group_window !== null && window.probability_inside_group_window !== undefined ? ` (${percent(window.probability_inside_group_window)} bajo el modelo)` : ''}.`));
      }
      if (window.calendar_complete && start && end) estimate.append(element('p', 'forecast-interval', `El bloque del calendario va del ${start} al ${end}.`));
      else if (window.calendar_complete === false) estimate.append(element('p', 'forecast-schedule-note', 'El calendario con hora confirmada no alcanza todo el rango del modelo. No se asigna una fecha final sin datos.'));
    } else {
      const messages = {insufficient_history: 'La muestra histórica es insuficiente para publicar una probabilidad. Se conserva el calendario disponible.', no_upcoming: 'No hay próximos partidos con fecha y hora confirmadas en esta captura.', mixed_leagues: 'Selecciona una sola liga para obtener una referencia histórica.'};
      if (!stale) estimate.append(element('p', 'forecast-empty', messages[data.status] || 'No hay una estimación disponible para este calendario.'));
    }
    estimate.append(element('p', 'forecast-caution', 'Referencia de la liga para estos próximos encuentros; excluye los que ya empezaron y los horarios sin confirmar. Una racha sin casos no obliga a que el siguiente partido sea ambos marcan.'));
    if (data.notice || sample.window || data.assumptions) {
      const method = element('details', 'forecast-method'); method.append(element('summary', '', 'Cómo se calcula esta referencia'));
      if (sample.window) method.append(element('p', '', String(sample.window)));
      if (data.assumptions) method.append(element('p', '', String(data.assumptions)));
      if (data.notice) method.append(element('p', '', String(data.notice)));
      estimate.append(method);
    }
    const calendar = element('div', 'upcoming-calendar');
    const title = element('div', 'upcoming-heading'); title.append(element('h3', '', 'Calendario próximo'), element('span', 'subtle-pill', 'HORA COL')); calendar.append(title);
    if (!upcoming.length) calendar.append(element('p', 'forecast-empty', 'Todavía no hay encuentros futuros con horario confirmado para mostrar.'));
    else {
      const list = element('ol', 'upcoming-list');
      upcoming.slice(0,20).forEach((game, index) => {
        const row = element('li');
        const position = element('span', 'upcoming-rank', integer(stale ? index + 1 : game.rank || index + 1));
        const fixture = element('div', 'upcoming-fixture'); fixture.append(element('strong', '', `${game.home} · ${game.away}`), element('span', '', `${formatDate(game.date)} · ${game.time || 'hora pendiente'} COL`));
        row.append(position, fixture); list.append(row);
      });
      calendar.append(list);
      if (upcoming.length > 20) calendar.append(element('p', 'forecast-interval', `Se muestran los primeros 20 de ${integer(upcoming.length)} encuentros con hora confirmada.`));
      const counts = new Map(); upcoming.forEach(game => {const key = game.kickoff || `${game.date}|${game.time}`; counts.set(key, (counts.get(key) || 0) + 1);});
      if ([...counts.values()].some(count => count > 1)) calendar.append(element('p', 'forecast-schedule-note', 'Hay partidos simultáneos. Su número solo organiza el listado; no indica cuál producirá el siguiente caso antes.'));
    }
    root.append(estimate, calendar);
  }
  function scoreCell(home, away, final = false) {
    const cell = element('td'); const known = home !== null && home !== undefined && away !== null && away !== undefined;
    cell.append(element('span', 'score-chip' + (final ? ' ft-score' : ''), known ? `${home}-${away}` : '—'));
    return cell;
  }
  function cardCell(value, red = false) {
    const cell = element('td', 'card-cell');
    if (value === null || value === undefined) {cell.append(element('span', 'missing-value', 'N/D')); cell.title = 'Dato pendiente'; return cell;}
    if (red && Number(value) === 0) {cell.setAttribute('aria-label', 'Sin tarjetas rojas'); return cell;}
    const span = element('span', 'card-value'); const icon = element('span', 'tiny-card' + (red ? ' red' : '')); icon.setAttribute('aria-hidden', 'true'); span.append(icon, document.createTextNode(integer(value))); cell.append(span); return cell;
  }
  function renderTeam(team) {
    const root = $('team-summary'); root.replaceChildren(); root.hidden = !team;
    if (!team) return;
    const title = element('div', 'team-summary-title'); title.append(element('strong', '', team.team), element('span', '', hasCards() ? 'Rendimiento y tarjetas propias' : 'Rendimiento del equipo')); root.append(title);
    const fields = [['PJ', integer(team.pj)], ['G / E / P', `${integer(team.wins)} / ${integer(team.draws)} / ${integer(team.losses)}`], ['GF / GC', `${integer(team.gf)} / ${integer(team.ga)}`]];
    fields.forEach(([label, value]) => {const item = element('div', 'team-stat'); item.append(element('strong', '', value), document.createTextNode(label)); root.append(item);});
    if (hasCards()) {
      [['Amarillas / PJ', team.own_yellow_average, false], ['Rojas / PJ', team.own_red_average, true]].forEach(([label, value, red]) => {const item = element('div', 'team-stat own-card' + (red ? ' red' : '')); item.append(element('strong', '', average(value)), document.createTextNode(label)); root.append(item);});
    }
  }
  function renderGames(games, pagination) {
    const root = $('matches-body'); root.replaceChildren(); const paginationData = pagination || {page: 1, page_size: state.page_size, total: 0, pages: 1};
    const total = safeCount(paginationData.total); const page = safeCount(paginationData.page) || 1; const pages = Math.max(1, safeCount(paginationData.pages));
    $('match-count').textContent = integer(total);
    if (!games?.length) {const row = element('tr'); const cell = element('td', 'table-empty', 'No hay partidos con estos filtros. Prueba otra selección o pulsa «Actualizar datos» para consultar la fuente.'); cell.colSpan = visibleColumnCount(); row.append(cell); root.append(row);}
    const statusNames = {finished: 'Finalizado', scheduled: 'Por jugar', live: 'En juego', postponed: 'Aplazado', cancelled: 'Cancelado', suspended: 'Suspendido'};
    (games || []).forEach((game, index) => {
      const row = element('tr');
      const position = (page - 1) * (paginationData.page_size || state.page_size) + index + 1;
      const numberCell = element('td', 'number-cell', integer(game.number ?? position));
      const date = element('td', 'date-cell', game.date_confirmed === false ? 'Fecha por confirmar' : formatDate(game.date));
      if (game.time && game.time_confirmed !== false) date.append(element('span', 'secondary-text', String(game.time) + ' COL'));
      else if (game.status === 'scheduled') date.append(element('span', 'secondary-text', 'Hora por confirmar'));
      const phase = element('td', 'phase-cell', game.round ? `Jornada ${game.round}` : (game.phase || '—'));
      const scopeName = {first: 'Primera vuelta', second: 'Segunda vuelta', all: ''}[game.scope] ?? game.scope;
      if (scopeName) phase.append(element('span', 'secondary-text', scopeName));
      const fixture = element('td', 'fixture-cell'); fixture.append(element('strong', '', game.home), element('span', 'versus', 'vs'), element('strong', '', game.away));
      if (game.administrative) {fixture.append(element('span', 'secondary-text', 'Resultado administrativo · fuera del análisis')); if (game.administrative_reason) fixture.title = game.administrative_reason;}
      const ht = scoreCell(game.ht_home, game.ht_away); const ft = scoreCell(game.ft_home, game.ft_away, true);
      const highlighted = Boolean(game.btts_ht) && !game.administrative;
      const greenCells = [numberCell, date, phase, fixture, ht, ft];
      if (hasCorners()) {
        const corners = element('td', 'corners-value corners-column', game.corners === null || game.corners === undefined ? 'N/D' : integer(game.corners));
        if (game.corners === null || game.corners === undefined) corners.title = 'Dato pendiente';
        greenCells.push(corners);
      }
      if (highlighted) greenCells.forEach(cell => cell.classList.add('btts-cell'));
      row.append(...greenCells);
      if (hasCards()) row.append(cardCell(game.yellow), cardCell(game.red, true));
      const status = element('td'); status.append(element('span', 'match-state ' + (game.administrative ? 'admin' : (['live', 'scheduled', 'postponed'].includes(game.status) ? game.status : '')), game.administrative ? 'Administrativo' : (statusNames[game.status] || 'Por confirmar'))); row.append(status); root.append(row);
    });
    const start = total ? (page - 1) * (paginationData.page_size || state.page_size) + 1 : 0;
    const end = total ? Math.min(total, start + (games?.length || 0) - 1) : 0;
    $('pagination-label').textContent = `${integer(start)}–${integer(end)} de ${integer(total)} partidos`;
    $('page-label').textContent = `${integer(page)} / ${integer(pages)}`;
    $('previous-page').disabled = page <= 1; $('next-page').disabled = page >= pages;
  }
  function showLoadError(error) {
    notice(error.message || 'No fue posible cargar los datos. Intenta de nuevo.', true);
    $('scope-note').hidden = true; renderForecast(null); updateHeading(); renderKPIs({}); renderOutcomes({}); renderFrequency({}); renderScorelines({}); renderTeam(null);
    const root = $('matches-body'); root.replaceChildren(); const row = element('tr'); const cell = element('td', 'table-empty', 'No se pudieron cargar los partidos. El archivo histórico sigue disponible para descargar.'); cell.colSpan = visibleColumnCount(); row.append(cell); root.append(row);
    $('previous-page').disabled = true; $('next-page').disabled = true;
    $('kpi-grid').setAttribute('aria-busy', 'false');
  }
  function renderDashboard(data) {
    dashboard = data;
    if (data.pagination?.page) state.page = data.pagination.page;
    $('scope-note').textContent = data.options?.scope_note || ''; $('scope-note').hidden = !data.options?.scope_note;
    const scopes = [{id: 'all', label: 'Temporada completa'}, ...(data.options?.scopes || []).filter(scope => scope.id !== 'all')];
    setOptions($('scope-select'), scopes, state.scope);
    setOptions($('team-select'), [{id: 'all', label: 'Todos los equipos'}, ...(data.options?.teams || []).map(team => ({id: team, label: team}))], state.team);
    updateControls(); updateHeading();
    const scopeLabel = scopes.find(scope => scope.id === state.scope)?.label || 'Temporada completa';
    $('active-filter-label').textContent = `${scopeLabel} · ${state.team === 'all' ? 'Todos los equipos' : state.team}`.toUpperCase();
    $('data-timestamp').textContent = formatTimestamp(data.updated_at || meta?.updated_at);
    renderTeam(data.team_stats); renderKPIs(data.summary); renderOutcomes(data.summary || {}); renderFrequency(data); renderScorelines(data.scorelines); renderGames(data.games, data.pagination);
    history.replaceState(null, '', '?' + query());
  }
  async function loadDashboard() {
    if (!meta || !state.season) return;
    if (controller) controller.abort();
    controller = new AbortController(); const request = ++requestNumber;
    $('matches-table').classList.add('loading-table'); $('kpi-grid').setAttribute('aria-busy', 'true');
    try {
      const results = await Promise.allSettled([
        fetchJSON('/api/dashboard?' + query(), {signal: controller.signal}),
        fetchJSON('/api/forecast?' + new URLSearchParams({league: state.league}), {signal: controller.signal})
      ]);
      if (request !== requestNumber) return;
      if (results[0].status === 'rejected') throw results[0].reason;
      renderDashboard(results[0].value);
      if (results[1].status === 'fulfilled') renderForecast(results[1].value);
      else renderForecast(null, 'No fue posible consultar el calendario y la estimación en este momento. Los datos históricos siguen disponibles.');
    }
    catch (error) {if (error.name !== 'AbortError' && request === requestNumber) showLoadError(error);}
    finally {if (request === requestNumber) {$('matches-table').classList.remove('loading-table'); $('kpi-grid').setAttribute('aria-busy', 'false');}}
  }
  async function syncData() {
    if (syncing || isOffline()) return;
    if (isHostedSnapshot()) {
      $('sync-button').disabled = true; $('sync-button').querySelector('span').textContent = 'Recargando…';
      window.location.reload(); return;
    }
    syncing = true; const button = $('sync-button'); button.disabled = true; button.classList.add('syncing'); button.querySelector('span').textContent = 'Consultando fuente…';
    const requestedLeague = state.league, requestedSeason = state.season;
    notice('Consultando los partidos de la temporada seleccionada. Puede tardar unos segundos.');
    try {
      const result = await fetchJSON('/api/sync', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({league: requestedLeague, season: requestedSeason})});
      const errors = Array.isArray(result.errors) ? result.errors : [];
      let message = result.message || `${integer(result.added)} partidos nuevos · ${integer(result.updated)} actualizados · ${integer(result.unchanged)} sin cambios.`;
      if (errors.length) message += ' ' + errors.map(error => typeof error === 'string' ? error : 'No se pudo consultar una parte de la fuente.').join(' ');
      notice(message, errors.length > 0);
      meta = await fetchJSON('/api/meta'); renderMeta(); await loadDashboard();
    } catch (error) {notice(error.message || 'No se pudo actualizar. Los datos guardados se conservan.', true);}
    finally {syncing = false; button.disabled = !meta || isOffline(); button.classList.remove('syncing'); button.querySelector('span').textContent = syncLabel();}
  }
  function initialSkeleton() {
    const root = $('kpi-grid'); root.replaceChildren();
    (state.league === 'hungary' ? ['Ambos marcan · HT', 'Ambos marcan · FT', 'Goles por partido · FT', 'Goles por partido · HT', 'Partidos por jugar', 'Partidos analizados'] : ['Ambos marcan · HT', 'Ambos marcan · FT', 'Córners por partido', 'Goles por partido · FT', 'Goles por partido · HT', 'Tarjetas', 'Frecuencia', 'Partidos analizados']).forEach((label, index) => {const card = kpi(label, '—', 'Cargando datos…', index === 0 ? 'green' : 'blue', '·', index === 0); card.classList.add('loading'); root.append(card);});
  }
  $('filters').addEventListener('submit', event => event.preventDefault());
  $('season-select').addEventListener('change', event => {state.season = event.target.value; state.scope = 'all'; state.team = 'all'; state.condition = 'all'; state.page = 1; loadDashboard();});
  [['scope-select', 'scope'], ['team-select', 'team'], ['condition-select', 'condition'], ['status-select', 'status']].forEach(([id, key]) => {
    $(id).addEventListener('change', event => {state[key] = event.target.value; if (key === 'team' && state.team === 'all') state.condition = 'all'; state.page = 1; loadDashboard();});
  });
  document.querySelectorAll('[data-order]').forEach(button => button.addEventListener('click', () => {if (button.dataset.order !== state.order) {state.order = button.dataset.order; state.page = 1; updateControls(); loadDashboard();}}));
  $('search-input').addEventListener('input', event => {clearTimeout(searchTimer); const value = event.target.value; searchTimer = setTimeout(() => {state.search = value; state.page = 1; loadDashboard();}, 300);});
  $('reset-button').addEventListener('click', () => {notice(''); resetFilters();});
  $('sync-button').addEventListener('click', syncData);
  $('previous-page').addEventListener('click', () => {if (state.page > 1) {state.page--; loadDashboard();}});
  $('next-page').addEventListener('click', () => {if (state.page < safeCount(dashboard?.pagination?.pages)) {state.page++; loadDashboard();}});
  ['export-link', 'history-link'].forEach(id => $(id).addEventListener('click', event => {if ($(id).getAttribute('aria-disabled') === 'true') event.preventDefault();}));
  initialSkeleton();
  (async () => {
    try {meta = await fetchJSON('/api/meta'); renderMeta(); updateControls(); await loadDashboard();}
    catch (error) {showLoadError(error); $('page-description').textContent = 'El archivo no está disponible por el momento. Vuelve a cargar la página.'; $('data-timestamp').textContent = 'Sin conexión con el servidor';}
  })();
})();
