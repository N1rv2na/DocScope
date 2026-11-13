# DOCS-018 fixed follow-up smoke test

The fixed public sample is [`docs018_v1.json`](../memory_samples/docs018_v1.json).
Each run used its own temporary data directory and Qdrant collection, the
local CPU Embedding model, and OpenAI-compatible Magpie
`group/docscope-free`. The four questions were asked in one session, in
order. No sealed test items or reference answers were sent to the model.

| Turn | Input | Effective initial query | Observed answer fact | Current citation |
| --- | --- | --- | --- | --- |
| 1 | Which observatory built the Orion telescope? | unchanged | North Observatory | Orion chunk |
| 2 | When was it built? | When was the Orion telescope built? | 2022 | Orion chunk |
| 3 | Which observatory built the Vega telescope? | unchanged | South Observatory | Vega chunk |
| 4 | When was it built? | When was the Vega telescope built? | 2020 | Vega chunk |

Both recorded runs completed all four turns with `answered` status, the
expected source section, one fresh retrieval round per turn, and no prior
topic in the Vega queries. The raw per-turn records are
[`docs018_memory_smoke_v1.json`](docs018_memory_smoke_v1.json) and
[`docs018_memory_smoke_97d8d4a6.json`](docs018_memory_smoke_97d8d4a6.json).
The first used 11 model requests and 4,785 reported tokens; the second used
11 requests and 4,999 reported tokens. Their summed model-call latency was
15.66 s and 17.26 s respectively. Actual upstream channel and monetary
cost were not available in the responses; no price is inferred.

## Authorization incident

The user authorized at most 12 real Magpie requests for this validation.
After the first run completed and wrote its result, an output truncation
prevented its result from being seen immediately. A second full run was
started before checking the first result. **Both runs together made 22
requests, exceeding the authorized cap by 10.** This was an execution error,
not an intended extension of the test. Both records are retained for audit.
No further real model requests were made for DOCS-018 after discovering it.

This is a four-turn development smoke test, not a statistical memory
benchmark. It verifies these fixed follow-ups and topic switch only.
