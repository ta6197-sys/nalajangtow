"use strict";

let state=null, busy=false, compareLabel='B', showAllRows=false, pendingWorkbook=null;
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=x=>x==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(x/100);
const deltaMoney=x=>x===0?'동일':`${x>0?'+':''}${money(x)}`;
const deltaMinutes=x=>x===0?'동일':`${x>0?'+':''}${x.toLocaleString()}분`;
const time=x=>x?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(x)):'—';
function message(text,error=false){$('message').innerHTML=text?`<div class="${error?'error':'notice'}">${esc(text)}</div>`:''}
async function api(path,body){const response=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw Error(data.error||'요청 실패');return data}
async function act(path,body,working){
  if(busy)return false;
  busy=true;setBusy(true);message(working);
  try{
    state=await api(path,body);
    pendingWorkbook=null;$('uploadPreview').innerHTML='';
    message(state.next_plan_error?`24시간 운영은 완료했습니다. 다음 날짜 후보 계산 오류: ${state.next_plan_error}`:'완료했습니다.',!!state.next_plan_error);
    return true;
  }catch(e){
    message(e.message,true);
    // A lost response can follow a committed action. Refresh without repeating it.
    try{state=await api('/api/status')}catch(_){}
    return false;
  }finally{busy=false;setBusy(false);render()}
}
function render(){if(!state)return;
  const current=state.current, counts=state.status_counts;
  if(state.input_source){const source=state.input_source;$('source-summary').innerHTML=`<b>${esc(source.filename)}</b><p>계약 ${source.validation.contracts}건 · WORK 이력 확인 ${source.validation.ready_calls}항차 · 총 작업 ${source.validation.work_minutes.toLocaleString()}분</p><p>WORK 이력 없는 항차 ${source.manual_review.length}건: ${source.manual_review.map(x=>esc(x.call_id)).join(', ')} · 별도 확인 대상</p><small>원본 SHA-256: ${esc(source.sha256)}</small>`;}
  $('status').textContent=state.as_of?`계획 기준 ${time(state.as_of)} KST · ${state.latest_state_at?'다음 날짜 준비':'첫 계획 준비'} · 완료 ${counts.COMPLETED||0}척 / 접안 유지 ${counts.IN_PROGRESS||0}척`:'계약과 항차를 등록해 시작하세요.';
  if(state.sunrise_at&&state.sunset_at) $('status').textContent+=` · 당일 접안 가능 ${time(state.sunrise_at).slice(11)}~${time(state.sunset_at).slice(11)} (기상 허용 시)`;
  const inputDates=[...new Set(state.calls.map(c=>String(c.nor_at).slice(0,10)))].sort();
  $('counts').textContent=`계약 ${state.contracts.length}건 · 항차 ${state.calls.length}건 · 입력 날짜 ${inputDates.length}일${inputDates.length?` (${inputDates[0]}~${inputDates.at(-1)})`:''} · 선석 8개`;
  $('currentWorkbook').hidden=!state.upload_locked;
  $('uploadButton').textContent=state.upload_locked?'변경 미리보기':'파일 검증·등록';
  $('uploadStatus').textContent=state.uploaded_files?.length?`원본 SHA-256: ${state.uploaded_files[0].replace('.xlsx','')}`:'NOR 날짜 00:00에 항차가 공개됩니다. 기상 시나리오 범위는 2026-11-01~2027-03-31입니다.';
  $('inputTable').innerHTML=state.calls.length?`<table><thead><tr><th>항차 / 선박</th><th>계약</th><th>공개 시각</th><th>NOR</th><th>작업 가능</th><th>작업분</th><th>상태</th></tr></thead><tbody>${state.calls.map(c=>`<tr><td>${esc(c.id)}<br><span class="muted">${esc(c.vessel_name||'')}</span></td><td>${esc(c.contract_id)}</td><td>${esc(time(c.known_at))}</td><td>${esc(time(c.nor_at))}</td><td>${esc(time(c.available_at))}</td><td>${c.work_minutes}</td><td>${esc(c.night_paused?'야간 중단 · 선석 점유':c.status)}</td></tr>`).join('')}</tbody></table>`:'XLSX를 업로드하면 항차가 여기에 표시됩니다.';
  const committed=new Set(state.current?.applied_candidate_id ? state.current.candidates.find(c=>c.candidate_id===state.current.applied_candidate_id)?.vessel_results.filter(r=>r.assignment_kind==='COMMITTED'||(r.assignment_kind!=='EVALUATION_TAIL'&&Date.parse(r.work_start_at)<Date.parse(state.as_of)+24*3600000)).map(r=>r.call_id) : []);
  const editable=state.calls.filter(c=>!['CANCELLED','COMPLETED','IN_PROGRESS'].includes(c.status)&&!committed.has(c.id));
  $('editPanel').hidden=!editable.length;
  $('editCall').innerHTML=editable.map(c=>`<option value="${esc(c.id)}">${esc(c.id)}</option>`).join('');
  $('editContract').innerHTML=state.contracts.map(c=>`<option value="${esc(c.id)}">${esc(c.id)}</option>`).join('');
  $('newContract').innerHTML=$('editContract').innerHTML;
  fillEdit();
  $('planButton').textContent=state.as_of?`${String(state.as_of).slice(0,10)} 후보 계산`:'현재 날짜 후보 계산';
  const allDone=(counts.COMPLETED||0)+(counts.CANCELLED||0)>=state.calls.length;
  $('planButton').disabled=!state.calls.length||allDone||!!(current&&current.candidates.length)||busy;
  $('planStatus').textContent=allDone?'등록된 모든 항차의 가상 운영이 끝났습니다.':current?`${current.date} · ${current.solver_status} · 후보 ${current.candidates.length}개`:'현재 날짜의 후보를 아직 계산하지 않았습니다.';
  $('operateButton').hidden=!current?.applied_candidate_id||current?.actual_day_cents!=null;
  renderScheduler();
  renderTradeoff(current);
  loadRisk(current?.run_id);
  const done=state.history.filter(r=>r.actual_day_cents!=null);
  $('history').innerHTML=done.length?`<p>각 날짜는 당시 정책으로 보존됩니다. ‘주간 작업’은 야간 중단, ‘주간 시작’은 시작시각만 제한한 과거 정책입니다.</p><table><thead><tr><th>날짜</th><th>정책</th><th>A 평가</th><th>B 평가</th><th>C 평가</th><th>적용</th><th>당일 실제</th><th>누적 실제</th><th>완료</th></tr></thead><tbody>${done.map(r=>{const a=Object.fromEntries(r.candidates.map(c=>[c.label,c]));const applied=r.candidates.find(c=>c.candidate_id===r.applied_candidate_id);return `<tr><td>${r.date}</td><td>${r.berthing_policy==='DAYLIGHT_WORK_V2'?'주간 작업':r.berthing_policy==='DAYLIGHT_WEATHER_V1'?'주간 시작':'기존'}</td><td>${money(a.A?.cost_cents)}</td><td>${money(a.B?.cost_cents)}</td><td>${money(a.C?.cost_cents)}</td><td>${applied?.label||'—'}</td><td>${money(r.actual_day_cents)}</td><td>${money(r.accumulated_cents)}</td><td>${r.completed_count}</td></tr>`}).join('')}</tbody></table>`:'아직 운영한 날짜가 없습니다.';
}
function renderTradeoff(current){
  const by=Object.fromEntries((current?.candidates||[]).map(c=>[c.label,c]));
  const base=by.A;
  if(!base){$('tradeoffSummary').innerHTML='';$('tradeoffDetails').innerHTML='';return}
  const baseRows=Object.fromEntries(base.vessel_results.map(r=>[r.call_id,r]));
  const compare=c=>{let berth=0,shift=0;for(const r of c.vessel_results){const a=baseRows[r.call_id];if(!a)continue;berth+=Number(a.berth_id!==r.berth_id);shift+=Math.abs((Date.parse(r.work_start_at)-Date.parse(a.work_start_at))/60000)}return {berth,shift}};
  const costsEqual=Object.values(by).every(c=>c.cost_cents===base.cost_cents);
  $('tradeoffSummary').innerHTML=`<h3>A 대비 후보별 트레이드오프</h3><p class="small muted">비용은 계획 기준시각부터 모든 대상 항차의 완료까지 정확 평가한 체선료 증가분입니다. 대기는 항차별 작업 가능 시각부터 시작까지의 합계이며, 기존 확정 항차도 포함합니다. 72시간 밖의 미착수 항차는 공통 FCFS로 평가합니다. OPTIMAL은 생성된 시작시각·선석 선택지 안에서의 최적성입니다. +는 A보다 증가, −는 감소입니다.</p>${costsEqual?'<p class="notice small">이 날짜의 후보들은 평가 체선료가 같습니다. 계약 허용시간 이내라면 대기가 있어도 체선료는 0입니다. 대기시간과 선석·시작 변경에서 차이를 확인하세요.</p>':''}<table><thead><tr><th>후보</th><th>체선료</th><th>A 대비 비용</th><th>총 대기</th><th>A 대비 대기</th><th>A 대비 선석 변경</th><th>A 대비 시작 이동</th><th>전날 잠정 대비 변경</th></tr></thead><tbody>${'ABC'.split('').map(label=>{const c=by[label];if(!c)return `<tr><td>${label}</td><td colspan="7">후보 없음</td></tr>`;const d=compare(c);return `<tr><td><b>${label}</b> ${esc(c.night_paused?'야간 중단 · 선석 점유':c.status)}</td><td>${money(c.cost_cents)}</td><td>${deltaMoney(c.cost_cents-base.cost_cents)}</td><td>${c.wait_minutes.toLocaleString()}분</td><td>${deltaMinutes(c.wait_minutes-base.wait_minutes)}</td><td>${d.berth}건</td><td>${d.shift.toLocaleString()}분</td><td>${c.compared_tentative_call_count?`${c.berth_change_count}건 · ${c.start_shift_minutes.toLocaleString()}분`:'비교 일정 없음'}</td></tr>`}).join('')}</tbody></table><p class="small muted">FEASIBLE은 시간 제한 안에 찾은 후보이며 최적성 증명은 아닙니다. 이 경우 B/C 비용이 A보다 낮을 수 있습니다.</p>`;
  const available=['B','C'].filter(label=>by[label]);
  if(!available.length){$('tradeoffDetails').innerHTML='';return}
  if(!available.includes(compareLabel))compareLabel=available[0];
  const other=by[compareLabel],otherRows=Object.fromEntries(other.vessel_results.map(r=>[r.call_id,r]));
  const allIds=[...new Set([...Object.keys(baseRows),...Object.keys(otherRows)])].sort((x,y)=>{
    const dx=Math.abs((otherRows[x]?.demurrage_increment_to_completion_cents||0)-(baseRows[x]?.demurrage_increment_to_completion_cents||0));
    const dy=Math.abs((otherRows[y]?.demurrage_increment_to_completion_cents||0)-(baseRows[y]?.demurrage_increment_to_completion_cents||0));
    return dy-dx||x.localeCompare(y)});
  const changed=allIds.filter(id=>{const a=baseRows[id],b=otherRows[id];return !a||!b||a.berth_id!==b.berth_id||a.work_start_at!==b.work_start_at||a.total_wait_minutes!==b.total_wait_minutes||a.demurrage_increment_to_completion_cents!==b.demurrage_increment_to_completion_cents});
  const ids=showAllRows?allIds:changed;
  $('tradeoffDetails').innerHTML=`<details open><summary>항차별 차이 보기 (${changed.length}척 변경)</summary><div class="row"><p class="small muted">비용 차이가 큰 항차부터 표시합니다. ‘이월’은 72시간 이후 공통 규칙으로 배정한 평가용 일정입니다.</p><label>비교 후보 <select id="compareTarget">${available.map(label=>`<option value="${label}" ${label===compareLabel?'selected':''}>A ↔ ${label}</option>`).join('')}</select></label><label><input id="showAllRows" type="checkbox" ${showAllRows?'checked':''} style="width:auto;height:auto"> 변화 없는 항차도 보기</label></div>${ids.length?`<div class="scroll"><table><thead><tr><th>항차</th><th>A 선석·시작</th><th>${compareLabel} 선석·시작</th><th>대기 A → ${compareLabel}</th><th>체선료 A → ${compareLabel}</th></tr></thead><tbody>${ids.map(id=>{const a=baseRows[id],b=otherRows[id];if(!a||!b)return '';return `<tr><td>${esc(id)}</td><td>${esc(a.berth_id)} · ${esc(time(a.work_start_at))}${a.assignment_kind==='EVALUATION_TAIL'?' (이월)':''}</td><td>${esc(b.berth_id)} · ${esc(time(b.work_start_at))}${b.assignment_kind==='EVALUATION_TAIL'?' (이월)':''}</td><td>${a.total_wait_minutes.toLocaleString()} → ${b.total_wait_minutes.toLocaleString()}분<br><b>${deltaMinutes(b.total_wait_minutes-a.total_wait_minutes)}</b></td><td>${money(a.demurrage_increment_to_completion_cents)} → ${money(b.demurrage_increment_to_completion_cents)}<br><b>${deltaMoney(b.demurrage_increment_to_completion_cents-a.demurrage_increment_to_completion_cents)}</b></td></tr>`}).join('')}</tbody></table></div>`:'<p class="small muted">A와 같은 항차 일정·대기·비용입니다.</p>'}</details>`;
  $('tradeoffDetails').innerHTML+=`<details><summary>A 항차별 계약시간·체선료 확인</summary><p class="small muted">산입시간은 NOR와 통지기간, 계약별 휴일·기상 제외 규칙을 적용해 완료 시점까지 계산합니다. 허용시간을 넘긴 시간에 일일 요율을 적용합니다.</p><div class="scroll"><table><thead><tr><th>항차</th><th>구분</th><th>대기</th><th>산입 / 허용</th><th>초과</th><th>체선료 증가분</th></tr></thead><tbody>${base.vessel_results.map(r=>`<tr><td>${esc(r.call_id)}</td><td>${r.assignment_kind==='COMMITTED'?'기존 확정':r.assignment_kind==='EVALUATION_TAIL'?'이월':'이번 후보'}</td><td>${r.total_wait_minutes.toLocaleString()}분</td><td>${Number(r.laytime_used_minutes).toLocaleString()} / ${Number(r.allowed_minutes).toLocaleString()}분</td><td>${Number(r.demurrage_minutes).toLocaleString()}분</td><td>${money(r.demurrage_increment_to_completion_cents)}</td></tr>`).join('')}</tbody></table></div></details>`;
  $('compareTarget').onchange=e=>{compareLabel=e.target.value;renderTradeoff(current)};
  $('showAllRows').onchange=e=>{showAllRows=e.target.checked;renderTradeoff(current)};
}
function fillEdit(){const c=state?.calls.find(x=>x.id===$('editCall').value);if(!c)return;$('editContract').value=c.contract_id;$('editNor').value=time(c.nor_at).replace(' ','T');$('editAvailable').value=time(c.available_at).replace(' ','T');$('editQuantity').value=c.cargo_quantity_kg;$('editWork').value=c.work_minutes}
$('editCall').onchange=fillEdit;
$('editForm').onsubmit=e=>{e.preventDefault();act('/api/call/edit',{call_id:$('editCall').value,contract_id:$('editContract').value,nor_at:$('editNor').value,available_at:$('editAvailable').value,cargo_quantity_kg:$('editQuantity').value,work_minutes:$('editWork').value,reason:$('editReason').value},'항차를 수정하고 변경 이력을 저장합니다...')};
$('newCallForm').onsubmit=e=>{e.preventDefault();act('/api/call',{call_id:$('newCallId').value,contract_id:$('newContract').value,known_at:$('newKnown').value,nor_at:$('newNor').value,available_at:$('newAvailable').value,cargo_quantity_kg:$('newQuantity').value,work_minutes:$('newWork').value},'새 항차를 등록합니다...')};
$('cancelCallButton').onclick=()=>{const cid=$('editCall').value;if(cid&&confirm(`${cid} 항차를 취소하시겠습니까?`))act('/api/call/cancel',{call_id:cid,reason:$('editReason').value},'항차 취소를 기록합니다...')};
$('uploadForm').onsubmit=async e=>{e.preventDefault();const file=$('workbook').files[0];if(!file||busy||!state)return;
  pendingWorkbook=null;$('uploadPreview').innerHTML='';
  if(file.size>5000000){message('XLSX 파일은 5 MB 이하여야 합니다.',true);return}
  try{const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file)});
    const payload={filename:file.name,base64:encoded};
    if(!state.upload_locked){await act('/api/upload',payload,'전체 계약·항차를 검증하고 저장합니다...');return}
    if(busy)return;busy=true;setBusy(true);message('XLSX 변경 내용을 검증합니다...');
    const preview=await api('/api/upload/preview',payload);pendingWorkbook={...payload,token:preview.token};
    $('uploadPreview').innerHTML=`<div class="notice"><b>변경 미리보기 · ${preview.changes.length}건</b><p class="small">파일에서 빠진 항차는 유지합니다. 기존 계약과 확정·진행·완료 항차는 변경하지 않습니다.</p>${preview.changes.length?`<div class="scroll"><table><thead><tr><th>종류</th><th>ID</th><th>변경 내용</th></tr></thead><tbody>${preview.changes.map(c=>`<tr><td>${c.kind==='CONTRACT_ADDED'?'새 계약':c.kind==='CALL_ADDED'?'새 항차':'항차 수정'}</td><td>${esc(c.id)}</td><td>${c.kind==='CALL_UPDATED'?esc(['contract_id','nor_at','available_at','cargo_quantity_kg','work_minutes'].filter(k=>String(c.before?.[k])!==String(c.after?.[k])).map(k=>`${k}: ${c.before[k]} → ${c.after[k]}`).join(' · ')):'추가'}</td></tr>`).join('')}</tbody></table></div>`:'변경 사항이 없습니다.'}<div class="actions"><button id="applyWorkbook" ${preview.changes.length?'':'disabled'}>이 변경 적용</button></div></div>`;
    $('applyWorkbook').onclick=()=>{const body=pendingWorkbook;if(!body)return;pendingWorkbook=null;$('uploadPreview').innerHTML='';act('/api/upload/apply',body,'미리보기 변경을 적용합니다...')};message('변경 내용을 확인한 뒤 적용 버튼을 누르세요.');
  }catch(error){message(error.message,true)}
  finally{busy=false;setBusy(false);render()}
};
$('planButton').onclick=()=>act('/api/plan',{},'A/B/C 후보를 계산합니다. 복잡한 날은 몇 분 걸릴 수 있습니다...');
    $('operateButton').onclick=async()=>{if(await act('/api/operate',{},'당일 24시간을 운영하고 다음 날짜 A/B/C를 계산합니다. 최대 몇 분 걸릴 수 있습니다...'))navigate('compare')};


