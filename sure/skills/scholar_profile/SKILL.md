# /scholar_profile

Extract a comprehensive scholar profile from DBLP, OpenAlex, Google Scholar, and personal pages. Generate a structured system prompt for LLM-based persona simulation.

## Arguments

- `scholar_name` (required) — Full name of the scholar, e.g. "Yoshua Bengio"
- `native_name` (optional) — Native-language name for Chinese scholar web search
- `seed_url` (optional) — Personal page or Google Scholar profile URL
- `language` (optional) — Output language: `en` (default) or `zh`

## Environment Variables

The pipeline reads all configuration from `process.env` (inherited by child processes). No `.env` files or dotenv.

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `OPENAI_API_KEY` | Yes | — | Standard PI env var. Set via `pi auth` or environment. |
| `LLM_BASE_URL` | No | - | OpenAI-compatible API endpoint. |
| `LLM_MODEL` | No | - | Model name for LLM calls. |
| `TAVILY_API_KEY` | No | — | For web search (interviews, lab pages). Pipeline skips web search if unset. |
| `HTTP_PROXY` / `HTTPS_PROXY` | No | — | PI sets these automatically from `settings.json` `httpProxy`. |

## Workflow

1. Parse the arguments from the invocation. If `scholar_name` is missing, report an error and finish with status `failed`.

2. Check that `OPENAI_API_KEY` is available. If not, report error and finish with `failed`.

3. Run the pipeline script:
   ```bash
   python <package_dir>/scripts/run_pipeline.py \
     --scholar-name "<scholar_name>" \
     --output-dir "<run_dir>" \
     [--native-name "<native_name>"] \
     [--seed-url "<seed_url>"] \
     [--language "<language>"]
   ```

4. After the pipeline completes, call `sure_update_state` to report progress:
   ```
   sure_update_state(
     phase={ id: "complete", label: "Pipeline complete", status: "success" },
     counters={ completed_stages: 5, total_stages: 5 },
     artifacts=[...]
   )
   ```

5. Write `manifest.json` to the run directory with this envelope:
   ```json
   {
     "schema_version": "1",
     "run_id": "<from context>",
     "skill_name": "scholar_profile",
     "status": "success",
     "created_at": "<ISO timestamp>",
     "inputs": {
       "scholar_name": "...",
       "native_name": "...",
       "seed_url": "...",
       "language": "en"
     },
     "outputs": {
       "system_prompt": "<run_dir>/system_prompt.md",
       "system_prompt_docx": "<run_dir>/system_prompt.docx",
       "scholar_csv": "<run_dir>/scholars.csv",
       "source_urls": "<run_dir>/source_urls.json",
       "mainline_json": "<run_dir>/mainline.json"
     },
     "validation": {
       "system_prompt_words": <word count>,
       "total_papers": <paper count>,
       "sources_scraped": <source count>
     },
     "artifacts": [
       { "type": "markdown", "path": "<run_dir>/system_prompt.md" },
       { "type": "word", "path": "<run_dir>/system_prompt.docx" },
       { "type": "csv", "path": "<run_dir>/scholars.csv" },
       { "type": "json", "path": "<run_dir>/source_urls.json" },
       { "type": "json", "path": "<run_dir>/mainline.json" }
     ]
   }
   ```

6. Call `sure_finish` with:
   - `status`: "success" if all outputs exist and `system_prompt.md` has ≥ 500 words, otherwise "incomplete"
   - `manifest_path`: path to the manifest.json
   - `summary`: one-line description of what was produced

## Stage Details

| Stage | Script | Output |
|-------|--------|--------|
| 1. DBLP → CSV | `build_scholars_csv.py` | `scholars.csv` |
| 2. Source Discovery | `build_scholar_sources.py` | `<name>_sources.txt` |
| 3. Mainline Extraction | `scholar_mainline_builder.py` | `mainline_graph.json` |
| 4. Paper Scoring | `scholar_author_digest.py` | `paper_digest_scored.json`, `scholar_report.docx` |
| 5. Prompt Generation | `professor_system_prompt_builder.py` | `system_prompt.md`, `system_prompt.docx` |

## Failure Handling

If any stage fails:
- Log the error
- Try to continue with remaining stages if possible
- If the pipeline cannot complete, write an error manifest and call `sure_finish` with status `failed`
- Include error details in the manifest's `error` field

## Constraints

- Single scholar only (no batch)
- Requires `OPENAI_API_KEY` in environment
- Pipeline takes 5-15 minutes depending on web scraping speed
- Google Scholar search may fail without proxy — this is non-fatal, the pipeline continues with other sources
- `TAVILY_API_KEY` is optional; without it, the pipeline skips web search (interviews, lab pages) and relies on DBLP + OpenAlex + Wikipedia
