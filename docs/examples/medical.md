# Medical — A Patient Timeline

Encounters, labs, imaging and notes accumulate across systems over years. The question is always
some form of *"what is this patient's history with X?"*

## Setup

```
producer   whk_01J…  EHR webhook       inbound_auth: signature
producer   crw_01J…  lab crawler       enumerate, watermark on ResultDateTime
producer   crw_01J…  imaging crawler   tree, materialise scanned PDFs

memory types   encounter    ttl null       on_expiry keep_members
               observation  ttl null       keep_members
case type      patient      identifiers[]  MRN, NHS number, internal id
```

Everything correlates on **MRN — a deterministic join**, not entity resolution.

## Records

**A lab result** — arrives as a record, no file:

```json
{ "external_id": "LAB-8841207", "event_time": "2024-03-14T09:20:00Z",
  "content": { "kind": "inline", "text": "Creatinine 1.8 mg/dL (H)" },
  "case":   { "external_id": "MRN-A12345", "case_type": "patient" },
  "memory": { "type": "observation", "key": "MRN-A12345:labs" },
  "metadata": { "tags": ["source:lab", "panel:renal"] } }
```

After normalization and enrichment:

```json
{ "canonical_type": "Observation",
  "identifiers": ["MRN-A12345", "LAB-8841207"],
  "event_time": "2024-03-14T09:20:00Z",
  "facets": { "analyte": "creatinine", "value": 1.8, "unit": "mg/dL",
              "abnormal": true, "reference_high": 1.3 },
  "classification": "regulated:phi",
  "derived": { "served_by_model": "gemma4:12b (local)", "fallback_depth": 0 } }
```

**An imaging report** — a scanned PDF, so a different pipeline:

```json
{ "external_id": "IMG-55120", "event_time": "2024-03-15T11:02:00Z",
  "content": { "kind": "pending", "provider": "pacs-export",
               "resource_id": "study/1.2.840...", "connection_id": "conn_01J…" },
  "case": { "external_id": "MRN-A12345", "case_type": "patient" } }
```

W2 fetches it, detects **no text layer**, and routes to OCR — a different cost profile and a
different agent from a digital PDF. That detection happens before routing, or the cost model is
wrong by an order of magnitude.

## The timeline

```
GET /api/v1/cases/{id}/timeline

2019-06-02  encounter    Cardiology consult          [note]
2019-06-02  observation  Echo: EF 55%                [report]
2024-03-14  observation  Creatinine 1.8 (H)          [lab]     ← abnormal
2024-03-15  imaging      CT abdomen — see report     [scanned] ← OCR path
2024-03-19  encounter    Nephrology referral         [note]
```

**Ordered by `event_time`, not ingestion.** All of it was backfilled last Tuesday. Order by
`created_at` and the 2019 echo appears after the 2024 CT — a timeline that renders perfectly and
misleads clinically.

## What the design contributes

| Mechanism | Why it matters here |
|-----------|--------------------|
| **Cases declared, never inferred** | Two patients named *Maria Garcia* must never merge. Entity resolution would try |
| **Identifier correlation** | MRN match is a join. No inference, no confidence score, no error rate |
| **`event_time`** | The single most important field in a clinical timeline |
| **Classification-gated inference** | `regulated:phi` pins to local models and **fails closed** rather than falling through to a third party |
| **Break-glass** | Emergency access with justification, scope, time limit and an audit record |
| **Audit on every read** | *"Who accessed this record in March?"* is a question that gets asked |
| **Legal hold** | A record under litigation hold survives an erasure request, and the response says so |

## The constraint

> The pipeline ships a **Medical/DICOM agent**, so HIPAA is in scope. In a hosted deployment that
> requires a BAA with **every** sub-processor touching PHI — including inference providers.
> **This is a self-hosted-only story** unless the cloud inference choice changes.

Which is why `classification-gated inference` is a Phase 1 item: every inference call made before
that check exists is an untracked disclosure.