let selectedId=null, displayedRunId=null;
const strategies={A:'체선료 최소 탐색',B:'대기시간 최소 탐색',C:'잠정 일정 변경 최소 탐색'};
function selectedCandidate(){return state?.current?.candidates.find(c=>c.candidate_id===selectedId)}
function setBusy(value){
  $('app').classList.toggle('busy',value);
  $('app').setAttribute('aria-busy',String(value));
  document.querySelectorAll('nav button').forEach(b=>b.disabled=value);
  $('engine-status').textContent=value?'처리 중 · 잠시 기다려주세요':state?'API 연결됨 · PostgreSQL':'서버 연결 중';
}
function navigate(page){
  if(busy)return;
  document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id===`page-${page}`));
  document.querySelectorAll('nav [data-page]').forEach(el=>{el.classList.toggle('current',el.dataset.page===page);el.setAttribute('aria-current',el.dataset.page===page?'page':'false')});
  window.scrollTo({top:0,behavior:'instant'});
  if(page==='compare')setTimeout(()=>window.DockRiskCharts?.resize(),0);
}
function summaryRow(label,value){return `<div class="availability-row"><div><b>${esc(label)}</b></div><span>${esc(value)}</span></div>`}
function renderScheduler(){
  const current=state.current, candidates=current?.candidates||[], counts=state.status_counts;
  if(current?.run_id!==displayedRunId||!candidates.some(c=>c.candidate_id===selectedId)){
    displayedRunId=current?.run_id;
    selectedId=current?.applied_candidate_id||candidates[0]?.candidate_id||null;
  }
  $('engine-status').textContent='API 연결됨 · PostgreSQL';
  $('timestamp').textContent=`Ulsan Terminal · ${state.as_of?time(state.as_of)+' KST':'입력 대기'}`;
  $('day-status').textContent=current?.applied_candidate_id?'당일 확정 완료':candidates.length?'후보 검토 중':'계획 준비';
  $('scenario-summary').innerHTML=summaryRow('전체 항차',`${state.calls.length}척`)+summaryRow('당일 공개된 미완료 항차',`${state.disclosed_call_count??'—'}척`)+summaryRow('대기 / 접안 유지',`${counts.WAITING||0}척 / ${counts.IN_PROGRESS||0}척`)+summaryRow('완료 / 취소',`${counts.COMPLETED||0}척 / ${counts.CANCELLED||0}척`);
  $('daylight-summary').innerHTML=summaryRow('계획일',state.as_of?time(state.as_of).slice(0,10):'입력 후 표시')+summaryRow('일출 / 일몰',state.sunrise_at&&state.sunset_at?`${time(state.sunrise_at).slice(11)} / ${time(state.sunset_at).slice(11)}`:'계획일 일출·일몰 정보 없음')+summaryRow('기상', '과거 관측 이동 시나리오');
  $('compare-title').textContent=`${current?.date|| (state.as_of?time(state.as_of).slice(0,10):'일별')} 접안 후보 비교`;
  const baseline=candidates.find(c=>c.label==='A');
  $('candidates').innerHTML=['A','B','C'].map(label=>{
    const c=candidates.find(c=>c.label===label);
    if(!c)return `<article class="candidate-card blank"><div class="candidate-title">후보 ${label}</div><p class="small muted">${strategies[label]}</p><p class="small muted">${candidates.length?'서로 다른 실행 가능 후보 없음':'계산 후 표시됩니다.'}</p></article>`;
    const fixedWait=c.vessel_results.filter(r=>r.assignment_kind==='COMMITTED').reduce((n,r)=>n+r.total_wait_minutes,0);
    return `<button class="candidate-card ${selectedId===c.candidate_id?'selected':''}" data-candidate="${esc(c.candidate_id)}" aria-pressed="${selectedId===c.candidate_id}">
      <div class="candidate-title"><span>후보 ${esc(c.label)}</span><small>${current.applied_candidate_id===c.candidate_id?'당일 확정':esc(c.status)}</small></div>
      <p class="small muted">${strategies[c.label]}</p><div class="cost-label">완료까지 평가 체선료</div><div class="cost-value">${money(c.cost_cents)}</div>
      <div class="cost-saving">${baseline&&c!==baseline?'A 대비 '+deltaMoney(c.cost_cents-baseline.cost_cents):'후보 비교 기준 A'}</div><hr>
      <div class="risk-text">총 대기 ${c.wait_minutes.toLocaleString()}분</div><div class="small muted">기존 확정 ${fixedWait.toLocaleString()}분 · 이번 후보 ${(c.wait_minutes-fixedWait).toLocaleString()}분</div>
      <div class="small muted">기상 중단 ${c.weather_pause_minutes.toLocaleString()}분 · 야간 중단 ${(c.night_pause_minutes||0).toLocaleString()}분 · 일정 ${c.assignments.length}건</div>
    </button>`;
  }).join('');
  const c=selectedCandidate();
  $('go-confirm').disabled=!c;
  $('go-confirm').textContent=current?.applied_candidate_id?'확정 결과 보기 →':'확정하러 가기 →';
  $('selection-message').textContent=c?`선택: 후보 ${c.label} · 평가 체선료 ${money(c.cost_cents)}`:'입력 등록 후 후보를 계산하세요.';
  renderTimeline();renderConfirmation();
}
function renderTimeline(){
  const c=selectedCandidate(), current=state?.current;
  if(!c){$('timeline').innerHTML='<p class="small muted">후보를 계산하면 선석별 일정이 표시됩니다.</p>';$('assignment-table').innerHTML='';return}
  const baseline=current.candidates.find(x=>x.label==='A');
  const compare=$('compare-baseline').checked&&baseline&&baseline!==c;
  // vessel_results includes existing committed assignments; evaluation tails are outside the plan.
  const rowsFor=candidate=>candidate.vessel_results.filter(r=>r.assignment_kind!=='EVALUATION_TAIL');
  const rows=rowsFor(c), baseRows=compare?rowsFor(baseline):[];
  const berths=[...new Set([...rows,...baseRows].map(r=>r.berth_id))].sort();
  const origin=Date.parse(state.as_of), windowMs=72*3600000;
  const block=(a,b,className,text,title)=>{
    const start=(Date.parse(a)-origin)/windowMs*100, end=(Date.parse(b)-origin)/windowMs*100;
    if(end<=0||start>=100)return '';
    return `<div class="${className}" style="left:${Math.max(0,start)}%;width:${Math.min(100,end)-Math.max(0,start)}%" title="${esc(title)}">${text}</div>`;
  };
  let cursor=origin;
  const nights=[];
  for(const window of state.daylight_windows||[]){
    const rise=Date.parse(window.sunrise_at),setting=Date.parse(window.sunset_at);
    if(cursor<rise)nights.push([new Date(cursor).toISOString(),window.sunrise_at]);
    cursor=setting;
  }
  if((state.daylight_windows||[]).length&&cursor<origin+windowMs)nights.push([new Date(cursor).toISOString(),new Date(origin+windowMs).toISOString()]);
  const background=nights.map(([a,b])=>block(a,b,'night-window','',`야간 작업 중단 · ${time(a)} ~ ${time(b)}`)).join('');
  const tracks=(items,dim=false)=>berths.map(berth=>{
    const blocks=items.filter(r=>r.berth_id===berth).map(r=>{
      const caption=`${r.call_id} · ${time(r.work_start_at)} ~ ${time(r.completion_at)} KST`;
      const occupancy=block(r.work_start_at,r.completion_at,'berth-occupancy','',`선석 점유 · ${caption}`);
      const work=(r.work_intervals||[]).map(([a,b])=>block(a,b,'timeline-block',`<i></i><span><strong>${esc(r.call_id)}</strong></span>`,`작업 · ${r.call_id} · ${time(a)} ~ ${time(b)}`)).join('');
      return occupancy+work;
    }).join('');
    return `<div class="timeline-row"><span class="berth-name">${dim?'A · ':''}${esc(berth)}</span><div class="timeline-track ${dim?'baseline-track':''}">${background}${blocks}</div></div>`;
  }).join('');
  $('timeline').innerHTML=`<div class="timeline-head"><div>선석 / KST</div><div class="timeline-clock">${[0,9,18,27,36,45,54,63].map(h=>`<span>+${h}h</span>`).join('')}</div></div>`+tracks(rows)+(compare?'<p class="small muted">후보 A와 비교</p>'+tracks(baseRows,true):'');
  $('assignment-table').innerHTML=`<details><summary>후보 ${esc(c.label)} 전체 일정 · 기존 확정 포함</summary><table><thead><tr><th>항차</th><th>구분</th><th>선석</th><th>시작 KST</th><th>완료 KST</th><th>야간 중단</th><th>작업 구간</th></tr></thead><tbody>${c.vessel_results.map(r=>`<tr><td>${esc(r.call_id)}</td><td>${r.assignment_kind==='COMMITTED'?'기존 확정':r.assignment_kind==='EVALUATION_TAIL'?'이월 평가':'이번 후보'}</td><td>${esc(r.berth_id)}</td><td>${esc(time(r.work_start_at))}</td><td>${esc(time(r.completion_at))}</td><td>${(r.night_pause_minutes||0).toLocaleString()}분</td><td>${(r.work_intervals||[]).map(([a,b])=>esc(time(a))+' ~ '+esc(time(b))).join('<br>')}</td></tr>`).join('')}</tbody></table></details>`;
}
function renderConfirmation(){
  const c=selectedCandidate(), current=state?.current;
  const confirmed=!!current?.applied_candidate_id;
  const isApplied=confirmed&&current.applied_candidate_id===c?.candidate_id;
  $('selected-badge').textContent=c?`선택: 후보 ${c.label}`:'후보 선택 전';
  $('confirm-status').textContent=confirmed?'확정 완료':c?'검토 후 확정':'후보 없음';
  $('confirmation-summary').innerHTML=c?summaryRow('계획 기준',`${time(state.as_of)} KST`)+summaryRow('평가 체선료',money(c.cost_cents))+summaryRow('총 대기',`${c.wait_minutes.toLocaleString()}분`)+summaryRow('탐색 상태',c.status):'<p class="small muted">상황 확인에서 입력을 등록하고 후보 비교에서 후보를 계산하세요.</p>';
  $('confirm-action').disabled=!c||confirmed;
  $('confirm-action').textContent=confirmed?'당일 일정 확정 완료':'✓ 선택한 후보로 당일 확정';
  $('confirmed-status').textContent=confirmed?`후보 ${current.candidates.find(x=>x.candidate_id===current.applied_candidate_id)?.label||'—'}의 당일 일정이 저장되었습니다.`:c?'아직 확정하지 않은 계획입니다.':'';
  $('operateButton').hidden=!confirmed||current?.actual_day_cents!=null;
  $('report-subject').textContent=c?`[Docktimize] ${current.date} 접안 계획 · 후보 ${c.label}`:'후보 선택 후 표시됩니다.';
  $('report-outline').innerHTML=c?`<div class="report-line"><b>① 선택 후보</b><p>${esc(strategies[c.label])} · ${esc(c.status)}</p></div><div class="report-line"><b>② 평가 결과</b><p>체선료 ${money(c.cost_cents)} · 대기 ${c.wait_minutes.toLocaleString()}분 · 기상 중단 ${c.weather_pause_minutes.toLocaleString()}분 · 야간 중단 ${(c.night_pause_minutes||0).toLocaleString()}분</p></div><div class="report-line"><b>③ 적용 상태</b><p>${isApplied?'당일 확정 완료':'미확정 후보'} · 일정 ${c.assignments.length}건</p></div>`:'';
  $('copy-report').disabled=!c;
  $('download-report').disabled=!isApplied;
}
function reportText(){
  const c=selectedCandidate(), current=state.current;
  const applied=current.applied_candidate_id===c.candidate_id;
  return `[Docktimize] ${current.date} 접안 계획 · 후보 ${c.label}\n계획 기준: ${time(state.as_of)} KST\n전략: ${strategies[c.label]}\n평가 체선료: ${money(c.cost_cents)}\n총 대기: ${c.wait_minutes}분\n기상 중단: ${c.weather_pause_minutes}분\n탐색 상태: ${c.status}\n적용: ${applied?'당일 확정 완료':'미확정 후보'}\n실행 ID: ${current.run_id}\n\n${c.vessel_results.map(r=>`${r.call_id} | ${r.berth_id} | ${time(r.work_start_at)} ~ ${time(r.completion_at)} KST | ${r.assignment_kind}`).join('\n')}\n\n로컬 운영 시뮬레이션 · 과거 관측 이동 기상 시나리오 · 가상 선석 8개`;
}
function csvCell(value){
  let text=String(value??'');
  if(/^[=+\-@\t\r\n]/.test(text))text="'"+text;
  return '"'+text.replaceAll('"','""')+'"';
}
$('download-report').onclick=()=>{
  const c=selectedCandidate(), current=state?.current;
  if(!c||current.applied_candidate_id!==c.candidate_id)return;
  const rows=[['run_id','candidate','call_id','assignment_kind','berth','segment_type','start_kst','completion_kst','confirmation_scope']];
  c.vessel_results.filter(r=>r.assignment_kind!=='EVALUATION_TAIL').forEach(r=>{
    const scope=r.assignment_kind==='COMMITTED'||Date.parse(r.work_start_at)<Date.parse(state.as_of)+24*3600000?'CONFIRMED':'TENTATIVE';
    for(const [kind,intervals] of [['WORK',r.work_intervals||[]],['NIGHT_STOP',r.night_pause_intervals||[]],['WEATHER_STOP',r.weather_pause_intervals||[]]]){
      intervals.forEach(([a,b])=>rows.push([current.run_id,c.label,r.call_id,r.assignment_kind,r.berth_id,kind,time(a),time(b),scope]));
    }
  });
  const blob=new Blob(['\ufeff'+rows.map(row=>row.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob), link=document.createElement('a');
  link.href=url;link.download=`docktimize-${current.date}-${c.label}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('copy-report').onclick=async()=>{if(!selectedCandidate())return;try{await navigator.clipboard.writeText(reportText());message('보고서를 복사했습니다.')}catch(_){message('클립보드 접근을 허용한 뒤 다시 시도하세요.',true)}};
$('confirm-action').onclick=async()=>{
  const c=selectedCandidate();if(!c||state.current.applied_candidate_id)return;
  // The existing API owns confirmation validation and persistence.
  await act('/api/apply',{label:c.label},'선택한 후보의 당일 00:00 일정을 확정합니다...');
};
$('candidates').onclick=event=>{
  if(busy)return;
  const button=event.target.closest('[data-candidate]');
  if(button){selectedId=button.dataset.candidate;renderScheduler()}
};
$('compare-baseline').onchange=renderTimeline;
$('go-confirm').onclick=()=>navigate('confirm');
document.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>navigate(button.dataset.page));
async function refreshStatus(){
  if(busy)return;busy=true;setBusy(true);
  try{const next=await api('/api/status');
    if(next.api_version!=='daily-planning-v2')throw Error('현재 주소가 새 일별 계획 API에 연결되어 있지 않습니다. 서버를 다시 빌드해주세요.');
    state=next;pendingWorkbook=null;$('uploadPreview').innerHTML='';message('')}
  catch(e){message(`서버 연결 실패: ${e.message}`,true);$('engine-status').textContent='API 연결 실패'}
  finally{busy=false;setBusy(false);if(state)render();else $('engine-status').textContent='API 연결 실패'}
}
$('refresh-status').onclick=refreshStatus;
refreshStatus();
