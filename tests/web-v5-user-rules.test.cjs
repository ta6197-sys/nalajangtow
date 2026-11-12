'use strict';
// Run after installation modifies the existing frontend model.
const test = require('node:test');
const assert = require('node:assert/strict');
const M = require('../web/v5-preview-model.js');
const row = (id, berth, etb, etu) => ({call_id:id,berth_id:berth,
  phase_policy_version:'V5_USER_ETU_SWITCHING_20261112_V1',
  direction_seconds:3600,switching_rule_seconds:5400,etb_at:etb,etu_at:etu});
test('ETU is a point, not a duration', () => {
  assert.equal(M.pointPercent('2026-11-12T11:00:00+09:00',
    '2026-11-12T10:00:00+09:00','2026-11-12T12:00:00+09:00'),50);
});
test('switching is shown only when a following call is affected', () => {
  const a=row('A','BERTH-04','2026-11-12T08:00:00+09:00','2026-11-12T10:00:00+09:00');
  const b=row('B','BERTH-04','2026-11-12T11:30:00+09:00','2026-11-12T13:00:00+09:00');
  const meta=[{call_id:'B',eta_at:'2026-11-12T08:30:00+09:00'}];
  const edges=M.switchingEdges([a,b],meta);
  assert.equal(edges.length,1);
  assert.equal(edges[0].from,'A');assert.equal(edges[0].to,'B');
  assert.equal(M.switchingEdges([a],meta).length,0);
  assert.equal(M.switchingEdges([a,b],[{call_id:'B',eta_at:'2026-11-12T12:00:00+09:00'}]).length,0);
});
test('identical reference berth labels never merge independent berth IDs', () => {
  const a=row('A','BERTH-03','2026-11-12T08:00:00+09:00','2026-11-12T10:00:00+09:00');
  const b=row('B','BERTH-11','2026-11-12T11:30:00+09:00','2026-11-12T13:00:00+09:00');
  assert.deepEqual(M.switchingEdges([a,b],[{call_id:'B',eta_at:'2026-11-12T08:00:00+09:00'}]),[]);
});
