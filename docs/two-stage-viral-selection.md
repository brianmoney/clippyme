# Two-stage viral selection

ClippyMe's CommandCode path can run an editorial pipeline instead of asking one
model to both find and promote clips:

```
transcript → discovery (GLM-5.3 Flash, high recall)
           → consolidation / fair cap
           → independent review (Claude Opus 5.5 by default)
           → boundary refinement + dedupe
           → promotional copy for survivors only
           → existing render / reframe / subtitle / smart-cut pipeline
```

`VIRAL_MODE=auto` (the default) selects this path when
`VIRAL_PROVIDER=openai`; Gemini keeps the legacy single-stage flow. The UI
exposes the same controls under **Settings → Two-stage selection**.

## Why the stages are separate

The legacy prompt mixes three jobs: find candidates, score them, and write
titles/hooks. A single model tends to over-produce competent-but-flat clips and
its numerical score anchors the next decision. The two-stage design separates:

- **Recall** at discovery, where a missed exceptional moment is the expensive
  error. Overlapping alternatives are allowed and no titles/hooks/scores are
  requested.
- **Judgement** at review, where a different model sees the real transcript
  excerpts (never the discovery model's ratings) and can reject everything.
- **Copy** last, only for clips that survived, so a title cannot compensate for
  a weak selection.

## Stage 1 — discovery

- Model: `VIRAL_DISCOVERY_MODEL` (default `z-ai/glm-5.3-flash`).
- Long transcripts are split into overlapping word chunks
  (`VIRAL_DISCOVERY_CHUNK_WORDS`, default 6000; overlap `..._OVERLAP`, default
  400) so a chunk boundary cannot hide a moment.
- Each chunk gets the full local transcript plus a TOON word table with **global
  word indices**. Candidates are anchored by `word_start_index` /
  `word_end_index`; the application, not the model, owns the final timestamps.
- Candidates carry a stable content-addressed id, exact excerpt, context,
  opening line, strongest quote, a short `why`, an editorial category, and up to
  three alternative boundaries.
- Per chunk the prompt asks for
  `ceil(max_candidates / chunks) + 4` proposals (capped by
  `VIRAL_MAX_CANDIDATES`). Consolidation removes only near-identical proposals
  (≤0.75 s on both edges or IoU ≥ 0.92) and keeps meaningfully different
  overlaps. The global cap is applied round-robin across chunks so one long
  section cannot crowd out the rest.

The model output is JSON after a `### JSON ###` delimiter and is passed through
the existing strict → clean → json_repair → reformat parsing chain.

## Stage 2 — independent review

- Model: `VIRAL_REVIEW_MODEL` (default `claude-opus-5-5`, routed automatically to
  CommandCode's `/messages` endpoint).
- The prompt contains the real transcript table plus each candidate's exact
  excerpt and surrounding context. It does **not** contain discovery scores
  (there are none) and candidates are presented chronologically.
- Ten dimensions are scored 0–10: opening hook, emotional impact,
  surprise/novelty, shareability, self-contained clarity, narrative payoff,
  retention potential, quotability, information value, audience relevance.
- The reviewer also flags late payoffs, background dependence, better openings,
  shorter-edit opportunities, duplicates, and misleading hooks.
- Ranking: independent scores → ordered `shortlist` → bounded pairwise
  comparisons (at most 20, top candidates only) for close calls. The application
  computes the quality mean, applies `VIRAL_MIN_QUALITY` (default 6.0), rejects
  duplicates/misleading hooks, applies pairwise tie-breaks only when the score
  gap is ≤ 0.75, and caps at `VIRAL_MAX_SELECTED_CLIPS` (default 8).
- **Reject-all is valid.** When nothing clears the bar the job finishes with zero
  clips; the orchestrator does not substitute TextTiling or a whole-video render
  for a reviewed decision (`reviewed_empty=True`).

## Audio cues (ElevenLabs Scribe)

When the transcript comes from ElevenLabs Scribe, its provider-tagged
non-speech events (`(laughter)`, `(applause)`, `(cheering)`, `(music)`, …) are
part of the selection evidence, not just the single-stage prompt:

- discovery receives the events that intersect its chunk, with timestamps and a
  rule to prefer candidates that end just after a reaction cue;
- review receives each candidate's own cues (inside the interval plus a small
  tail) so `emotional_impact` / `narrative_payoff` can use the real reaction;
- cue text is signal only — it is never copied into excerpts, quotes or copy;
- audio events are part of the discovery/review artifact hashes, so adding or
  removing tags invalidates a cached analysis rather than silently reusing the
  old (cue-blind) result.

Deepgram and local Whisper produce no events; the prompts state that none were
tagged and selection proceeds on words alone.


## Boundaries, dedupe and copy

- Review may suggest replacement word indices. The application validates them,
  snaps to real word starts/ends, clamps to the source duration, trims the intro
  if over `max_duration`, extends the tail if under `min_duration`, then the
  existing `snap_clips_to_transcript` pass performs the final
  word/sentence/silence snapping.
- Final overlap resolution runs after boundary refinement (IoU ≥ 0.8 keeps the
  higher-ranked clip).
- Copy is generated only for the survivors by `VIRAL_COPY_MODEL` (blank = the
  discovery model). If the copy call fails, grounded fallback copy is derived
  from the selected excerpt; no invented facts are introduced.
- Final clips keep every field the dashboard and publishing integrations already
  use, plus `candidate_id`, `review` (rubric, rank, flags, original boundaries)
  and `discovery` (category, quote, alternatives, word indices).

## Per-job clip count

The Create tab's custom clip count is sent as `target_clips` (Auto omits it).
It is not free-text instructions any more:

- legacy single-stage: the prompt asks for about N moments instead of the
  hard-coded 3–15 wording;
- two-stage: discovery is told the target (and its candidate budget scales to
  roughly `3 × target`, bounded by `VIRAL_MAX_CANDIDATES`), review is told to
  select at most N, and N becomes the final selection ceiling. Review can still
  return fewer — or zero — when the material does not support the target.

`POST /api/process` and `/api/batch` accept `target_clips` (1–50; the clip
schema caps usable output at 20). Live-monitor jobs keep using
`CLIPPYME_MAX_CLIPS`.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `VIRAL_MODE` | `auto` | `auto` = two-stage on CommandCode, single on Gemini. `two_stage` / `single` force one. |
| `VIRAL_DISCOVERY_MODEL` | `z-ai/glm-5.3-flash` | Stage 1 model. |
| `VIRAL_REVIEW_MODEL` | `claude-opus-5-5` | Stage 2 model. |
| `VIRAL_COPY_MODEL` | blank | Copy model; blank reuses the discovery model. |
| `VIRAL_MAX_CANDIDATES` | `32` | Recall cap before review (4–60). |
| `VIRAL_MAX_SELECTED_CLIPS` | `8` | Hard ceiling after review (1–20). |
| `VIRAL_MIN_QUALITY` | `6.0` | Mean rubric floor (0–10). |
| `VIRAL_TWO_STAGE_FALLBACK` | `single` | `single` runs the legacy selection if a stage fails; `off` returns no clips and keeps artifacts for retry. |
| `VIRAL_DISCOVERY_FALLBACK_MODELS` | `z-ai/glm-5.3-flashx` | Tried if the discovery model is unavailable/overloaded. |
| `VIRAL_REVIEW_FALLBACK_MODELS` | `claude-sonnet-5-5` | Tried if your CommandCode plan excludes Opus. The report and logs record the model actually used. |
| `VIRAL_STAGE_MAX_ATTEMPTS` | `2` | Transient-error retries per stage call. |
| `VIRAL_REASONING_EFFORT` | `low` | OpenAI-style thinking budget. GLM-5.3 Flash cannot turn thinking fully off and otherwise burns its output budget on large prompts; `low` prevents empty/truncated JSON and upstream 524s. |
| `VIRAL_MAX_ANALYSIS_COST` | `0` | Recorded in the report; not enforceable because CommandCode does not publish API pricing. |
| `CLIPPYME_VIRAL_EVAL` | `0` | Also run the legacy single-stage path on the same transcript and write `viral_eval_report.json` (roughly doubles analysis cost). |
| `CLIPPYME_FORCE_VIRAL_ANALYSIS` | `0` | Ignore cached stage artifacts and rerun. |

**Important:** Opus 5.5 requires a CommandCode Provider-or-higher plan (or extra
on-demand usage). If your plan excludes it, the review falls through to
`VIRAL_REVIEW_FALLBACK_MODELS` and the report/logs say so — do not describe that
result as an Opus review. Upgrade the plan or set the fallback explicitly.

## Artifacts and inspection

Per job directory:

- `two_stage_discovery.json` — chunks, every candidate, discovery usage/latency.
- `two_stage_review.json` — evaluations, flags, pairwise comparisons, shortlist,
  rejection reasons, review usage/latency/coverage.
- `two_stage_copy.json` — per-clip copy and whether fallback copy was used.
- `two_stage_report.json` — lean report: models (requested + actual), counts,
  rank/quality/start/end per survivor, token usage and latency per stage, cost
  fields (`null` when unknown), rejection reasons, coverage.
- `viral_eval_report.json` — only when evaluation mode ran.

Inspect with `GET /api/analysis/{job_id}` or:

```bash
python scripts/viral_eval.py --job-dir output/<job_id>
python scripts/viral_eval.py --two-stage two_stage_result.json \
  --legacy legacy_single_stage.json --reference reference.json
```

The result payload also includes a compact `two_stage` report, which the
dashboard renders as an expandable analysis panel on the results page. JSON is
blocked from the public `/videos` mount, so the API/script is the supported
inspection path.

## Retry / failure behaviour

- Every stage persists before the next one starts. A failed review keeps
  `two_stage_discovery.json`, so a retry skips transcription and discovery.
- A failed copy stage uses grounded fallback copy instead of failing the job.
- If discovery or review fails entirely, the dispatcher either runs the legacy
  single-stage path (`VIRAL_TWO_STAGE_FALLBACK=single`, the default) or returns
  no clips (`off`). The report records the failure.
- If all copies of a stage fail, the job does not present unreviewed candidates
  as approved: it either falls back to the explicitly legacy path or produces
  zero clips.

## Measured example (one representative transcript)

A real CommandCode run was performed on
`tests/fixtures/viral_transcript.json` (372 words / 156.2 s, capped at 10
candidates, 5 final clips, quality floor 6.0). This is a small illustrative
fixture, not a benchmark.

| Run | Model(s) | Clips | Tokens in/out | Latency |
|---|---|---|---|---|
| Legacy single-stage | `claude-sonnet-5-5` | 3 | 11,014 / 2,415 | 19.1 s |
| Two-stage | discovery `z-ai/glm-5.3-flash`, review requested `claude-opus-5-5` but plan-blocked → `claude-sonnet-5-5`, copy `z-ai/glm-5.3-flash` | 2 | 17,813 / 22,784 | 259.9 s stage time (discovery was reused from the preceding attempt; 146.5 s wall on the resumed run) |

Comparison (manual reference ranges `[0–40.2]`, `[101.6–128.4]`):

- 1 overlapping selection, top-5 precision 1.0, reference recall 1.0 on this
  2-range reference set.
- Legacy also kept a 57.9 s version of the bank story and a 58 s product story;
  two-stage kept a tighter 40.2 s bank story and a 26.8 s product-deletion payoff.
- Two-stage rejected the closing "janitor" segment at 4.5/10 ("ends without a
  payoff"); legacy kept it at score 62.
- Cost is **unknown** (CommandCode does not publish pricing), not zero.

Operational notes from this run:

- `z-ai/glm-5.3-flash` returned transient Cloudflare 524/503 errors on one
  attempt; the configured fallback (`z-ai/glm-5.3-flashx`) / retry chain handled
  it. If it persists, raise `VIRAL_STAGE_MAX_ATTEMPTS` or put `flashx` first.
- GLM copy generation was slow (125 s, 8.5k output tokens). Set
  `VIRAL_COPY_MODEL` to a faster model if that latency matters.
- One fixture and a hand-authored reference cannot prove real-world superiority.
  Use `CLIPPYME_VIRAL_EVAL=1` on representative videos and record user
  acceptance/edits over time before treating the scores as predictive.
