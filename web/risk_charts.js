/* Read-only ECharts views of persisted Risk outcomes. No CDN or client pricing. */
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
  function empty(id, message) {
    mount(id).setOption({title:{text:message,left:'center',top:'middle',
      textStyle:{fontSize:12,fontWeight:'normal',color:'#777'}},series:[]},true);
  }
  function draw(report, vesselLabel='B') {
    const summary=report.summary;
    const risk=summary.risk_by_candidate||summary.conditional_risk_by_candidate;
    const labels=['A','B','C'];
    if (risk && labels.some(label=>risk[label])) {
      mount('risk-comparison-chart').setOption({
        tooltip:{trigger:'axis'},legend:{top:0},grid:{left:75,right:20,top:42,bottom:38},
        xAxis:{type:'category',data:labels},yAxis:{type:'value',name:'USD',scale:true},
        series:[
          {name:'기대 비용',type:'bar',data:labels.map(label=>dollars(risk[label]?.expected_cost_cents))},
          {name:'CVaR 95%',type:'bar',data:labels.map(label=>dollars(risk[label]?.cvar_cents))}
        ]},true);
    } else empty('risk-comparison-chart','정산된 비용 없음');
    const settled=report.outcomes.filter(row=>row.status==='SUCCEEDED'&&Number.isInteger(row.cost_cents));
    if (settled.length) {
      const values=settled.map(row=>row.cost_cents);
      const low=Math.min(...values),high=Math.max(...values);
      const width=Math.max(1,Math.ceil((high-low+1)/10));
      const buckets=Array.from({length:Math.min(10,Math.ceil((high-low+1)/width))},(_,i)=>low+i*width);
      mount('risk-distribution-chart').setOption({tooltip:{trigger:'axis'},legend:{top:0},
        grid:{left:60,right:18,top:38,bottom:60},
        xAxis:{type:'category',data:buckets.map(value=>moneyLabel(value)),axisLabel:{rotate:35}},
        yAxis:{type:'value',name:'건'},
        series:labels.map(label=>({name:label,type:'bar',data:buckets.map(start=>
          settled.filter(row=>row.label===label&&row.cost_cents>=start&&row.cost_cents<start+width).length)}))},true);
      const tails=labels.map(label=>settled.filter(row=>row.label===label)
        .map(row=>row.cost_cents).sort((a,b)=>b-a));
      const n=Math.max(...tails.map(list=>Math.max(1,Math.ceil(list.length*0.05))));
      mount('risk-tail-chart').setOption({tooltip:{trigger:'axis'},legend:{top:0},
        grid:{left:70,right:20,top:38,bottom:36},
        xAxis:{type:'category',name:'상위 순위',data:Array.from({length:n},(_,i)=>i+1)},
        yAxis:{type:'value',name:'USD',scale:true},
        series:labels.map((label,i)=>({name:label,type:'line',data:tails[i].slice(0,n).map(dollars)}))},true);
    } else {
      empty('risk-distribution-chart','정산된 시나리오 없음');
      empty('risk-tail-chart','정산된 시나리오 없음');
    }
    const coverage=summary.coverage_by_candidate||{};
    mount('risk-failure-chart').setOption({tooltip:{trigger:'axis'},legend:{top:0},
      grid:{left:50,right:20,top:38,bottom:35},
      xAxis:{type:'category',data:labels},yAxis:{type:'value',name:'건'},
      series:[['물리 불가능','infeasible'],['정산 미정','unresolved'],['결과 누락','missing']]
        .map(([name,key])=>({name,type:'bar',stack:'failure',data:labels.map(label=>coverage[label]?.[key]??0)}))},true);
    const vessel=summary.vessel_loss_by_candidate?.[vesselLabel];
    if (vessel) {
      const entries=Object.entries(vessel).sort((a,b)=>
        Math.abs(Number(b[1].paired_loss_risk.expected_cost_cents))-
        Math.abs(Number(a[1].paired_loss_risk.expected_cost_cents)));
      mount('risk-vessel-chart').setOption({tooltip:{trigger:'axis'},
        grid:{left:70,right:20,top:20,bottom:70},
        xAxis:{type:'category',data:entries.map(([id])=>id),axisLabel:{rotate:35}},
        yAxis:{type:'value',name:'A 대비 USD'},
        series:[{type:'bar',data:entries.map(([,item])=>dollars(item.paired_loss_risk.expected_cost_cents))}]},true);
    } else empty('risk-vessel-chart','전체 표본의 항차별 손실 통계 없음');
    resize();
  }
  function moneyLabel(cents){return '$'+(cents/100).toLocaleString()}
  function resize(){for(const chart of instances.values())chart.resize()}
  root.DockRiskCharts = { riskBar, vesselWaterfall, scenarioHistogram, draw, resize, dispose };
})(window);
