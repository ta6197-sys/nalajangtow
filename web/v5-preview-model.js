/* Pure, testable V5 preview transformations. Never create missing data. */
(function (root, factory) {
  const model = factory();
  if (typeof module === 'object' && module.exports) module.exports = model;
  root.DockV5Model = model;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const safe = value => String(value == null ? '' : value).replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  function cost(cents) {
    if (cents === null || cents === undefined || !Number.isSafeInteger(Number(cents))) return '미확정';
    return new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(Number(cents) / 100);
  }
  function local(value) {
    if (!value || Number.isNaN(Date.parse(value))) return '—';
    return new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul', year:'numeric', month:'2-digit',
      day:'2-digit', hour:'2-digit', minute:'2-digit'}).format(new Date(value));
  }
  function proof(window) {
    const status = window && window.summary && window.summary.solver_status;
    const final = window && window.final_candidate_domain;
    return final && final.optimality_proven === true && window.summary.final_domain_optimality_proven === true
      && (status === 'OPTIMAL') ? '생성된 후보 집합 내 최적' : '유효 일정 · 최적성 미증명';
  }
  function segments(row, start, end) {
    const lo = Date.parse(start), hi = Date.parse(end), width = hi - lo;
    if (!Number.isFinite(width) || width <= 0) return [];
    const clip = (a, b, kind) => {
      const x = Date.parse(a), y = Date.parse(b);
      if (!Number.isFinite(x) || !Number.isFinite(y) || y <= x) return null;
      const left = Math.max(lo, x), right = Math.min(hi, y);
      if (right <= left) return null;
      return {kind, leftPercent: 100 * (left - lo) / width, widthPercent: 100 * (right - left) / width};
    };
    const out = [];
    const berthStart = row.berthing_start_at || row.pob_at || row.etb_at;
    const berthEnd = row.physical_clearance_at || row.etu_at;
    const occupancy = clip(berthStart, berthEnd, 'occupancy');
    if (occupancy) out.push(occupancy);
    const hose = clip(row.hose_connection_start_at || row.etb_at, row.work_start_at, 'hose');
    if (hose) out.push(hose);
    for (const pair of (row.work_intervals || [])) {
      let piece = null;
      if (Array.isArray(pair)) piece = clip(pair[0], pair[1], 'work');
      else if (pair && typeof pair === 'object') piece = clip(pair.start_at, pair.end_at, 'work');
      if (piece) out.push(piece);
    }
    const arrival = clip(row.berthing_start_at, row.etb_at, 'berthing');
    if (arrival) out.push(arrival);
    const disconnect = clip(row.work_end_at, row.hose_disconnect_end_at, 'disconnect');
    if (disconnect) out.push(disconnect);
    const switching = clip(row.switching_start_at, row.switching_end_at, 'switching');
    if (switching) out.push(switching);
    const unberth = clip(row.unberthing_start_at, row.etu_at, 'unberth');
    if (unberth) out.push(unberth);
    return out;
  }
  function chartNumber(exact) {
    if (typeof exact !== 'string' && typeof exact !== 'number') return NaN;
    const raw = String(exact).trim();
    if (/^[+-]?\d+\/\d+$/.test(raw)) {
      const [num, den] = raw.split('/').map(Number);
      return den === 0 ? NaN : num / den;
    }
    return raw && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw) ? Number(raw) : NaN;
  }
  function validatedTankSeries(window, tankId) {
    const raw = (window && window.tank_inventory || {})[tankId];
    if (!Array.isArray(raw)) return null;
    const out = raw.map(p => ({at: p.at, exact: String(p.inventory_bbl), numeric: chartNumber(p.inventory_bbl)}));
    return out.every(p => p.at && Number.isFinite(Date.parse(p.at)) && Number.isFinite(p.numeric)) ? out : null;
  }
  function improvement(history) {
    if (!Array.isArray(history)) return [];
    let best = null;
    return history.map(h => {
      const cost = h.validated_cost_cents;
      if (cost !== null && cost !== undefined && Number.isSafeInteger(Number(cost)))
        best = best === null ? Number(cost) : Math.min(best, Number(cost));
      return {round: h.round, candidates: h.candidate_count, exactCents: cost, bestCents: best,
        solverStatus: h.solver_status};
    });
  }
  return {safe, cost, local, proof, segments, validatedTankSeries, improvement, chartNumber};
});
