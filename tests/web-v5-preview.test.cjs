'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const M = require('../web/v5-preview-model.js');
const row = {berthing_start_at:'2026-11-03T00:00:00+09:00',
  etb_at:'2026-11-03T01:00:00+09:00', work_start_at:'2026-11-03T02:00:00+09:00',
  work_intervals:[['2026-11-03T02:00:00+09:00','2026-11-03T04:00:00+09:00']],
  physical_clearance_at:'2026-11-03T05:00:00+09:00'};
test('Gantt uses actual timestamps',() => {
  const r=M.segments(row,'2026-11-03T00:00:00+09:00','2026-11-06T00:00:00+09:00');
  assert.equal(r.find(x=>x.kind==='work').widthPercent,100*2/72);
});
test('no fabricated inventory series',() => {
  assert.equal(M.validatedTankSeries({},'T'),null);
  const p=M.validatedTankSeries({tank_inventory:{T:[{at:'2026-11-03T00:00:00+09:00',inventory_bbl:'1/3'}]}},'T');
  assert.equal(p[0].exact,'1/3');
  assert.equal(p[0].numeric,1/3); // Chart uses an approximation; tooltip preserves exact.
});
test('no false final-domain optimality',() => {
  const w={summary:{solver_status:'OPTIMAL',final_domain_optimality_proven:false},
    final_candidate_domain:{optimality_proven:false}};
  assert.match(M.proof(w),/미증명/);
});
test('missing costs are not zero',() => assert.equal(M.cost(null),'미확정'));
test('cost improvement does not fabricate an invalid round',() => {
  const data=M.improvement([{round:0,validated_cost_cents:1200},{round:1,validated_cost_cents:null},
    {round:2,validated_cost_cents:900}]);
  assert.equal(data[1].bestCents,1200);
  assert.equal(data[2].bestCents,900);
});
test('escape untrusted strings',() => assert.equal(M.safe('<img>'),'&lt;img&gt;'));
