# AI models: choosing them, keys, and running one locally

The product uses a language model for four things. Which model serves each is chosen by the
superadmin on **Settings → AI**; nothing else depends on the provider.

| Feature | What it does | What the model is sent | Without a model |
|---|---|---|---|
| **Chatbot** | Picks one of our predefined queries for the question, then phrases the result | The question, the last 8 messages, and the query result: the user's own patients' ids, scores, diagnoses | Hidden |
| **Explanations on patient pages** | "What would lower this risk", the wording around the cost return, and the explanation of each week's change | One patient's scores, risk factors, diagnosis, dates. Never the patient's id | Hidden; the cost figures still show, they are computed by the server |
| **Model Studio** | Proposes the training plan, writes the pipeline code, words the verdict | Column summaries only (names, types, ranges, the values of short category lists). Never a patient row | Works: name and type rules, the standard pipeline, the rules' own verdict |
| **Reading uploaded files** | Data page upload: which file and column is which, which column is the readmission outcome, what the hospital's codes mean, and a short summary | Column summaries only (names, types, missing rates, the values of short category lists). Never a row, and never the values of a column named like a name, address, phone, email or national id | Name-matching and value rules; most exports need nothing more |

Code: `api/llm.py` (who serves what), `api/llm_providers.py` (Gemini, Claude, OpenAI, Ollama),
`api/ai_settings.py` (the settings page's API), `frontend/src/components/settings/AiSettings.jsx`.

## Choosing models

1. **Settings → AI → Providers.** Paste a key for Gemini, Claude or OpenAI, or give the address of a
   local Ollama server. Keys are stored encrypted with `INGEST_SECRET_KEY` and never shown again.
   A key can instead be given to the server as `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY` or `OLLAMA_BASE_URL`; the page shows which.
2. **Features.** For each, pick a provider and a model. The model list is fetched from the provider,
   so new models appear without a code change. **Test** sends one small question and checks whether
   the model can call tools; the chatbot needs that.
3. **Save.** The app hides the AI controls of any feature that is off.

With nothing chosen, a `GEMINI_API_KEY` on the server serves every feature, as before this page
existed:

| Feature | Gemini model | If it is slow, busy or not on the key |
|---|---|---|
| Model Studio | `STUDIO_GEMINI_MODEL`, default `gemini-flash-latest` | `GEMINI_MODEL` takes over |
| Chatbot, explanations, reading uploaded files | `GEMINI_MODEL`, default `gemini-flash-lite-latest` | - |

A Gemini model chosen on the settings page for Model Studio falls back the same way. Model Studio
uses the stronger model because its plan matters: on the UCI diabetes file the lighter model took
the encounter number for the patient id and flagged no leak; the stronger one chose the patient
number, kept a patient's stays together, and flagged the discharge disposition (deaths and hospice
cannot be readmitted). On 20 October 2026 `gemini-flash-latest` took about a minute per call.

Without any model, Model Studio's plan comes from name and type rules: the outcome, a repeating
patient column as the patient id, `*_id` code columns as categories, and columns named like
something known only after discharge (`days_to_readmission`, `death_date`) dropped as likely
leakage. The statistical leak scan runs either way.

## Patient details

The chatbot and the patient-page explanations send patient details. They only run on a provider
marked **May receive patient details**:

- **A local model** is marked by default: nothing leaves the server.
- **A cloud provider** must be marked by the superadmin, and only once the hospital has a
  data-protection agreement with it (for example Gemini through Google Cloud's Vertex AI, OpenAI's
  or Anthropic's enterprise terms). On Google's free tier, what is sent may be used to improve
  Google's products; use a paid key.
- **A Gemini key in the server's environment** from before this page existed stays marked, so an
  existing deployment keeps working. Untick it to stop.

**AI features on** switches all four off at once.

## A local model on the clinic's server (Ollama)

1. Run Ollama next to the app. In the clinic's Docker Compose file:
   ```yaml
   ollama:
     image: ollama/ollama
     volumes: ["ollama:/root/.ollama"]
     restart: unless-stopped
     # With an NVIDIA GPU:
     # deploy: {resources: {reservations: {devices: [{capabilities: [gpu]}]}}}
   ```
2. Pull a model once:
   ```bash
   docker compose exec ollama ollama pull qwen2.5:14b
   ```
3. On Settings → AI, give Ollama the address `http://ollama:11434`, choose the model for each
   feature, and press **Test**.

Which model:

| Model | Hardware | Good for |
|---|---|---|
| `qwen2.5:14b`, or another 14B instruct model | GPU with ~16 GB, or a fast CPU (slow) | Every feature; tool calling is reliable |
| `llama3.1:8b`, `qwen2.5:7b` | GPU with ~8 GB, or CPU | Model Studio and reading files; the chatbot is usable but misses some questions |

Any other server that speaks the OpenAI API (vLLM, LM Studio, llama.cpp) works through the
**OpenAI** provider with its address.

## Cloud models

Use the provider's fast, inexpensive tier for the chatbot and explanations (Gemini Flash, Claude
Haiku 4.5, OpenAI's mini models) and, if wanted, a stronger one for Model Studio's code (Gemini
Pro, Claude Sonnet 5.5, OpenAI's flagship). Model Studio works without any of them.
