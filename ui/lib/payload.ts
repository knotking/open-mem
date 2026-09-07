/**
 * Reading a browser payload back out of a `FileReader` data URL.
 *
 * This is one function in `lib/` rather than a couple of lines inline because
 * the couple of lines were written twice, were wrong in the same way both
 * times, and were in components that nothing tests.
 */

/**
 * The base64 body of a `data:` URL, without its header.
 *
 * **Not the first comma.** A data URL is
 * `data:<mime>[;params];base64,<payload>`, and a MIME type is allowed commas
 * of its own: `MediaRecorder` produces `video/webm;codecs=vp8,opus`, so the
 * first comma sits inside the codec list and slicing there yields
 * `opus;base64,GkXfo59...`. The API refused that with "Only base64 data is
 * allowed" — correctly — and the console showed `[object Object]`, so
 * recording a video failed twice over and explained neither.
 *
 * Audio (`codecs=opus`), photos (`image/png`) and most chosen files carry no
 * comma, which is why every other path worked and this one did not.
 *
 * The `;base64,` marker is the honest anchor: base64's alphabet has no comma
 * in it, so the payload cannot contain one and the header always ends here.
 */
export function base64FromDataUrl(dataUrl: string): string | null {
  const marker = dataUrl.indexOf(";base64,");
  if (marker < 0) return null;
  const payload = dataUrl.slice(marker + ";base64,".length);
  return payload.length > 0 ? payload : null;
}
