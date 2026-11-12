'use strict';
/* This is a real read-only checkpoint UI + allowlisted small-scenario runner. */
(() => {
  const M = window.DockV5Model;
  const $ = id => document.getElementById(id);
  let currentRun = null, currentWindow = null, polling = null, chartInstances = [];
  const notice = (v, error=false) => { $('notice').textContent = v; $('notice').style.color = error ? '#ac2d40' : '#43556b'; };
  async function api(path, options) {
    const r = await fetch(path, Object.assign({headers:{Accept:'application/json'}}, options || {}));
    let data;
    try {data = await r.json();} catch (_) {throw Error(`HTTP ${r.status}: invalid JSON`);}
    if (!r.ok) throw Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  const money = M.cost, safe = M.safe;
  function kpi(label, value) {
    return `<div class="kpi"><div class="kpi-label">${safe(label)}</div><div class="kpi-value">${safe(value)}</div></div>`;
  }
  async function refreshRuns(selectRun) {
    const results = await api('/api/v5-preview/runs');
    const current = selectRun || $('run').value;
    $('run').innerHTML = '<option value="">실행 결과 선택</option>' +
      results.runs.map(r => `<option value="${safe(r.run_id)}">${safe(r.run_id)} · ${safe(r.status)}</option>`).join('');
    if ([...$('run').options].some(o => o.value === current)) $('run').value = current;
    if (!results.configured) notice('실행 결과 폴더가 아직 없습니다. 입력 및 실행 시나리오를 설정하세요.');
    return results;
  }
  async function refreshScenarios() {
    const data = await api('/api/v5-preview/scenarios');
    $('scenario').innerHTML = data.scenarios.length ? data.scenarios.map(s =>
      `<option value="${safe(s)}">${safe(s)}</option>`).join('') : '<option value="">시나리오 미설정</option>';
    $('execute').disabled = !data.execution_enabled;
    if (!data.execution_enabled) notice('관리자가 검증된 V5 입력 및 운영 정책을 구성해야 실행할 수 있습니다. 저장 결과 조회는 가능합니다.');
  }
  async function selectRun(id) {
    currentRun = id; currentWindow = null;
    if (!id) { $('preview').hidden = true; return; }
    const summary = await api('/api/v5-preview/runs/' + encodeURIComponent(id));
    $('window').innerHTML = '<option value="">창 선택</option>' + summary.windows.map(w =>
      `<option value="${w.window_id}">Window ${w.window_id} · ${safe(M.local(w.as_of_at))}</option>`).join('');
    notice(`실행 ${id} · 검증된 계획창 ${summary.validated_window_count}/${summary.requested_windows} · ` +
      `상태 ${summary.status} · 전체 140항차 완료 여부 ${summary.all_scope_calls_completed === true ? '예' : '미확인/아니오'}`);
    if (summary.windows.length) {
      $('window').value = String(summary.windows[0].window_id);
      await selectWindow(Number($('window').value));
    } else $('preview').hidden = true;
  }
  function renderKpis(w) {
    const history = M.improvement(w.candidate_improvement_history);
    const first = history[0] && history[0].exactCents;
    const best = history.length ? history[history.length-1].bestCents : null;
    let improvement = '미확정';
    if (first !== null && first !== undefined && best !== null && best !== undefined && Number(first) > 0)
      improvement = `${(100 * (first - best) / first).toFixed(2)}%`;
    $('kpis').innerHTML = kpi('선박 수', String(w.selected_schedule.length) + '척') +
      kpi('계획창 예상 체선료', money(w.summary.window_demurrage_cents)) +
      kpi('비용 개선', improvement) +
      kpi('최적성', M.proof(w)) +
      kpi('물리/재고/정산', Object.values(w.validation_results).every(s => ['VALID','SETTLED','NO_WORK'].includes(s)) ? '검증됨' : '미검증');
  }
  function renderGantt(w) {
    const start = w.as_of_at, end = w.planning_horizon_end;
    const byBerth = new Map();
    for (const row of w.selected_schedule) {
      if (!byBerth.has(row.berth_id)) byBerth.set(row.berth_id, []);
      byBerth.get(row.berth_id).push(row);
    }
    const ticks = ['0h','12h','24h','36h','48h','60h','72h'];
    let html = '<div class="gantt"><div class="ticks"><span>선석</span><div class="tickscale">' +
      ticks.map(t => `<span>${t}</span>`).join('') + '</div></div>';
    for (const berth of [...byBerth.keys()].sort()) {
      html += `<div class="berth-row"><span class="berth-name">${safe(berth)}</span><div class="track"><div class="boundary"></div>`;
      for (const r of byBerth.get(berth)) {
        const parts = M.segments(r, start, end);
        const base = parts.find(p => p.kind === 'occupancy');
        for (const s of parts) {
          html += `<div class="segment ${safe(s.kind)}" style="left:${s.leftPercent.toFixed(5)}%;width:${s.widthPercent.toFixed(5)}%"></div>`;
        }
        if (base) html += `<button class="vessel-label" data-call="${safe(r.call_id)}" style="left:${Math.min(92,base.leftPercent).toFixed(4)}%">${safe(r.call_id)}</button>`;
      }
      html += '</div></div>';
    }
    html += '</div>';
    $('gantt').innerHTML = byBerth.size ? html : '<div class="empty">선택된 선박 일정 없음</div>';
    for (const b of $('gantt').querySelectorAll('[data-call]')) {
      b.addEventListener('click', () => {
        const r = w.selected_schedule.find(row => row.call_id === b.dataset.call);
        const call = w.call_metadata.find(c => c.call_id === b.dataset.call) || {};
        const price = w.vessel_demurrage_breakdown[b.dataset.call] || {};
        const fields = [
          ['선박', call.vessel_id], ['항차', r.call_id], ['선석', r.berth_id],
          ['화물', [call.cargo_group, call.direction].filter(Boolean).join(' / ')],
          ['ETA', M.local(call.eta_at)], ['ETB', M.local(r.etb_at)],
          ['작업 시작', M.local(r.work_start_at)], ['작업 종료', M.local(r.work_end_at)],
          ['ETU', M.local(r.etu_at)], ['선석 해제', M.local(r.physical_clearance_at)],
          ['예상 체선료', money(price.demurrage_cents)]
        ];
        $('selected-detail').innerHTML = '<strong>선박 상세</strong><table><tbody>' + fields.map(([k,v]) =>
          `<tr><th>${safe(k)}</th><td>${safe(v == null ? '—' : v)}</td></tr>`).join('') + '</tbody></table>';
      });
    }
  }
  function clearCharts() {
    for (const chart of chartInstances) try {chart.dispose();} catch (_) {}
    chartInstances = [];
  }
  function renderTanks(w) {
    clearCharts();
    const values = w.tank_inventory || {};
    const ids = Object.keys(values).sort();
    if (!ids.length) { $('tanks').innerHTML = '<div class="empty">검증된 탱크 재고가 없습니다.</div>'; return; }
    const refs = Object.fromEntries((w.tank_boundaries || []).map(t => [t.tank_id, t]));
    $('tanks').innerHTML = ids.map((id, i) => {
      const p = refs[id] || {};
      return `<div class="tank-box"><b>${safe(p.cargo_group || id)} · ${safe(id)}</b>` +
        `<div class="meta">최소 ${safe(p.min_inventory_bbl == null ? '미제공' : p.min_inventory_bbl)} / ` +
        `용량 ${safe(p.capacity_bbl == null ? '미제공' : p.capacity_bbl)} bbl</div>` +
        `<div id="tank-chart-${i}" class="chart"></div><div id="tank-table-${i}" class="meta"></div></div>`;
    }).join('');
    ids.forEach((id,i) => {
      const series = M.validatedTankSeries(w, id);
      const ref = refs[id] || {};
      if (!series) { $(`tank-table-${i}`).textContent = '검증된 재고 시계열을 읽을 수 없습니다.'; return; }
      if (!window.echarts) {
        $(`tank-table-${i}`).textContent = series.map(p => `${M.local(p.at)}: ${p.exact} bbl`).join(' | ');
        return;
      }
      const max = Number(ref.capacity_bbl), min = Number(ref.min_inventory_bbl);
      const chart = echarts.init($(`tank-chart-${i}`)); chartInstances.push(chart);
      const markLines = [];
      if (ref.capacity_bbl != null && Number.isFinite(max)) markLines.push({name:'최대 용량', yAxis:max});
      if (ref.min_inventory_bbl != null && Number.isFinite(min)) markLines.push({name:'최소 재고', yAxis:min});
      chart.setOption({animation:false, grid:{left:66,right:24,top:18,bottom:45},
        tooltip:{trigger:'axis',formatter:params => {
          const datum = params && params[0] && params[0].value;
          const t = datum && datum[0];
          const exact = series.find(p=>p.at===t) || series.find(p=>Date.parse(p.at)===Date.parse(t));
          return exact ? `${M.safe(M.local(exact.at))}<br/>${M.safe(exact.exact)} bbl` : '값 없음';
        }},
        xAxis:{type:'time',axisLabel:{formatter:v=>M.local(new Date(v).toISOString()).slice(5,16)}},
        yAxis:{type:'value',name:'bbl',scale:true},
        series:[{type:'line',step:'end',name:id,data:series.map(p=>[p.at,p.numeric]),
          symbol:'none',lineStyle:{width:2},markLine:{silent:true,symbol:'none',data:markLines}}]});
      const exact = document.createElement('div'); exact.className = 'meta';
      exact.textContent = `원본 재고점 ${series.length}개 · 최초 ${series[0].exact} bbl · 마지막 ${series[series.length-1].exact} bbl`;
      $(`tank-table-${i}`).appendChild(exact);
      // Exact numbers are not reconstructed from chart floats.
      chart.on('updateAxisPointer', event => {
        const index = event.axesInfo && event.axesInfo[0] && event.axesInfo[0].value;
        const near = series.reduce((a,b) => !a || Math.abs(Date.parse(b.at)-index) < Math.abs(Date.parse(a.at)-index) ? b : a, null);
        if (near) exact.textContent = `정확 값 ${M.local(near.at)} · ${near.exact} bbl`;
      });
    });
  }
  function renderHistory(w) {
    const history = M.improvement(w.candidate_improvement_history);
    $('cost-history').innerHTML = history.length ? '<table><thead><tr><th>Round</th><th>ETB 후보</th><th>검증 비용</th><th>최선 비용</th><th>상태</th></tr></thead><tbody>' +
      history.map(r => `<tr><td>${safe(r.round)}</td><td>${safe(r.candidates)}</td>` +
      `<td>${money(r.exactCents)}</td><td>${money(r.bestCents)}</td><td>${safe(r.solverStatus)}</td></tr>`).join('') +
      '</tbody></table>' : '<div class="empty">비용 개선 이력이 저장되지 않았습니다.</div>';
  }
  function renderRolling(w) {
    $('carryover').innerHTML = '<table><thead><tr><th>탱크</th><th>24시간 후 재고 (bbl)</th></tr></thead><tbody>' +
      Object.entries(w.carried_inventory || {}).map(([id,quantity]) =>
        `<tr><td>${safe(id)}</td><td>${safe(quantity)}</td></tr>`).join('') + '</tbody></table>' +
      `<p class="meta">계획창의 예상 체선료는 서로 겹칩니다. 창별 비용을 합산한 값은 전체 정산이 아닙니다.</p>`;
    $('checks').innerHTML = '<table><thead><tr><th>검증</th><th>결과</th></tr></thead><tbody>' +
      Object.entries(w.validation_results || {}).map(([key,value]) =>
        `<tr><td>${safe(key)}</td><td>${safe(value)}</td></tr>`).join('') + '</tbody></table>' +
      `<p class="meta">구조적 Checkpoint 검증이며, 엔진의 물리·재고·정산 독립 재생 테스트를 대체하지 않습니다.</p>`;
  }
  async function selectWindow(number) {
    if (!currentRun || !number) return;
    const w = await api('/api/v5-preview/runs/' + encodeURIComponent(currentRun) + '/windows/' + number);
    currentWindow = w;
    $('preview').hidden = false;
    renderKpis(w); renderGantt(w); renderTanks(w); renderHistory(w); renderRolling(w);
    $('raw').textContent = JSON.stringify(w, null, 2);
    notice(`Window ${number} · ${M.local(w.as_of_at)} · ${M.proof(w)} · 구조적 Checkpoint 무결성 확인`);
  }
  async function poll(jobId) {
    if (polling) clearTimeout(polling);
    const job = await api('/api/v5-preview/jobs/' + encodeURIComponent(jobId));
    if (job.status === 'RUNNING') {
      notice(`${jobId} 실행 중 · 실제 최적화 계산을 수행합니다.`);
      polling = setTimeout(() => poll(jobId).catch(e => notice(e.message,true)), 1600);
    } else {
      $('execute').disabled = false;
      await refreshRuns(jobId);
      if (job.status === 'COMPLETED') { $('run').value = jobId; await selectRun(jobId); }
      else notice(`${jobId} 실행 실패: ${job.reason || '사유 미상'}`, true);
    }
  }
  $('execute').addEventListener('click', async () => {
    $('execute').disabled = true;
    try {
      const body = {scenario:$('scenario').value, vessels:Number($('vessels').value), windows:Number($('windows').value)};
      const r = await api('/api/v5-preview/start', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      await poll(r.run_id);
    } catch (e) { notice(e.message, true); $('execute').disabled = false; }
  });
  $('refresh').addEventListener('click', () => refreshRuns().catch(e => notice(e.message,true)));
  $('run').addEventListener('change', () => selectRun($('run').value).catch(e => notice(e.message,true)));
  $('window').addEventListener('change', () => selectWindow(Number($('window').value)).catch(e => notice(e.message,true)));
  Promise.all([refreshRuns(),refreshScenarios()]).catch(e => notice(e.message,true));
})();
