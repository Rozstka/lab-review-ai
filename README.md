# Lab Review AI

A working OCR → structured laboratory data → human review portfolio MVP. **Synthetic data only; no diagnosis or clinical interpretation.**
FastAPI, Tesseract, optional local LLM, Pydantic, SQLAlchemy, PostgreSQL and Docker.

## End-to-end flow
1. `POST /documents/image`: upload a PNG/JPEG (<=2 MB, <=12 megapixels) with `patient_id=SYN-001`. Or submit a synthetic text fixture to `/documents/text`.
2. Tesseract reads the image. Demo mode parses three explicit analytes: Hemoglobin, Glucose and WBC. Ollama mode extracts schema-constrained JSON, then every machine-generated field is checked against its literal source line.
3. The result remains `pending_review`, even when validation passes. Inspect OCR text, measurements, units, reference intervals and `GET /documents/{id}/source`.
4. `POST /documents/{id}/review` with `expected_revision`, `decision`, and a review note. Correct measurements if needed. Unknown source lines require `acknowledge_unparsed=true` after inspection.
5. Only approved results appear at `/patients/{id}/records`. Decisions and before/after values are written atomically to an audit table. Stale/repeated reviews return 409.

The source intervals are copied, not medical rules. Unit checking enforces the demo schema, not clinical validity.
Corrected values represent an explicit human override and are preserved in the audit. There is no automatic approval or connection to a medical record system.

## Demonstration
Install Tesseract locally (`brew install tesseract` on macOS, `apt-get install tesseract-ocr` on Debian/Ubuntu); Docker includes it.
The committed synthetic scan is generated from `examples/synthetic-lab.txt`:
```sh
python scripts/make_scan.py
# With the API running and API_TOKEN loaded from .env:
python scripts/demo.py
```
The demo uploads the real image and prints the pending document ID; it does not approve on behalf of a person.
Example review body in Swagger, after inspecting the source:
```json
{"expected_revision":1,"decision":"approve","note":"Compared every field against the synthetic source","acknowledge_unparsed":false}
```
To correct fields, include the full `measurements` array. All items retain a source line for traceability.

## Deliberate MVP boundaries
English PNG/JPEG scans and three named analytes; PDF, handwriting, arbitrary layouts and Russian OCR are not supported.
Synchronous OCR has a 20-second timeout; add a queue for larger workloads. The literal parser is intentionally conservative.
Initial text/image source is retained in the database, not a public file URL. No real patients, claims of clinical accuracy, commercial customers or business impact.

## Quick start — Docker with PostgreSQL
```sh
python3 scripts/configure.py
docker compose up --build -d
docker compose logs api
```
API: http://localhost:8000/docs. `GET /health` reports mode and database connectivity.
Open `.env` locally and paste `API_TOKEN` into the Swagger **Authorize** dialog. Do not share that value.
`docker compose down` stops the demo and retains its database volume.

## Local start — Python 3.12+, SQLite by default
```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
python scripts/configure.py
set -a
. ./.env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Run setup only once; it intentionally refuses to overwrite `.env`. SQLite provides a quick local path.
For PostgreSQL set `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB` before starting.
Generated Compose passwords are hex, avoiding URL-encoding ambiguity. Supply a dedicated database.

## LLM mode
Default `LLM_MODE=demo` is deterministic and requires no model or paid API key; it is clearly labelled in responses.
For real model inference install [Ollama](https://ollama.com/), run `ollama pull qwen2.5:3b`, set `LLM_MODE=ollama`, and restart.
Default model: `OLLAMA_MODEL=qwen2.5:3b`. Local API: `OLLAMA_URL=http://localhost:11434`.
Compose connects to host Ollama through `host.docker.internal`; ensure it is reachable from Docker on your OS.
Do not expose an unauthenticated Ollama server to the internet.
The adapter uses Ollama's [chat API](https://docs.ollama.com/api/chat), schema-constrained JSON, timeouts and bounded output.
A small local model can still fail validation; the API preserves the original document with `extraction_status=needs_manual_entry`, an explicit error in `issues`, and an empty measurements array. A reviewer must transcribe/correct it or reject it. It never substitutes a plausible answer.

## Tests
```sh
pytest -q
# Optional: use a disposable PostgreSQL database; tests DROP this project's tables.
TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/TEST_DB pytest -q
```
Tests cover both persistence paths, API validation, provider contract and failure handling.
GitHub Actions installs dependencies, runs SQLite and PostgreSQL tests, and builds the image.
`requirements.lock` pins resolved packages; `requirements.txt` lists direct dependency ranges.
See [SECURITY.md](SECURITY.md) for deployment boundaries and [reports](reports/) for measured smoke-test evidence.

## Reproduce the small regression evaluation

Run `python scripts/evaluate.py`. Reports explicitly identify the tiny synthetic sample; the measurements are not claims of production or medical accuracy.

Optional real-model check: `python scripts/live_smoke.py` uses an isolated in-memory database and the configured Ollama model, and saves the response and elapsed time. This is a smoke test, not a model benchmark.

### Observed OCR limitation

The extra regression set uses a different default font: Tesseract misreads the WBC unit in all four scans, so exact name/value/unit precision and recall are 8/12 (66.7%). Unit validation flags those records for correction and prevents approval. The committed main fixture extracts all three numeric values correctly, but its WBC unit can also require correction to `10^9/L` after comparing with the image. This is why every document requires human review; no automatic clinical accuracy claim is made.

If a `Patient:` identifier exists in the source and differs from the requested synthetic patient ID, ingestion returns 422. Sources without an identifier rely on the operator's supplied ID.

### Real-model image behavior

The small local model sometimes tries to repair the OCR-misread WBC unit. Exact source verification rejects that unsupported alteration. The image is retained in the manual-review queue with no trusted extracted values; the text-only model fixture succeeds. See `reports/image-llm-smoke.json`. This safe abstention is an explicit current limitation.

Reproduce the image + LLM check with `python scripts/image_llm_smoke.py` (requires Tesseract and local qwen2.5:3b). A safely queued `needs_manual_entry` result is expected for the difficult WBC unit.
