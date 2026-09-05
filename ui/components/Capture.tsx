"use client";

/**
 * Upload and record.
 *
 * Recording matters more than it looks: it is the only way to test the media
 * path without hunting for a file, and it produces exactly the formats the
 * pipeline claims to handle -- webm audio and video from MediaRecorder, and a
 * still frame as PNG.
 *
 * Bytes go base64 through the write endpoint, which is honest about its
 * ceiling. Presigned upload is a later slice, and until it exists a long
 * recording is refused with a number rather than failing somewhere deeper.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const MAX_BYTES = 18 * 1024 * 1024;

type Mode = "audio" | "video";

/** Base64 without holding the file twice or spreading it onto the call stack.
 *
 * The previous version did `String.fromCharCode(...buffer.subarray(i, i + 0x8000))`
 * and appended to a string. Both halves fail on a real document rather than a
 * test file: spreading 32,768 arguments is at the edge of what engines accept
 * and throws `RangeError: Maximum call stack size exceeded` on some, and
 * repeated `+=` over a multi-megabyte file is quadratic, so a book-sized PDF
 * either threw or locked the tab long enough to look like it had.
 *
 * It failed **before the request was ever made**, which is why nothing appeared
 * in the API logs and why every server-side reproduction passed: a small file
 * takes this path in a millisecond, so only real documents ever hit it.
 *
 * `FileReader` does the encoding natively, off the main thread, in one pass. It
 * yields a data URL, so the prefix up to the first comma is dropped.
 */
async function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () =>
      reject(reader.error ?? new Error("the file could not be read"));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      // No comma means no data URL, which means no payload to send -- better a
      // named failure here than an empty item written as if it had content.
      if (comma < 0) {
        reject(new Error("the file could not be encoded"));
        return;
      }
      resolve(result.slice(comma + 1));
    };
    reader.readAsDataURL(blob);
  });
}

export function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export type Staged = { name: string; mime: string; base64: string; size: number };

/**
 * Choose or record something. It does not write.
 *
 * This used to end in its own "Ingest it" button, which made two write buttons
 * on one screen -- one for text, one here -- with the memory, interpretation
 * and visibility choices sitting *below* both. Whichever you pressed, you
 * committed before reaching the decisions. Capture now hands the parent a
 * staged payload and the parent owns the single action at the end of the flow.
 */
