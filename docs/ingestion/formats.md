# Formats

Tiers of support, not a count. "60+ MIME types" does not mean 60 types are equally well handled.

## Tier A — full extraction and enrichment

| Group | Formats | Extraction | Tier |
|-------|---------|------------|------|
| Text & markup | `txt md rst html csv tsv json yaml xml log` | native parse, **no LLM** | small |
| PDF — digital | `pdf` with a text layer | text extraction | large |
| PDF — scanned | `pdf` image-only | **OCR — separate pipeline** | multimodal |
| Office | `docx xlsx pptx odt ods` | structured parse | large |
| Email | `eml msg mbox` | MIME parse + thread rebuild | medium |
| Images | `jpg png webp heic tiff gif` | caption + OCR | multimodal |
| Audio | `mp3 m4a wav ogg opus amr flac` | transcription + diarization | omni |
| Video | `mp4 mov webm mkv` | audio → transcript, keyframes → caption | omni |
| Code | `py js ts go rs java swift sql sh tf` | language detect, structure | medium |
| Calendar / contacts | `ics vcf` | structured → canonical `Event` / `Person` | small |
| Archives | `zip tar gz 7z` | expand + recurse, capped | — |

**`ics` and `vcf` are the highest value per unit cost in the whole list** — they map directly onto
canonical types with zero LLM involvement. Worth prioritising well above their apparent
importance.

## Tier B — text extraction, shallow structure

`epub` `mobi` `tex` `rtf` `pages` `numbers` `parquet` `sqlite` `gpx` `kml` `geojson` `svg`

## Tier C — stored, metadata-only

DICOM, CAD, 3D meshes, design files, unrecognised binaries. Versioned, tagged and findable by
filename and context; not content-searchable. Nothing is rejected.

## Eight gotchas that decide whether this works

| Issue | Why it bites |
|-------|--------------|
| **HEIC** | The iPhone default — likely the highest-volume image format — and it needs `libheif`, absent from most base images |
| **Digital vs scanned PDF** | Different products. One is cheap text extraction; the other is OCR through a vision model. Detect before routing or the cost model is off by an order of magnitude |
| **`mbox`** | Takeout exports are multi-GB single files holding tens of thousands of messages. Must stream and fan out; one file can legitimately generate 50k jobs |
| **Voice notes** | `amr` and `opus` are what WhatsApp and Telegram actually send — more important than `flac` |
| **Encrypted files** | Password-protected PDFs and zips must fail with a clear status, never crash a worker or retry-loop |
| **Huge spreadsheets** | A 500k-row `xlsx` would blow a workspace embedding budget on one file. Needs chunk strategy plus max-chunks-per-doc |
| **Text encoding** | Latin-1, Windows-1252 and Shift-JIS appear in real exports. Without detection you embed mojibake |
| **Macro-enabled Office** | `docm` / `xlsm` are a malware vector specifically because team members download each other's uploads |

## Cost

Audio and video are the expensive tier by a wide margin. Ten hours of uploaded video, transcribed
and keyframe-captioned on a user's own API key, is a large unbudgeted bill. Gate media
transcription behind explicit per-org opt-in with a visible cost estimate.
