import assert from "node:assert/strict";
import { test } from "node:test";
import { describeError } from "./types.ts";

test("FastAPI's validation shape becomes a sentence, not [object Object]", () => {
  // The real payload behind "error when recording video".
  const detail = [{
    type: "value_error",
    loc: ["body", "items", 0, "content", "inline", "bytes_b64"],
    msg: "Value error, Only base64 data is allowed",
  }];
  assert.equal(
    describeError(detail, 422),
    "items.0.content.inline.bytes_b64: Value error, Only base64 data is allowed",
  );
});

test("more than one rejected field is reported, not just the first", () => {
  const detail = [
    { loc: ["body", "producer_id"], msg: "Field required" },
    { loc: ["body", "items"], msg: "Field required" },
  ];
  assert.equal(describeError(detail, 422),
    "producer_id: Field required; items: Field required");
});

test("an ordinary string detail is passed through unchanged", () => {
  assert.equal(describeError("payload exceeds the configured maximum", 413),
    "payload exceeds the configured maximum");
});

test("a shape nobody anticipated still says something", () => {
  // Two endpoints raise a dict detail. Better the JSON than the status alone,
  // and better the status alone than "[object Object]".
  assert.equal(describeError({ error: "conflict", current_state: "enriched" }, 409),
    '{"error":"conflict","current_state":"enriched"}');
  assert.equal(describeError(undefined, 500), "request failed (500)");
  assert.equal(describeError("", 404), "request failed (404)");
  assert.equal(describeError([], 422), "request failed (422)");
});
