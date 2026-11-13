import {
  isEducationScopeConflictStatus,
  isLatestRequest,
  matchesEvidenceSelection,
  reconcileEffect,
} from '../src/api/schema31Contracts.ts';

const assert = (condition: unknown, message: string): asserts condition => {
  if (!condition) throw new Error(message);
};

assert(isEducationScopeConflictStatus(404), 'education 404 must refresh the latest list');
assert(isEducationScopeConflictStatus(409), 'education 409 must refresh the latest list');
assert(!isEducationScopeConflictStatus(500), 'server errors must not be treated as scope conflicts');

let currentRequest = 1;
const requestA = currentRequest;
currentRequest = 2;
const requestB = currentRequest;
assert(!isLatestRequest(requestA, currentRequest), 'late A evidence must be ignored after B starts');
assert(isLatestRequest(requestB, currentRequest), 'B evidence must remain current');

assert(
  matchesEvidenceSelection('EVENT_B', 'EVENT_B', 'b'.repeat(64), 'b'.repeat(64)),
  'save must target the inspected peer and fingerprint',
);
assert(
  !matchesEvidenceSelection('EVENT_B', 'EVENT_A', 'a'.repeat(64), 'a'.repeat(64)),
  'a stale peer must not be saved after selection changes',
);
assert(
  reconcileEffect(20).includes('save up to 20'),
  'reconcile disclosure must describe its save effect',
);

console.log('schema31 interactions ok');
