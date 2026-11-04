const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const drain=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){
  const row={work_intervals:[['2026-11-06T07:00:00+09:00','2026-11-06T17:23:00+09:00'],['2026-11-07T06:50:00+09:00','2026-11-07T08:00:00+09:00']],night_pause_intervals:[['2026-11-06T17:23:00+09:00','2026-11-07T06:50:00+09:00']],night_pause_minutes:807,weather_pause_intervals:[],call_id:'CALL-1',berth_id:'BERTH-01',assignment_kind:'PLANNED',work_start_at:'2026-11-06T07:00:00+09:00',completion_at:'2026-11-07T08:00:00+09:00',total_wait_minutes:60,demurrage_increment_to_completion_cents:12500,allowed_minutes:100,laytime_used_minutes:200,demurrage_minutes:100};
  const a={label:'A',candidate_id:'candidate-a',cost_cents:12500,wait_minutes:60,status:'OPTIMAL',weather_pause_minutes:30,assignments:[row],vessel_results:[row],berth_change_count:0,start_shift_minutes:0,compared_tentative_call_count:0};
  const b={...a,label:'B',candidate_id:'candidate-b',cost_cents:25000,wait_minutes:30,vessel_results:[{...row,total_wait_minutes:30,berth_id:'BERTH-02'}]};
  return {api_version:'daily-planning-v2',daylight_windows:[{sunrise_at:'2026-11-06T06:49:00+09:00',sunset_at:'2026-11-06T17:23:00+09:00'},{sunrise_at:'2026-11-07T06:50:00+09:00',sunset_at:'2026-11-07T17:22:00+09:00'},{sunrise_at:'2026-11-08T06:51:00+09:00',sunset_at:'2026-11-08T17:21:00+09:00'}],as_of:'2026-11-06T00:00:00+09:00',latest_state_at:null,sunrise_at:'2026-11-06T06:49:00+09:00',sunset_at:'2026-11-06T17:23:00+09:00',calls:[{id:'CALL-1',contract_id:'CT-1',known_at:'2026-11-06T00:00:00+09:00',nor_at:'2026-11-06T06:00:00+09:00',available_at:'2026-11-06T06:00:00+09:00',status:'REGISTERED',cargo_quantity_kg:1000,work_minutes:100}],contracts:[{id:'CT-1'}],upload_locked:true,uploaded_files:[],status_counts:{},current:{run_id:'run-1',date:'2026-11-06',solver_status:'OPTIMAL',applied_candidate_id:null,candidates:[a,b]},history:[]};
}
// Minimal DOM harness: execute the shipped script and inspect its actual controls.
// Browser layout and accessibility are checked separately against the running app.
async function boot(data=fixture()){
  const elements=new Map(), calls=[], downloads=[];
  class Element{
    constructor(id){this.id=id;this.value='';this.textContent='';this.disabled=false;this.hidden=false;this.checked=false;this.dataset={};this.attrs={};this.classes=new Set();this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key),add:key=>this.classes.add(key),remove:key=>this.classes.delete(key)};this.files=[]}
    set innerHTML(value){this.markup=value;for(const match of value.matchAll(/id="([^"]+)"/g))if(!elements.has(match[1]))elements.set(match[1],new Element(match[1]));if(['editCall','editContract','newContract'].includes(this.id))this.value=value.match(/value="([^"]+)"/)?.[1]||''}
    get innerHTML(){return this.markup||''}
    setAttribute(k,v){this.attrs[k]=v}
    click(){this.onclick?.()}
  }
  for(const match of html.matchAll(/id="([^"]+)"/g)){assert(!elements.has(match[1]),'duplicate HTML ID');elements.set(match[1],new Element(match[1]))}
  const pages=['situation','compare','confirm'].map(name=>elements.get(`page-${name}`));
  const nav=['situation','compare','confirm'].map(page=>{const e=new Element('nav-'+page);e.dataset.page=page;return e});
  const document={getElementById:id=>{assert(elements.has(id),'Missing DOM ID: '+id);return elements.get(id)},querySelectorAll:selector=>selector==='.page'?pages:selector.includes('data-page')||selector==='nav button'?nav:[],createElement:()=>new Element('download')};
  let server=structuredClone(data), responder=null;
  const context=vm.createContext({document,window:{scrollTo(){}},navigator:{clipboard:{writeText:async text=>{context.copied=text}}},fetch:async(url,options)=>{calls.push({url,options});if(responder)return responder(url,options);return {ok:true,json:async()=>structuredClone(server)}},Intl,Date,Number,String,Set,Map,Blob,URL:{createObjectURL:blob=>{downloads.push(blob);return 'blob:test'},revokeObjectURL(){}},setTimeout:fn=>fn(),confirm:()=>true,FileReader:class{readAsDataURL(){this.result='data:application/octet-stream;base64,QQ==';this.onload()}}});
  vm.runInContext(source,context);await drain();
  return {el:id=>elements.get(id),nav,pages,calls,downloads,context,setServer:x=>server=structuredClone(x),respond:fn=>responder=fn,select:id=>elements.get('candidates').onclick({target:{closest:()=>({dataset:{candidate:id}})}})};
}
test('loads existing API results, converts cents, shows daylight and escapes input',async()=>{
  const data=fixture();data.calls[0].id='<img src=x onerror=alert(1)>';
  const ui=await boot(data);
  assert.equal(ui.calls[0].url,'/api/status');
  assert.match(ui.el('candidates').innerHTML,/\$125\.00/);
  assert.match(ui.el('inputTable').innerHTML,/&lt;img/);
  assert.doesNotMatch(ui.el('inputTable').innerHTML,/<img/);
  assert.match(ui.el('daylight-summary').innerHTML,/06:49 \/ 17:23/);
  assert.equal(ui.el('planButton').disabled,true);
  assert.equal(ui.el('app').attrs['aria-busy'],'false');
});
test('selecting B updates report and timeline without confirming; A is also confirmable',async()=>{
  const ui=await boot();
  assert.equal(ui.el('confirm-action').disabled,false);
  ui.select('candidate-b');
  assert.match(ui.el('selected-badge').textContent,/후보 B/);
  assert.match(ui.el('timeline').innerHTML,/BERTH-02/);
  assert.match(ui.el('report-outline').innerHTML,/\$250\.00/);
  assert.equal(ui.calls.length,1);
  assert.equal(ui.el('download-report').disabled,true);
});
test('confirmation uses chosen label and locks actions; reload restores confirmed selection',async()=>{
  const data=fixture(),ui=await boot(data);ui.select('candidate-b');
  data.current.applied_candidate_id='candidate-b';ui.setServer(data);
  await ui.el('confirm-action').onclick();
  const request=ui.calls.find(c=>c.url==='/api/apply');
  assert.deepEqual(JSON.parse(request.options.body),{label:'B'});
  assert.equal(ui.el('confirm-action').disabled,true);
  assert.equal(ui.el('operateButton').hidden,false);
  assert.equal(ui.el('download-report').disabled,false);
  const restored=await boot(data);
  assert.match(restored.el('selected-badge').textContent,/후보 B/);
});
test('CSV distinguishes confirmed day from future tentative assignments',async()=>{
  const data=fixture();data.current.applied_candidate_id='candidate-a';
  data.current.candidates[0].vessel_results.push({...data.current.candidates[0].vessel_results[0],call_id:'=SUM(1)',work_start_at:'2026-11-07T07:00:00+09:00',completion_at:'2026-11-08T08:00:00+09:00'});
  const ui=await boot(data);ui.el('download-report').onclick();
  const csv=await ui.downloads[0].text();
  assert.match(csv,/confirmation_scope/);assert.match(csv,/CONFIRMED/);assert.match(csv,/TENTATIVE/);assert.match(csv,/'=SUM/);
});
test('empty inputs and missing candidates render without offering confirmation',async()=>{
  const data=fixture();Object.assign(data,{as_of:null,calls:[],contracts:[],current:null,upload_locked:false});
  const ui=await boot(data);
  assert.equal(ui.el('planButton').disabled,true);assert.equal(ui.el('go-confirm').disabled,true);assert.equal(ui.el('confirm-action').disabled,true);
  assert.match(ui.el('candidates').innerHTML,/계산 후 표시/);
});
test('day advancement with next-plan failure displays advanced state and does not retry',async()=>{
  const data=fixture();data.current.applied_candidate_id='candidate-a';const ui=await boot(data);
  Object.assign(data,{as_of:'2026-11-07T00:00:00+09:00',current:null,next_plan_error:'solver failed'});ui.setServer(data);
  await ui.el('operateButton').onclick();
  assert.match(ui.el('message').innerHTML,/24시간 운영은 완료/);
  assert.match(ui.el('timestamp').textContent,/2026-11-07/);
  assert.equal(ui.calls.filter(c=>c.url==='/api/operate').length,1);
  assert.equal(ui.el('operateButton').hidden,true);
  assert.equal(ui.el('planButton').disabled,false);
});
test('failed response refreshes committed state without replaying mutation; double submit blocked',async()=>{
  const data=fixture(),ui=await boot(data);let rejectRequest;
  ui.respond((url)=>url==='/api/apply'?new Promise((_,reject)=>{rejectRequest=reject}):Promise.resolve({ok:true,json:async()=>structuredClone(data)}));
  const first=ui.el('confirm-action').onclick();
  assert.equal(ui.el('app').attrs['aria-busy'],'true');
  await ui.el('confirm-action').onclick();
  data.current.applied_candidate_id='candidate-a';rejectRequest(new Error('connection lost'));await first;
  assert.equal(ui.calls.filter(c=>c.url==='/api/apply').length,1);
  assert.equal(ui.el('confirm-action').disabled,true);assert.equal(ui.el('app').attrs['aria-busy'],'false');
});
test('workbook changes are previewed before applying and token is sent unchanged',async()=>{
  const ui=await boot();ui.el('workbook').files=[{name:'input.xlsx',size:100}];
  ui.respond(async(url)=>({ok:true,json:async()=>url==='/api/upload/preview'?{token:'preview-token',changes:[{kind:'CALL_ADDED',id:'new-call'}]}:fixture()}));
  await ui.el('uploadForm').onsubmit({preventDefault(){}});
  assert.equal(ui.calls.filter(c=>c.url==='/api/upload/apply').length,0);
  assert.match(ui.el('uploadPreview').innerHTML,/new-call/);
  ui.el('applyWorkbook').onclick();await drain();
  const request=ui.calls.find(c=>c.url==='/api/upload/apply');
  assert.equal(JSON.parse(request.options.body).token,'preview-token');
});

test('timeline draws only actual work segments and shades forbidden night windows',async()=>{
  const ui=await boot();
  const timeline=ui.el('timeline').innerHTML;
  assert.equal((timeline.match(/class="timeline-block"/g)||[]).length,2);
  assert.match(timeline,/class="night-window"/);
  assert.match(timeline,/class="berth-occupancy"/);
  assert.match(ui.el('assignment-table').innerHTML,/야간 중단/);
  assert.match(ui.el('assignment-table').innerHTML,/2026-11-07 06:50/);
});
test('wrong API version is explicitly reported as a backend mismatch',async()=>{
  const data=fixture();delete data.api_version;
  const ui=await boot(data);
  assert.match(ui.el('message').innerHTML,/새 일별 계획 API에 연결되어 있지 않습니다/);
});
