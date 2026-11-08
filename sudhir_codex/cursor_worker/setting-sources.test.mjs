import assert from "node:assert/strict";
import test from "node:test";

import { resolveSudhirCursorSettingSources } from "./setting-sources.mjs";

test("unset and none omit settingSources", () => {
  assert.equal(resolveSudhirCursorSettingSources(undefined), undefined);
  assert.equal(resolveSudhirCursorSettingSources(""), undefined);
  assert.equal(resolveSudhirCursorSettingSources("none"), undefined);
  assert.equal(resolveSudhirCursorSettingSources("OFF"), undefined);
});

test("all restores the previous ambient load", () => {
  assert.deepEqual(resolveSudhirCursorSettingSources("all"), ["all"]);
  assert.deepEqual(resolveSudhirCursorSettingSources("ON"), ["all"]);
});

test("comma lists pass through", () => {
  assert.deepEqual(resolveSudhirCursorSettingSources("project,user"), [
    "project",
    "user",
  ]);
});
