# DOCS-019 independent memory comparison — synthetic dev v1

## Scope and frozen setup

The frozen input is [`docs019_v1.json`](../memory_samples/docs019_v1.json):
two synthetic conversations and 13 English questions per arm over the public
[`telescopes.md`](../../backend/tests/fixtures/telescopes.md) fixture. The
config is [`memory_dev_v1.json`](../configs/memory_dev_v1.json). The same
uploaded source, Qdrant 1.20.0 collection, local CPU Embedding model, and
Magpie `group/docscope-free` route were used for all three arms. The arms ran
sequentially: current question only, four-turn window with rewriting, then
window plus rolling summary with rewriting. Agent extra retries were disabled.
No sealed test, reference answer, or expected source label was supplied to a
model request; labels were applied only after each response.

The summary trigger was estimated `ceil(UTF-8 bytes / 3) >= 200` over
unsummarized user questions older than the four-turn window. This is an
estimate, not an exact Magpie tokenizer measurement. Each summary input was
bounded to 6000 bytes and the stored output to 1200 bytes.

## Observed results

| Arm | Answered | Correct fact | Correct citation section | Required query entity | Old topic in query | Old topic in answer | Requests | Summary calls completed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Current question only | 11/13 | 11/13 | 11/13 | 0/2 | 0/3 | 0/3 | 26 | 0 |
| Window + Rewrite | 13/13 | 12/13 | 12/13 | 1/2 | 1/3 | 1/3 | 37 | 0 |
| Window + Summary + Rewrite | 13/13 | 13/13 | 13/13 | 2/2 | 0/3 | 0/3 | 38 | 1 |

All 39 question attempts completed without a runtime error. The run made
**101 actual remote model requests**, under the authorized cap of 112. The
first arm has no history by design. Exactly one summary was generated, before
turn 8 of the long conversation; it was then reused for turn 9.

In the long conversation's final question, “When was the first telescope we
discussed built?”, the current-question arm abstained. The window-only arm
rewrote it as a Vega question and answered **2020** with a Vega citation,
which is wrong for the first discussed telescope. The summary arm rewrote it
as an Orion question and answered **2022** with an Orion citation. In the
short topic-switch conversation, the current-question arm abstained on
“When was it built?” while both memory arms correctly used Vega and cited its
chunk. All other scored turns were correct under this fixture's exact fact
and source-section labels.

The versioned [raw per-question JSON](docs019_memory_dev_v1.json) contains each
question, effective query, status, answer, citation IDs/sections, expected
labels, pollution flag, summary stage and request count. The labels are not
used by retrieval or prompting. A separate [offline scoring review](docs019_memory_dev_v1.review.json)
preserves the raw artifact's hash, adds the sample/source hashes, rejects
negated facts and conflicting years, and separately marks old-topic mentions
in answers. It used **no additional model requests**; its counts match the
table above. This remains a heuristic scorer, and the short answers were
manually checked against the two-sentence public fixture.
The linked config gained content-hash pins after the run to prevent future
sample drift; the original config snapshot remains inside the raw JSON.

## Limits and interpretation

- This is a **13-question synthetic development sample**, with repeated
  telescope questions and one long-distance referent. It is not a statistical
  Memory Benchmark, a sealed test result, or evidence of a general improvement.
- The route group may select different upstream channels. The actual channel
  and monetary cost were unavailable, so the three arms are not a strict
  same-channel causal comparison. Only the route ID was frozen.
- The raw JSON records Token diagnostics for **22 rewrite calls** (11 in each
  memory arm) and one summary call. The SSE stream does not expose Agent Planner/Answer
  diagnostics, and the temporary Run/Checkpoint stores were removed after
  this run. Thus **complete per-turn and total Token use cannot be reconstructed
  for this run**. The actual request counts above are complete. Future reruns
  now snapshot the Agent diagnostics before deleting temporary state.
- The run used the rewrite prompt version present when its process started.
  A subsequent wording correction explicitly allowed older-summary entity
  references while still disallowing factual claims. The 11 summary-arm
  rewrites above therefore measure the **previous wording**. One separate
  [real prompt smoke](docs019_rewrite_prompt_smoke.json) with a fixed
  Orion-first summary produced “When was the Orion telescope built?” with the
  final wording. It made one additional model request; **102 real requests in
  total** were made under the 112-request authorization. The smoke does not
  replace a new real three-arm run or measure answer correctness.
- This one long dialogue triggered one summary creation and later reuse. A
  second incremental update is covered by local API regression tests, not
  this real-model sample.
- The saved summary is a topic cue, not document evidence. Correct citation
  IDs and source sections are checked separately from answer facts; this toy
  source makes manual support review straightforward. Longer, more diverse
  conversations and ambiguous references remain unverified.
