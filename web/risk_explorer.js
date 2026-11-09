/* Docktimize shock explorer: read-only, provenance-aware dashboard. */
(function(root){
'use strict';
const $=id=>document.getElementById(id);
const labels={A:'체선료 최소',B:'대기시간 최소',C:'일정 변동성 최소'};
const names={arrival:'ETA 도착 지연',work:'하역시간 증가',weather:'기상 악화'};
const kinds=['arrival','work','weather'];
let run=null, candidates=[], report=null, selected='A', kind='arrival', scenarioId=null, sequence=0;
const money=c=>c==null?'미산출':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(Number(c)/100);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=x=>x==null?'미산출':(x*100).toFixed(1)+'%';
const minutes=x=>x==null?'미산출':Number(x).toLocaleString('ko-KR',{maximumFractionDigits:1})+'분';
const rowOf=(id,label)=>report?.outcomes.find(x=>x.scenario_id===id&&x.label===label);
function fallback(id,text){const el=$(id);if(el)el.textContent=text;}
function chart(id,options){
 const el=$(id);if(!el)return;
 if(!root.echarts){el.textContent='차트 라이브러리를 불러오지 못했습니다.';return}
 const instance=root.echarts.getInstanceByDom(el)||root.echarts.init(el);
 instance.setOption(options,true);
}
function bars(id,series,yName,format){
 chart(id,{tooltip:{trigger:'axis',valueFormatter:v=>v==null?'미산출':format(v)},legend:{top:0},grid:{left:73,right:20,top:40,bottom:34},xAxis:{type:'category',data:['A','B','C']},yAxis:{type:'value',name:yName,scale:true},series:series.map(s=>({name:s.name,type:'bar',data:s.data,barMaxWidth:45}))});
}
function setCandidate(label){selected=label;$('rex-candidates').querySelectorAll('[data-rex-candidate]').forEach(node=>node.setAttribute('aria-pressed',String(node.dataset.rexCandidate===label)));drawBase();drawRisk();if(report&&scenarioId)loadDetail();}
function drawBase(){
 const available=Object.fromEntries(candidates.map(c=>[c.label,c]));
 const finite=Object.values(available),minCost=Math.min(...finite.map(c=>c.cost_cents)),minWait=Math.min(...finite.map(c=>c.wait_minutes));
 $('rex-candidates').innerHTML=kinds.length&&['A','B','C'].map(label=>{
 const c=available[label];if(!c)return '<div class="rex-candidate rex-muted">후보 '+label+' · 계산되지 않음</div>';
 const chosen=selected===label;
 const points=[c.cost_cents===minCost?'체선료 최저':'A 대비 '+money(c.cost_cents-(available.A?.cost_cents??c.cost_cents)),c.wait_minutes===minWait?'대기시간 최저':minutes(c.wait_minutes)];
 return '<button class="rex-candidate" data-rex-candidate="'+label+'" aria-pressed="'+chosen+'"><span class="rex-overline">후보 '+label+' · '+labels[label]+'</span><b>'+money(c.cost_cents)+'</b><small>총 대기 '+minutes(c.wait_minutes)+'</small><span class="rex-help">'+points.join(' · ')+'</span></button>';
 }).join('');
 const chosen=available[selected];
 $('rex-why').textContent=!chosen?'후보가 없습니다.':selected==='A'?'현재 평가된 후보 중 비용 중심의 선택입니다. 작업 여유시간이 충분한지는 충격 결과에서 별도로 확인하세요.':selected==='B'?'대기시간 절감을 우선한 후보입니다. 비용 증가분과 충격 이후 지연을 비교하세요.':'기존 잠정 일정과의 변경을 줄이는 후보입니다. 실제 충격 강건성은 시뮬레이션에서 확인해야 합니다.';
 bars('rex-base-cost',[{name:'기준 체선료',data:['A','B','C'].map(x=>available[x]?.cost_cents==null?null:available[x].cost_cents/100)}],'USD',v=>'$'+Number(v).toLocaleString());
 bars('rex-base-wait',[{name:'총 대기',data:['A','B','C'].map(x=>available[x]?.wait_minutes??null)}],'분',v=>minutes(v));
}
function renderProbability(){
 if(!report){fallback('rex-prob','저장된 리스크 분석 없음');return}
 const items=report.scenario_index||[], total=items.length, subset=items.filter(s=>s.types.includes(kind));
 const weighted=items.every(s=>Number.isFinite(Number(s.scenario_weight))&&Number(s.scenario_weight)>=0);
 const denominator=weighted?items.reduce((n,s)=>n+Number(s.scenario_weight),0):0;
 const occurrence=weighted&&denominator>0?subset.reduce((n,s)=>n+Number(s.scenario_weight),0)/denominator:null;
 const mixed=subset.filter(s=>s.mixed).length;
 $('rex-prob').innerHTML='<strong>'+pct(occurrence)+'</strong><small>가상 표본 내 충격 포함 빈도 · '+subset.length+'/'+total+'개 시나리오</small><div class="rex-progress"><span style="width:'+((occurrence||0)*100)+'%"></span></div><small>복합 충격 '+mixed+'개 포함 · 실제 발생확률 아님</small>';
 const status=report.summary.full_distribution_available?'전체 표본 정산 완료':'전체 표본 정산 미완료';
 $('rex-coverage').textContent=status+' · '+esc(report.scenario_config?.model_id||'')+' · '+total+'회';
}
function selectScenarios(){
 if(!report)return;
 const subset=report.scenario_index.filter(s=>s.types.includes(kind));
 const select=$('rex-scenario');
 if(!subset.length){scenarioId=null;select.innerHTML='';drawRisk();return}
 if(!subset.some(s=>s.scenario_id===scenarioId))scenarioId=subset[0].scenario_id;
 select.innerHTML=subset.map(s=>'<option value="'+esc(s.scenario_id)+'">'+esc(String(s.scenario_id).split('-').at(-1))+' · '+(s.mixed?'복합 충격':'단일 충격')+'</option>').join('');
 select.value=scenarioId;
 drawRisk();loadDetail();
}
function drawRisk(){
 const selectedIndex=report?.scenario_index?.find(s=>s.scenario_id===scenarioId);
 const scenario=selectedIndex&&['A','B','C'].map(label=>rowOf(scenarioId,label));
 const costs=scenario?.map((r,i)=>{
  const base=candidates.find(x=>x.label==='ABC'[i]);
  return r?.status==='SUCCEEDED'&&Number.isInteger(r.cost_cents)&&base?.cost_cents!=null?(r.cost_cents-base.cost_cents)/100:null;
 })||[null,null,null];
 bars('rex-delta',[{name:'기준 대비 추가 체선료',data:costs}],'USD',v=>'$'+Number(v).toLocaleString());
 const cohort=report?.scenario_index?.filter(s=>s.types.includes(kind))||[];
 const rates=['A','B','C'].map(label=>{
  let usable=0,delayed=0;
  for(const item of cohort){const r=rowOf(item.scenario_id,label);if(!r||!Array.isArray(r.propagated_delay_call_ids))continue;
   usable++;if(r.propagated_delay_call_ids.length)delayed++}
  return {rate:usable?100*delayed/usable:null,n:usable};
 });
 bars('rex-propagation',[{name:'후속 전파 지연 비율',data:rates.map(r=>r.rate)}],'%',v=>Number(v).toFixed(1)+'%');
 $('rex-propagation-note').textContent='선택 충격이 포함된 가상 표본 중 후속 지연이 발생한 비율 · 평가 가능한 표본 '+rates.map((r,i)=>'ABC'[i]+':'+r.n+'/'+cohort.length).join(' ');
 const settled=(report?.outcomes||[]).filter(r=>r.label===selected&&r.status==='SUCCEEDED'&&Number.isInteger(r.cost_cents)&&cohort.some(s=>s.scenario_id===r.scenario_id));
 if(settled.length){
  const costs=settled.map(r=>r.cost_cents).sort((a,b)=>a-b),min=costs[0],max=costs.at(-1);
  const width=Math.max(1,Math.ceil((max-min+1)/12)),count=Math.min(12,Math.ceil((max-min+1)/width));
  const bucket=Array.from({length:count},(_,i)=>min+i*width);
  chart('rex-distribution',{tooltip:{trigger:'axis'},grid:{left:64,right:18,top:18,bottom:55},xAxis:{type:'category',data:bucket.map(x=>money(x)),axisLabel:{rotate:27}},yAxis:{type:'value',name:'시나리오 수'},series:[{type:'bar',name:'정산 성공 사례',data:bucket.map(x=>costs.filter(v=>v>=x&&v<x+width).length),barMaxWidth:42}]});
 } else fallback('rex-distribution','정산 성공 사례 없음');
 const coverage=cohort.length?settled.length+'/'+cohort.length:'0/0';
 $('rex-cost-note').textContent='선택 후보 '+selected+' · 정산 성공 '+coverage+'개 (성공 사례 조건부 분포, 전체 위험 분포 아님)';
 const candidate=candidates.find(c=>c.label===selected);
 const r=rowOf(scenarioId,selected);
 $('rex-impact').innerHTML='<div><small>선택 시나리오 추가 체선료</small><b>'+ (r?.status==='SUCCEEDED'&&candidate?money(r.cost_cents-candidate.cost_cents):'미산출')+'</b></div><div><small>후속 전파 지연 항차</small><b>'+(Array.isArray(r?.propagated_delay_call_ids)?r.propagated_delay_call_ids.length+'척':'미산출')+'</b></div><div><small>정산 상태</small><b>'+esc(r?.status||'분석 없음')+'</b></div>';
 $('rex-shock-reason').textContent=!selectedIndex?'분석 대상 시나리오가 없습니다.':kind==='arrival'?'ETA 지연 입력이 포함된 사례를 골랐습니다. 같은 선석의 다음 작업에 영향이 전파되는지 확인합니다.':kind==='work'?'하역시간 배율이 1보다 큰 사례입니다. 작업 완료 및 선석 반환 지연을 비교합니다.':'기상 작업 차단이 추가된 사례입니다. 재개 시각과 후속 작업 지연을 비교합니다.';
 $('rex-guidance').textContent=!r?'평가 결과를 조회할 수 없습니다.':r.status!=='SUCCEEDED'?'비용 정산이 완료되지 않았습니다. 물리 일정과 미정 사유를 확인하고 비용을 확정값으로 사용하지 마세요.':Array.isArray(r.propagated_delay_call_ids)&&r.propagated_delay_call_ids.length?'해당 항차의 후속 선박 ETA와 작업 시작을 모니터링하고 다른 후보와 비용 차이를 비교하세요.':'해당 표본에서 후속 전파 지연이 확인되지 않았습니다. 다른 시나리오도 함께 검토하세요.';
}
function gantt(detail){
 const base=candidates.find(c=>c.label===selected), outcome=detail?.outcomes?.find(x=>x.label===selected);
 const old=new Map((base?.vessel_results||[]).map(x=>[x.call_id,x]));
 const rows=(outcome?.vessel_results||[]).filter(x=>x.realized_start_at||x.work_start_at).sort((a,b)=>String(a.berth_id).localeCompare(String(b.berth_id))).slice(0,50);
 if(!rows.length){fallback('rex-gantt','이 시나리오에 저장된 물리 일정 상세가 없습니다.');return}
 const parse=x=>Date.parse(x), starts=rows.flatMap(r=>[parse(r.planned_start_at||old.get(r.call_id)?.work_start_at),parse(r.realized_start_at||r.work_start_at)]).filter(Number.isFinite);
 const ends=rows.flatMap(r=>[parse(r.planned_completion_at||old.get(r.call_id)?.completion_at),parse(r.realized_completion_at||r.completion_at)]).filter(Number.isFinite);
 if(!starts.length||!ends.length){fallback('rex-gantt','유효한 작업 시작·완료 시각 없음');return}
 const first=Math.min(...starts),last=Math.max(...ends),span=Math.max(1,last-first);
 const bar=(a,b,cls)=>{const x=parse(a),y=parse(b);if(!Number.isFinite(x)||!Number.isFinite(y)||y<=x)return '';return '<span class="'+cls+'" style="left:'+((x-first)/span*100)+'%;width:'+((y-x)/span*100)+'%"></span>'};
 $('rex-gantt').innerHTML='<div class="rex-gantt-legend">윤곽선 계획 · 색 막대 충격 후</div>'+rows.map(r=>{
 const oldRow=old.get(r.call_id)||{};
 const ps=r.planned_start_at||oldRow.work_start_at, pe=r.planned_completion_at||oldRow.completion_at;
 const rs=r.realized_start_at||r.work_start_at, re=r.realized_completion_at||r.completion_at;
 const delay=r.start_delay_minutes??(Number.isFinite(parse(ps))&&Number.isFinite(parse(rs))?(parse(rs)-parse(ps))/60000:null);
 return '<div class="rex-gantt-row" title="'+esc(r.call_id+' · '+r.berth_id+' · 시작 지연 '+minutes(delay))+'"><span class="rex-gantt-name">'+esc(r.berth_id)+' / '+esc(r.call_id)+'</span><div class="rex-gantt-track">'+bar(ps,pe,'rex-plan-bar')+bar(rs,re,'rex-actual-bar')+'</div><small>'+minutes(delay)+'</small></div>';
 }).join('');
 $('rex-gantt').querySelectorAll('.rex-gantt-row').forEach((node,i)=>node.addEventListener('click',()=>{$('rex-evidence').textContent=JSON.stringify(rows[i],null,2)}));
}
async function loadDetail(){
 const id=scenarioId,aid=report?.analysis_id,ticket=++sequence;
 if(!id||!aid){fallback('rex-gantt','시나리오 없음');return}
 fallback('rex-gantt','선석별 충격 후 일정 조회 중');
 try{
 const response=await fetch('/api/risk/analyses/'+encodeURIComponent(aid)+'/scenarios/'+encodeURIComponent(id));
 if(!response.ok)throw Error('시나리오 상세 조회 실패: '+response.status);
 const detail=await response.json();if(ticket!==sequence)return;
 gantt(detail);
 const picked=detail.outcomes.find(x=>x.label===selected);
 $('rex-evidence').textContent=JSON.stringify({scenario:detail.scenario,candidate:selected,outcome:{status:picked?.status,cost_cents:picked?.cost_cents,failure_class:picked?.failure_class,payload:picked?.payload}},null,2);
 }catch(error){if(ticket===sequence)fallback('rex-gantt',error.message)}
}
async function load(runId,base=[],candidate='A',force=false){
 candidates=base||[];selected=candidate||selected;
 if(!runId){run=null;report=null;fallback('rex-state','계획 후보를 먼저 계산하세요.');drawBase();return}
 drawBase();if(run===runId&&!force)return;run=runId;const ticket=++sequence;
 fallback('rex-state','저장된 리스크 분석 조회 중');
 try{
 const list=await fetch('/api/risk/analyses?run_id='+encodeURIComponent(runId));
 if(!list.ok)throw Error('분석 목록 조회 실패');
 const all=await list.json();if(ticket!==sequence)return;
 const latest=all.analyses?.find(x=>x.scenario_count===1000)||all.analyses?.[0];
 if(!latest){report=null;fallback('rex-state','저장된 시뮬레이션이 없습니다. Risk CLI 평가 및 저장 후 새로고침하세요.');return}
 const response=await fetch('/api/risk/analyses/'+encodeURIComponent(latest.analysis_id));
 if(!response.ok)throw Error('분석 결과 조회 실패');
 const data=await response.json();if(ticket!==sequence)return;
 if(data.run_id!==runId)throw Error('현재 계획과 분석의 실행 ID가 일치하지 않습니다');
 report=data;
 fallback('rex-state','분석 '+data.analysis_id.slice(0,8)+' · 동일 스냅샷의 A/B/C 비교');
 renderProbability();selectScenarios();
 }catch(e){if(ticket===sequence)fallback('rex-state',e.message)}
}
function init(){
 $('rex-candidates').addEventListener('click',e=>{const b=e.target.closest('[data-rex-candidate]');if(b){setCandidate(b.dataset.rexCandidate)}});
 $('rex-types').addEventListener('click',e=>{const b=e.target.closest('[data-rex-type]');if(!b)return;kind=b.dataset.rexType;$('rex-types').querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));renderProbability();selectScenarios()});
 $('rex-scenario').addEventListener('change',e=>{scenarioId=e.target.value;drawRisk();loadDetail()});
 $('rex-reload').addEventListener('click',()=>load(run,candidates,selected,true));
 root.addEventListener('resize',()=>{if(root.echarts)['rex-base-cost','rex-base-wait','rex-delta','rex-propagation','rex-distribution'].forEach(id=>{const el=$(id),chart=el&&root.echarts.getInstanceByDom(el);chart?.resize()})});
}
root.DockRiskExplorer={load,setCandidate,resize:()=>root.dispatchEvent(new Event('resize'))};
init();
})(window);
