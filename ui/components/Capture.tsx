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

async function toBase64(blob: Blob): Promise<string> {
  const buffer = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < buffer.length; i += step) {
    binary += String.fromCharCode(...buffer.subarray(i, i + step));
  }
  return btoa(binary);
}

export function humanBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function Capture({
  onSubmit,
  busy,
}: {
  onSubmit: (name: string, mime: string, base64: string, size: number) => Promise<void>;
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
      if (kind === "video" && video.current) {
        video.current.srcObject = media;
        await video.current.play().catch(() => undefined);
      }
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

  async function send() {
    const payload = blob.current;
    if (!payload) return;
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
    await onSubmit(name, payload.type, await toBase64(payload), payload.size);
    setPreview(null);
    blob.current = null;
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
        <video ref={video} muted playsInline className="preview" />
      )}

      {preview && (
        <div className="preview-wrap">
          {preview.mime.startsWith("image/") && <img src={preview.url} alt="" className="preview" />}
          {preview.mime.startsWith("audio/") && <audio src={preview.url} controls className="preview" />}
          {preview.mime.startsWith("video/") && <video src={preview.url} controls className="preview" />}
          <div className="row" style={{ marginTop: 10 }}>
            <span className="chip">{preview.mime || "unknown type"}</span>
            <span className="chip">{humanBytes(preview.size)}</span>
            <button onClick={send} disabled={busy}>
              {busy ? "Uploading…" : "Ingest it"}
            </button>
          </div>
        </div>
      )}

      {error && <p className="err">{error}</p>}
    </div>
  );
}
