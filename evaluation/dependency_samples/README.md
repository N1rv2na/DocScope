# DOCS-012 fixed dependency sample

The two Markdown sources here are original, public demo text. The question,
expected bridge and answer in `docs012_v1.json` are offline review labels;
the online Agent receives only the uploaded Markdown text and user question.

Manual review checklist:

1. `aster-telemetry.md` names the service used by Aster Telemetry Console for
   audit export (`Polaris Relay`) but does not name its operating team.
2. `polaris-relay.md` names the operating team (`East Region Operations`) but
   does not mention Aster Telemetry Console or audit export.
3. The question omits `Polaris Relay`. A second query using that bridge must
   be planned after reading the first source, and the Run must identify the
   first source's actual `chunk_id`.
4. The answer requires both sources: one to identify the relevant service and
   one to identify its operator. A one-shot list of queries is not evidence
   that a second query depended on the first retrieval.

The owner confirmed this dependency sample on 2026-10-30 (`review_status` in
the JSON). The automated test exercises the graph and provenance checks with
a fake planner and retriever. It does not establish real-LLM accuracy.
