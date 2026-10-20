# docs

Written analysis and artefacts, split by the dataset phase each belongs to.

| Folder | Contents |
|---|---|
| `diabetic/` | UCI Diabetes 130-US Hospitals — the four training tracks' validation reports, band thresholds, calibration curves, ROC curve, classification reports, feature mapping and label definition |
| `cms/` | CMS DE-SynPUF — the migration guide and the feasibility plan |
| `mimic/` | MIMIC-IV — the ICD explainer and disease landscape, the cohort code index, the feature spec, the ICD feature experiment, and the live band thresholds |
| `deliverables/` | Client-facing decks and documents |

Documents at this level span every phase:

| File | |
|---|---|
| `project_timeline.md` | What was built, in order, with each figure traced to its artefact |
| `project_comparison_baseline_vs_current.md` | Baseline against the current model |
| `HANDOVER.md` | File-by-file architecture reference |
| `ROADMAP.md` | Planned work |
| `pilot_ingestion_build.md` | How hospitals' data comes in: the ingest API, the plug-and-play upload, database pulls, re-scoring |
| `ai_models.md` | Choosing the AI model for each feature (Gemini, Claude, OpenAI, a local Ollama model), keys, and what each feature sends |

**Read at runtime, not documentation:** `mimic/band_thresholds_mimic.json` is the live
risk-band configuration — `api/mimic_scoring.py` and three scripts read it. See the Risk bands
section of the root `README.md` before editing it.
