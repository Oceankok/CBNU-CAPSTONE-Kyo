// Run: npm test
import assert from 'node:assert/strict';
import {
  confidenceLevel,
  formatConfidence,
  isReviewOnly,
  passesConfidenceFilter,
} from '../src/confidence.ts';

const normal = { review_only: false, ai_confidence: 0.9 };
const zero = { review_only: false, ai_confidence: 0 }; // a measured 0 is still a number
const reviewOnly = { review_only: true, ai_confidence: null };
const legacy = { ai_confidence: 0.82 }; // old rows without the flag

assert.equal(isReviewOnly(normal), false);
assert.equal(isReviewOnly(zero), false);
assert.equal(isReviewOnly(legacy), false);
assert.equal(isReviewOnly(reviewOnly), true);
assert.equal(isReviewOnly({ ai_confidence: null }), true); // flag missing but no value

assert.equal(formatConfidence(normal), '90%');
assert.equal(formatConfidence(normal, 1), '90.0%');
assert.equal(formatConfidence(zero), '0%');
assert.equal(formatConfidence(reviewOnly), '검토 필요');
assert.notEqual(formatConfidence(reviewOnly), '0%');

assert.equal(passesConfidenceFilter(normal, 0.8), true);
assert.equal(passesConfidenceFilter(legacy, 0.9), false);
assert.equal(passesConfidenceFilter(zero, 0.1), false); // a real 0 is filtered out
assert.equal(passesConfidenceFilter(reviewOnly, 0.9), true); // never hidden by the slider

assert.equal(confidenceLevel(0.9), 'high');
assert.equal(confidenceLevel(0.7), 'mid');
assert.equal(confidenceLevel(0.5), 'low');
console.log('confidence ok');
