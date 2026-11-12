'use strict';
(() => {
  const bundle = JSON.parse(document.getElementById('offline-bundle').textContent);
  const rows = bundle.games;
  const originalPosition = new Map(rows.map((game,index)=>[game.id,index]));
  const metadata = bundle.meta;
  metadata.offline = metadata.mode !== 'hosted_snapshot';
  metadata.auto_update = metadata.mode === 'hosted_snapshot';
  const mean = a => a.length ? a.reduce((s,n) => s+n, 0)/a.length : null;
  const sum = a => a.reduce((s,n) => s+n, 0);
  const pair = (g,p) => [g[p+'_home'],g[p+'_away']];
  const known = a => a.every(n => Number.isInteger(n) && n >= 0);
  const btts = a => known(a) && Math.min(...a) > 0;
  const rate = (n,b) => b ? n/b : null;
  const present = v => v !== null && v !== undefined;
  const capabilities = league => metadata.leagues?.find(item => item.id === league);
  const hasCorners = league => capabilities(league)?.corners !== false;
  const hasCards = league => Boolean(capabilities(league)?.cards);
  const roundId = game => JSON.stringify([game.scope || 'all', game.phase || '', game.round === '' || game.round === undefined ? null : game.round]);
  function roundLabel(game) {
    const scope = ({all:'',first:'Primera vuelta',second:'Segunda vuelta'})[game.scope] ?? (game.scope || '');
    const context = scope ? [scope] : [];
    if (game.phase && !['LaLiga','NB I','Ekstraklasa','Liga BetPlay'].includes(game.phase) && !context.includes(game.phase)) context.push(game.phase);
    context.push(game.round === null || game.round === undefined || game.round === '' ? 'Jornada pendiente' : `Jornada ${game.round}`);
    return context.join(' · ');
  }
  function roundOptions(games) {
    const options = new Map();
    games.slice().sort((a,b)=>(a.source_order || 0)-(b.source_order || 0) || originalPosition.get(a.id)-originalPosition.get(b.id)).forEach(game=>{
      const id = roundId(game);
      if (!options.has(id)) options.set(id,{id,label:roundLabel(game),count:0});
      options.get(id).count++;
    });
    return [...options.values()];
  }
  function selectRound(games,selected,options=roundOptions(games)) {
    if (!selected) return {games,selected:''};
    const id = options.some(item=>item.id===selected) ? selected : options[0]?.id || '';
    return {games:games.filter(game=>roundId(game)===id),selected:id};
  }
  function observedDroughts(games,metric='ht') {
    const htNote = 'Orden cronológico de los partidos finalizados de la selección. Los HT pendientes interrumpen la verificación. Los partidos simultáneos se agrupan; si falta una hora, se agrupa ese día. En bloques con algún ambos marcan, los demás partidos no se asignan a una racha. «Mínimo» indica un tramo verificado cuyo límite exacto es incierto.';
    const note = metric==='ft' ? htNote.replace('Los HT pendientes','Los FT pendientes') : metric==='red' ? htNote.replace('Los HT pendientes','Los datos de tarjetas rojas pendientes').replace('con algún ambos marcan','con alguna tarjeta roja') : htNote;
    const dateKnown = game=>Boolean(game.date) && game.date_confirmed!==false;
    const timeKnown = game=>Boolean(game.time) && game.time_confirmed!==false;
    const ref = game=>Object.fromEntries(['id','date','round','scope','phase','home','away','time'].map(key=>[key,key==='time' ? (timeKnown(game) ? game.time : null) : game[key] ?? null]));
    const compare = (a,b)=>a<b ? -1 : a>b ? 1 : 0;
    const keyCompare = (a,b)=>compare(a.date || '',b.date || '') || compare(timeKnown(a) ? a.time : '',timeKnown(b) ? b.time : '') || compare(String(a.id || ''),String(b.id || ''));
    const observed = game=>metric==='red' ? Number.isInteger(game.red) && game.red>=0 : known(pair(game,metric));
    const isCase = game=>metric==='red' ? observed(game) && game.red>0 : btts(pair(game,metric));
    const played = games.filter(game=>game.status==='finished' && !game.administrative);
    const missing = played.filter(game=>!observed(game)).length;
    const result = {top:[],current:null,[metric==='ht' ? 'missing_ht' : 'missing_data']:missing,uncertain_order:played.filter(game=>!dateKnown(game) || !timeKnown(game)).length,eligible:played.length-missing,mixed_blocks:0,note};
    if (played.some(game=>!dateKnown(game))) {
      result.note = 'No se pueden certificar rachas continuas: hay partidos finalizados sin fecha confirmada. '+note;
      return result;
    }
    const byDate = new Map(), blocks=[];
    played.forEach(game=>{if (!byDate.has(game.date)) byDate.set(game.date,[]);byDate.get(game.date).push(game);});
    [...byDate.keys()].sort().forEach(day=>{
      const dayGames=byDate.get(day).slice().sort(keyCompare);
      if (dayGames.some(game=>!timeKnown(game))) blocks.push(dayGames);
      else {
        const times=new Map();
        dayGames.forEach(game=>{if(!times.has(game.time))times.set(game.time,[]);times.get(game.time).push(game);});
        [...times.keys()].sort().forEach(time=>blocks.push(times.get(time)));
      }
    });
    const spells=[];let active=[],leftUncertain=false;
    function finish(state,{endedBy=null,interruptedBy=null,rightUncertain=false}={}) {
      if (!active.length) return null;
      const rounds=[],seen=new Set();
      active.forEach(game=>{
        const key=JSON.stringify([game.round ?? null,game.scope ?? null,game.phase ?? null]);
        if (!seen.has(key)) {seen.add(key);rounds.push({round:game.round ?? null,scope:game.scope ?? null,phase:game.phase ?? null});}
      });
      const spell={count:active.length,start:ref(active[0]),end:ref(active.at(-1)),ended_by:endedBy ? ref(endedBy) : null,state,rounds,interrupted_by:interruptedBy ? ref(interruptedBy) : null,lower_bound:Boolean(leftUncertain || rightUncertain)};
      spells.push(spell);active=[];return spell;
    }
    blocks.forEach(block=>{
      const unknown=block.filter(game=>!observed(game)),hits=block.filter(isCase);
      if (hits.length && hits.length!==block.length) result.mixed_blocks++;
      if (unknown.length) {finish('interrupted',{interruptedBy:unknown[0],rightUncertain:true});leftUncertain=true;}
      else if (hits.length) {const mixed=hits.length!==block.length;finish('closed',{endedBy:hits[0],rightUncertain:mixed});leftUncertain=mixed;}
      else active.push(...block);
    });
    result.current=finish('current');
    result.top=spells.sort((a,b)=>b.count-a.count || compare(a.start.date,b.start.date) || compare(a.start.time || '',b.start.time || '') || compare(String(a.start.id),String(b.start.id))).slice(0,3);
    return result;
  }
  const htDroughts = games=>observedDroughts(games,'ht');
  function frequency(games,p) {
    const hits = games.map((g,i) => btts(pair(g,p)) ? i+1 : 0).filter(Boolean);
    const boundaries = [0,...hits,games.length+1];
    return {max_without: games.length ? Math.max(...boundaries.slice(1).map((x,i) => x-boundaries[i]-1)) : null, current_without: games.length ? games.length-(hits.at(-1) || 0) : null, mean_gap:mean(hits.slice(1).map((x,i) => x-hits[i]))};
  }
  function summarize(games, team) {
    const eligible = games.filter(g => g.status === 'finished' && !g.administrative);
    const ht = eligible.filter(g => known(pair(g,'ht'))), ft = eligible.filter(g => known(pair(g,'ft')));
    const h = ht.filter(g => btts(pair(g,'ht'))).length, f = ft.filter(g => btts(pair(g,'ft'))).length;
    const corners = ft.map(g=>g.corners).filter(present), yellow=ft.map(g=>g.yellow).filter(present), red=ft.map(g=>g.red).filter(present);
    const outcomes = {1:0,X:0,2:0};ft.forEach(g=>outcomes[g.ft_home>g.ft_away ? '1' : g.ft_home<g.ft_away ? '2' : 'X']++);
    const unresolved = eligible.filter(g => !known(pair(g,'ht')) || !known(pair(g,'ft')) || (hasCorners(g.league) && !present(g.corners)) || (hasCards(g.league) && (!present(g.yellow) || !present(g.red)))).length;
    const s={total:games.length,finished:ft.length,scheduled:games.filter(g=>g.status==='scheduled').length,live:games.filter(g=>g.status==='live').length,excluded:games.filter(g=>g.administrative).length,ht_base:ht.length,ht_cases:h,ht_rate:rate(h,ht.length),ht_every:h?ht.length/h:null,ft_base:ft.length,ft_cases:f,ft_rate:rate(f,ft.length),ft_every:f?ft.length/f:null,goals_ht_average:mean(ht.map(g=>sum(pair(g,'ht')))),goals_ft_average:mean(ft.map(g=>sum(pair(g,'ft')))),corners_average:mean(corners),corners_base:corners.length,corners_missing:games.some(g=>hasCorners(g.league))?ft.length-corners.length:0,yellow_average:mean(yellow),yellow_base:yellow.length,red_average:mean(red),red_base:red.length,red_match_rate:rate(red.filter(n=>n>0).length,red.length),red_matches:red.filter(n=>n>0).length,yellow_total:yellow.length?sum(yellow):null,red_total:red.length?sum(red):null,home_wins:outcomes[1],draws:outcomes.X,away_wins:outcomes[2],unresolved};
    const scorelines={};
    for(const [p,items] of [['ht',ht],['ft',ft]]){
      const counts=new Map();items.forEach(g=>{const key=pair(g,p).join('-');counts.set(key,(counts.get(key)||0)+1);});
      scorelines[p]=[...counts].map(([score,count])=>({score,count})).sort((a,b)=>b.count-a.count || Number(a.score.split('-')[0])-Number(b.score.split('-')[0]) || Number(a.score.split('-')[1])-Number(b.score.split('-')[1])).slice(0,3);
    }
    let team_stats=null;
    if(team!=='all'){
      const pairs=ft.map(g=>g.home===team?[g.ft_home,g.ft_away]:[g.ft_away,g.ft_home]);
      const ownY=ft.map(g=>g[g.home===team?'yellow_home':'yellow_away']).filter(present),ownR=ft.map(g=>g[g.home===team?'red_home':'red_away']).filter(present);
      team_stats={team,pj:ft.length,wins:pairs.filter(([a,b])=>a>b).length,draws:pairs.filter(([a,b])=>a===b).length,losses:pairs.filter(([a,b])=>a<b).length,gf:sum(pairs.map(a=>a[0])),ga:sum(pairs.map(a=>a[1])),own_yellow_average:mean(ownY),own_red_average:mean(ownR)};
    }
    return {summary:s,frequency:{ht:frequency(ht,'ht'),ft:frequency(ft,'ft')},scorelines,team_stats};
  }
  function filtered(params){
    const league=params.get('league')||'spain',season=params.get('season')||'2026-27',scope=params.get('scope')||'all',team=params.get('team')||'all',condition=params.get('condition')||'all',status=params.get('status')||'all',search=(params.get('search')||'').toLocaleLowerCase().trim();
    let all=rows.filter(g=>g.league===league&&g.season===season);
    if(params.get('order')==='chronological') {
      const key=g=>[!(g.date&&g.date_confirmed!==false),g.date&&g.date_confirmed!==false?g.date:'',!(g.time&&g.time_confirmed!==false),g.time&&g.time_confirmed!==false?g.time:'',g.source_order||0,g.id];
      all=all.slice().sort((a,b)=>{const x=key(a),y=key(b);for(let i=0;i<x.length;i++){if(x[i]<y[i])return -1;if(x[i]>y[i])return 1;}return 0;});
    }
    const games=all.filter(g=>(scope==='all'||g.scope===scope)&&(team==='all'||((g.home===team||g.away===team)&&(condition==='all'||g[condition==='home'?'home':'away']===team)))&&(status==='all'||g.status===status)&&(!search||(g.home+' '+g.away).toLocaleLowerCase().includes(search)));
    return {all,games,league,season,team};
  }
  function dashboard(params){
    const {all,games,league,team}=filtered(params);const scopeValues=[...new Set(all.map(g=>g.scope).filter(s=>s&&s!=='all'))];
    scopeValues.sort((a,b)=>Number(a.includes('Finalización'))-Number(b.includes('Finalización'))||(a<b?-1:a>b?1:0));
    const scopes=league==='spain'?[{id:'all',label:'Temporada completa'},{id:'first',label:'Primera vuelta'},{id:'second',label:'Segunda vuelta'}]:[{id:'all',label:'Temporada completa'},...scopeValues.map(s=>({id:s,label:s}))];
    const teams=[...new Set(all.flatMap(g=>[g.home,g.away]))].sort();const missing=league==='spain'?all.filter(g=>g.round===null||g.round===undefined).length:0;
    const rounds=roundOptions(games), selection=selectRound(games,params.get('match_round'),rounds), matches=selection.games;
    const page_size=Math.min(200,Math.max(1,Number(params.get('page_size'))||40)),pages=Math.max(1,Math.ceil(matches.length/page_size)),page=Math.min(pages,Math.max(1,Number(params.get('page'))||1));
    const visible=matches.slice((page-1)*page_size,page*page_size).map(g=>({...g,btts_ht:g.status==='finished'&&!g.administrative&&btts(pair(g,'ht'))}));
    const ht_droughts=htDroughts(games);
    return {...summarize(games,team),ht_droughts,droughts:{ht:ht_droughts,ft:observedDroughts(games,'ft'),red:league==='spain' ? observedDroughts(games,'red') : null},options:{scopes,teams,scope_note:missing?`La fuente no informa la jornada de ${missing} partidos; se agrupan como «Jornada pendiente» hasta poder verificarla.`:null,rounds,selected_round:selection.selected},games:visible,pagination:{page,page_size,total:matches.length,pages},updated_at:metadata.updated_at};
  }
  window.__offlineDashboard=dashboard;
  window.__offlineFilters=new URLSearchParams();
  window.fetch=async (input,options={})=>{
    if(options.signal?.aborted) throw new DOMException('Aborted','AbortError');
    const url=new URL(String(input),'http://offline.invalid');
    let body,status=200;
    if(url.pathname==='/api/meta')body=metadata;
    else if(url.pathname==='/api/dashboard'){window.__offlineFilters=new URLSearchParams(url.search);body=dashboard(url.searchParams);}
    else if(url.pathname==='/api/forecast'){body=bundle.forecasts?.[url.searchParams.get('league')||'spain']||{status:'no_upcoming',upcoming:[],notice:'Esta captura no contiene una estimación del calendario.'};}
    else if(url.pathname==='/api/sync'){status=503;body={detail:'Esta copia funciona sin conexión. Para incorporar resultados nuevos se necesita la versión con servidor.'};}
    else {status=404;body={detail:'Esta acción no está disponible en la copia sin conexión.'};}
    return new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
  };
  function download(blob,name){const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}
  function csvCell(value){let text=value===null||value===undefined?'':String(value);if(/^[\t\r\n ]*[=+@-]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';}
  document.addEventListener('click',event=>{
    const link=event.target.closest('a');if(!link)return;
    if(link.id==='history-link'){
      event.preventDefault();const key=window.__offlineFilters.get('league')||'spain',file=bundle.workbooks[key];
      if(!file){const note=document.getElementById('notice');note.textContent='El archivo histórico de esta liga todavía no está disponible en esta copia.';note.className='notice';note.hidden=false;return;}
      const bytes=Uint8Array.from(atob(file.base64),c=>c.charCodeAt(0));download(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),file.name);
    }else if(link.id==='export-link'){
      event.preventDefault();const selection=filtered(window.__offlineFilters),{league,season}=selection;
      const {games}=selectRound(selection.games,window.__offlineFilters.get('match_round'));
      const headers=['N.º','Fecha COL','Hora COL','Jornada','Local','Visitante','HT','FT',...(hasCorners(league)?['Córners']:[]),...(hasCards(league)?['Amarillas','Rojas']:[]),'Torneo / vuelta','Fase','Estado'];
      const csv=[headers];const names={finished:'Finalizado',scheduled:'Programado',live:'En juego',postponed:'Aplazado',cancelled:'Cancelado'};
      games.forEach((g,i)=>{
        const score=p=>known(pair(g,p))?pair(g,p).join('-'):g.status==='finished'?'N/D':'';
        const observation=v=>present(v)?v:g.status==='finished'?'N/D':'';
        csv.push([i+1,g.date_confirmed===false?'Fecha pendiente':g.date,g.time_confirmed===false?'Hora pendiente':g.time,g.round,g.home,g.away,score('ht'),score('ft'),...(hasCorners(league)?[observation(g.corners)]:[]),...(hasCards(league)?[observation(g.yellow),g.red===0?'':observation(g.red)]:[]),({first:'Primera vuelta',second:'Segunda vuelta',all:'Temporada'})[g.scope]||g.scope,g.phase,g.administrative?'Administrativo':names[g.status]||g.status]);
      });
      download(new Blob(['\ufeff'+csv.map(row=>row.map(csvCell).join(';')).join('\r\n')],{type:'text/csv;charset=utf-8'}),`MisLigas_${league}_${season}_filtrado.csv`);
    }else if(link.classList.contains('brand')){event.preventDefault();window.scrollTo({top:0,behavior:'smooth'});}
  },true);
})();
