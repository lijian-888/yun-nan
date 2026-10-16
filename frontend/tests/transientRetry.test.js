import test from "node:test";
import assert from "node:assert/strict";

import { withTransientRetry } from "../src/transientRetry.js";

test("a transient gateway failure is retried without surfacing an error", async () => {
  let calls = 0;
  const retries = [];
  const result = await withTransientRetry(async () => {
    calls += 1;
    if (calls === 1) throw Object.assign(new Error("入口等待超时"), { transient: true });
    return { record: { name: "美香占2号" } };
  }, { delays: [0], onRetry: (attempt) => retries.push(attempt) });

  assert.equal(calls, 2);
  assert.deepEqual(retries, [1]);
  assert.equal(result.record.name, "美香占2号");
});

test("a permanent application error is not retried", async () => {
  let calls = 0;
  await assert.rejects(() => withTransientRetry(async () => {
    calls += 1;
    throw new Error("品种不存在");
  }, { delays: [0, 0] }), /品种不存在/);
  assert.equal(calls, 1);
});

