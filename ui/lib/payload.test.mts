import assert from "node:assert/strict";
import { test } from "node:test";
import { base64FromDataUrl } from "./payload.ts";

test("a recorded video survives the comma inside its own MIME type", () => {
  // What MediaRecorder actually produces. The comma in `vp8,opus` is why
  // splitting on the first comma sent `opus;base64,GkXfo...` to the API.
  const url = "data:video/webm;codecs=vp8,opus;base64,GkXfo59ChoEB";
  assert.equal(base64FromDataUrl(url), "GkXfo59ChoEB");
});

test("the paths that always worked keep working", () => {
  assert.equal(base64FromDataUrl("data:audio/webm;codecs=opus;base64,T2dnUw=="), "T2dnUw==");
  assert.equal(base64FromDataUrl("data:image/png;base64,iVBORw0K"), "iVBORw0K");
  assert.equal(base64FromDataUrl("data:application/pdf;base64,JVBERi0="), "JVBERi0=");
});

test("a payload is never mistaken for a header", () => {
  // Base64 has no comma in its alphabet, so the marker cannot appear twice in
  // a way that matters -- but a payload beginning with the marker's letters
  // must still come back whole.
  assert.equal(base64FromDataUrl("data:text/plain;base64,YmFzZTY0LA=="), "YmFzZTY0LA==");
});

test("something that is not a data URL is refused rather than guessed at", () => {
  // The old code returned everything after the first comma whatever it was, so
  // a reader that produced text instead of a data URL wrote nonsense into the
  // item and reported success.
  assert.equal(base64FromDataUrl("not a data url at all"), null);
  assert.equal(base64FromDataUrl("data:text/plain,hello%20there"), null);
  assert.equal(base64FromDataUrl("data:image/png;base64,"), null);
});
