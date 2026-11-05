/* Stage 2 standalone ECharts renderer; production wiring pending.
 * Requires vetted local ECharts bundle (window.echarts). No CDN included.
 * Does NOT compute financial statistics; uses Python-produced JSON.
 */
(function (root) {
  'use strict';
  const instances = new Map();
  function mount(id) {
    if (!root.echarts) throw new Error('Install/vendor a compatible ECharts bundle first');
    const el = root.document.getElementById(id);
    if (!el) throw new Error(`Missing chart element #${id}`);
    let instance = instances.get(id);
    if (!instance) {
      instance = root.echarts.init(el);
      instances.set(id, instance);
    }
    return instance;
  }
  function dollars(cents) {
    if (cents === null || cents === undefined) return null;
    const n = Number(cents);
    if (!Number.isFinite(n)) throw new Error('Nonfinite financial value');
    return n / 100;
  }
  function riskBar(elementId, report) {
    if (!report || !report.risk_by_candidate) throw new Error('Missing evaluated risk report');
    const rows = Object.entries(report.risk_by_candidate);
    mount(elementId).setOption({
      tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '—' : '$' + Number(v).toLocaleString() },
      legend: { top: 0 },
      grid: { left: 74, right: 25, top: 40, bottom: 35 },
      xAxis: { type: 'category', data: rows.map(([label]) => label) },
      yAxis: { type: 'value', name: 'USD', scale: true },
      series: [
        { name: '기대 체선료', type: 'bar', data: rows.map(([, r]) => dollars(r.expected_cost_cents)) },
        { name: 'CVaR 95%', type: 'bar', data: rows.map(([, r]) => dollars(r.cvar_cents)) },
      ],
    }, true);
  }
  function vesselWaterfall(elementId, tradeoff, label) {
    const selected = tradeoff?.comparisons?.find(row => row.label === label);
    if (!selected) throw new Error(`Candidate ${label} not found`);
    const rows = [...selected.vessel_deltas].sort((a,b) => Math.abs(b.cost_delta_cents)-Math.abs(a.cost_delta_cents));
    mount(elementId).setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 90, right: 20, top: 35, bottom: 70 },
      xAxis: { type: 'category', data: rows.map(r => r.call_id), axisLabel: { rotate: 45 } },
      yAxis: { type: 'value', name: 'Δ USD' },
      dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 6 }],
      series: [{ name: '기준안 대비 체선료', type: 'bar',
        data: rows.map(r => ({ value: dollars(r.cost_delta_cents),
          itemStyle: { color: r.cost_delta_cents > 0 ? '#d97706' : '#0d9488' } })) }],
    }, true);
  }
  function scenarioHistogram(elementId, buckets) {
    // buckets are server-generated {cost_min_usd,cost_max_usd,count}.
    if (!Array.isArray(buckets) || !buckets.length) throw new Error('No valid histogram buckets');
    mount(elementId).setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 55, right: 20, top: 25, bottom: 55 },
      xAxis: { type: 'category', name: 'Cost (USD)',
        data: buckets.map(b => `${b.cost_min_usd}–${b.cost_max_usd}`), axisLabel: { rotate: 35 } },
      yAxis: { type: 'value', name: 'Scenarios' },
      series: [{ type: 'bar', data: buckets.map(b => b.count) }],
    }, true);
  }
  function dispose() {
    for (const chart of instances.values()) chart.dispose();
    instances.clear();
  }
  root.DockRiskCharts = { riskBar, vesselWaterfall, scenarioHistogram, dispose };
})(window);