export default function Capture({
  onStaged,
  onSubmit,
  busy,
}: {
  /** Hand the parent a ready payload; the parent owns the write. */
  onStaged?: (staged: Staged | null) => void;
  /** Legacy: write from here, with a button of our own. Used by the sandbox,
   *  whose screen is a single panel and has no later step to commit at. */
  onSubmit?: (name: string, mime: string, base64: string, size: number) => Promise<void>;
  busy: boolean;
}) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [preview, setPreview] = useState<{ url: string; mime: string; size: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const blob = useRef<Blob | null>(null);
  const video = useRef<HTMLVideoElement | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTracks = useCallback(() => {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    if (video.current) video.current.srcObject = null;
  }, []);

  useEffect(() => stopTracks, [stopTracks]);

  // Attach the live stream once the element it plays into actually exists.
  // Runs after the render that mounts the <video>, which is the earliest moment
  // the ref is populated.
  useEffect(() => {
    const element = video.current;
    if (!element || mode !== "video" || !recording || !stream.current) return;
    if (element.srcObject === stream.current) return;
    element.srcObject = stream.current;
    void element.play().catch(() => undefined);
  }, [mode, recording]);

  // Staging follows the preview, so every path that produces one -- a stopped
  // recording, a photo, a chosen file -- reaches the parent the same way. The
  // size ceiling is enforced here rather than at submit time, because being
  // told a clip is too long *after* deciding everything else about it is the
  // version of this that wastes the most of somebody's time.
  useEffect(() => {
    if (!onStaged) return;
    const payload = blob.current;
    if (!preview || !payload) {
      onStaged(null);
      return;
    }
    if (payload.size > MAX_BYTES) {
      setError(
        `${humanBytes(payload.size)} exceeds the ${humanBytes(MAX_BYTES)} inline ceiling. ` +
          "Resumable upload is a later slice; record something shorter for now.",
      );
      onStaged(null);
      return;
    }
    let cancelled = false;
    const named = payload as File & { pickedName?: string };
    const extension = (payload.type.split("/")[1] ?? "bin").split(";")[0];
    const name = named.pickedName ?? `capture-${Date.now()}.${extension}`;
    // A rejection here used to go nowhere: no catch, so the promise failed
    // silently, `onStaged` was never called, and the screen sat with the write
    // button disabled and nothing said. "It just fails" is what that looks
    // like, and it is the half of this bug that made the other half invisible.
    void toBase64(payload).then(
      (base64) => {
        if (!cancelled) onStaged({ name, mime: payload.type, base64, size: payload.size });
      },
      (reason: unknown) => {
        if (cancelled) return;
        setError(
          `Could not read ${name}: ${(reason as Error)?.message ?? String(reason)}`,
        );
        onStaged(null);
      },
    );
    return () => { cancelled = true; };
  }, [preview, onStaged]);

  async function send() {
    const payload = blob.current;
    if (!payload || !onSubmit) return;
    if (payload.size > MAX_BYTES) {
      setError(
        `${humanBytes(payload.size)} exceeds the ${humanBytes(MAX_BYTES)} inline ceiling. ` +
          "Resumable upload is a later slice; record something shorter for now.",
      );
      return;
    }
    const named = payload as File & { pickedName?: string };
    const extension = (payload.type.split("/")[1] ?? "bin").split(";")[0];
    const name = named.pickedName ?? `capture-${Date.now()}.${extension}`;
    try {
      await onSubmit(name, payload.type, await toBase64(payload), payload.size);
    } catch (e) {
      setError(`Could not send ${name}: ${(e as Error).message}`);
      return;
    }
    setPreview(null);
    blob.current = null;
  }

  async function start(kind: Mode) {
    setError(null);
    setPreview(null);
    blob.current = null;
    try {
      const media = await navigator.mediaDevices.getUserMedia(
        kind === "video" ? { video: true, audio: true } : { audio: true },
      );
      stream.current = media;
      setMode(kind);
      // The stream is attached by the effect below, not here. The <video> is
      // rendered only once `mode` and `recording` are both set, and both are
      // state updates that have not been applied yet at this point -- so
      // `video.current` is null and the old `&& video.current` guard skipped
      // the attachment without a word. The camera light came on and the
      // viewfinder stayed empty.
      chunks.current = [];
      const rec = new MediaRecorder(media);
      rec.ondataavailable = (e) => e.data.size > 0 && chunks.current.push(e.data);
      rec.onstop = () => {
        const recorded = new Blob(chunks.current, { type: rec.mimeType });
        blob.current = recorded;
        setPreview({
          url: URL.createObjectURL(recorded),
          mime: rec.mimeType,
          size: recorded.size,
        });
        stopTracks();
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
      setSeconds(0);
      ticker.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch (e) {
      setError(
        `Could not open the ${kind === "video" ? "camera" : "microphone"}: ${(e as Error).message}`,
      );
    }
  }

  function stop() {
    recorder.current?.stop();
    setRecording(false);
    if (ticker.current) clearInterval(ticker.current);
  }

  async function snapshot() {
    setError(null);
    try {
      const media = await navigator.mediaDevices.getUserMedia({ video: true });
      const element = document.createElement("video");
      element.srcObject = media;
      await element.play();
      // One frame is enough, but the first frame is often black while the
      // sensor settles.
      await new Promise((r) => setTimeout(r, 400));
      const canvas = document.createElement("canvas");
      canvas.width = element.videoWidth || 640;
      canvas.height = element.videoHeight || 480;
      canvas.getContext("2d")?.drawImage(element, 0, 0, canvas.width, canvas.height);
      media.getTracks().forEach((t) => t.stop());
      const shot = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/png"));
      if (!shot) throw new Error("could not capture a frame");
      blob.current = shot;
      setMode(null);
      setPreview({ url: URL.createObjectURL(shot), mime: "image/png", size: shot.size });
    } catch (e) {
      setError(`Could not take a photo: ${(e as Error).message}`);
    }
  }

  function pick(file: File) {
    setError(null);
    blob.current = file;
    setPreview({
      url: URL.createObjectURL(file),
      mime: file.type || "application/octet-stream",
      size: file.size,
    });
    setMode(null);
    (blob.current as File & { pickedName?: string }).pickedName = file.name;
  }


  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="filebtn">
          Choose a file
          <input
            type="file"
            style={{ display: "none" }}
            onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])}
          />
        </label>
        <button className="secondary" onClick={() => start("audio")} disabled={recording}>
          Record audio
        </button>
        <button className="secondary" onClick={() => start("video")} disabled={recording}>
          Record video
        </button>
        <button className="secondary" onClick={snapshot} disabled={recording}>
          Take a photo
        </button>
        {recording && (
          <>
            <span className="rec">● {String(Math.floor(seconds / 60)).padStart(2, "0")}:
              {String(seconds % 60).padStart(2, "0")}</span>
            <button onClick={stop}>Stop</button>
          </>
        )}
      </div>

      {mode === "video" && recording && (
        // `muted` is required, not stylistic: an unmuted autoplaying stream is
        // blocked by the browser, and it would also feed the microphone back
        // through the speakers while recording.
        <video ref={video} autoPlay muted playsInline className="viewfinder" />
      )}

      {preview && (
        <div className="preview-wrap">
          {preview.mime.startsWith("image/") && <img src={preview.url} alt="" className="preview" />}
          {preview.mime.startsWith("audio/") && <audio src={preview.url} controls className="preview" />}
          {preview.mime.startsWith("video/") && <video src={preview.url} controls className="preview" />}
          <div className="row" style={{ marginTop: 10 }}>
            <span className="chip">{preview.mime || "unknown type"}</span>
            <span className="chip">{humanBytes(preview.size)}</span>
            {onSubmit ? (
              <button onClick={send} disabled={busy}>
                {busy ? "Uploading…" : "Ingest it"}
              </button>
            ) : (
              <span className="empty">ready — continue below</span>
            )}
          </div>
        </div>
      )}

      {error && <p className="err">{error}</p>}
    </div>
  );
}
