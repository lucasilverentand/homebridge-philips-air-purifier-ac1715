'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { TrailingValueDebouncer } = require('../trailing-value-debouncer');

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

test('applies only the final value after changes settle', async () => {
  const applied = [];
  const debouncer = new TrailingValueDebouncer(20, async (value) => applied.push(value));

  const first = debouncer.schedule(25);
  const second = debouncer.schedule(50);
  const third = debouncer.schedule(100);

  assert.deepEqual(await first, { applied: false, value: 25 });
  assert.deepEqual(await second, { applied: false, value: 50 });
  assert.deepEqual(await third, { applied: true, value: 100 });
  assert.deepEqual(applied, [100]);
});

test('serializes a final value that arrives while another apply is running', async () => {
  const applied = [];
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const debouncer = new TrailingValueDebouncer(10, async (value) => {
    applied.push(value);
    if (value === 25) await firstGate;
  });

  const first = debouncer.schedule(25);
  await wait(15);
  const second = debouncer.schedule(50);
  const third = debouncer.schedule(100);
  releaseFirst();

  assert.deepEqual(await first, { applied: true, value: 25 });
  assert.deepEqual(await second, { applied: false, value: 50 });
  assert.deepEqual(await third, { applied: true, value: 100 });
  assert.deepEqual(applied, [25, 100]);
});
