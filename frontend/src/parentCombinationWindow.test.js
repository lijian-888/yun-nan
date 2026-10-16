import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_COMBINATION_BATCH_SIZE,
  combinationWindow,
  nextCombinationWindowSize,
} from "./parentCombinationWindow.js";

test("large recommendation groups render a bounded first window", () => {
  const items = Array.from({ length: 100 }, (_, index) => ({ id: index + 1 }));
  const window = combinationWindow(items);

  assert.equal(DEFAULT_COMBINATION_BATCH_SIZE, 10);
  assert.equal(window.visible.length, 10);
  assert.equal(window.remaining, 90);
  assert.equal(window.hasMore, true);
});

test("show-more advances by one bounded batch without exceeding the result set", () => {
  assert.equal(nextCombinationWindowSize(10, 100), 20);
  assert.equal(nextCombinationWindowSize(95, 100), 100);
  assert.equal(nextCombinationWindowSize(100, 100), 100);
});
