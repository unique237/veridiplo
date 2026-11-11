# Policy-as-code rules (`kb/policy_rules/`)

Rules are YAML lists (schema = CLAUDE.md §4.3) evaluated by `app/policy/engine.py` at five **checkpoints**. Every rule cites
real KB text through `justified_by` (chunk ids or semantic anchors such as `CM-DP-2024#consent`, resolved through
`kb/chunks/anchors.json`). Integrity is enforced by `app/policy/loader.py::validate_rules()` and `tests/unit/test_policy_rules.py`.

| File | Content |
|---|---|
| `10_consent_purpose.yaml` | PR-CONSENT-001/002/003, PR-PURPOSE-001, PR-BULK-001 |
| `20_reporting.yaml` | PR-REDACT-001, PR-NOACCUSE-001, PR-EVIDENCE-001, PR-HUMAN-REVIEW-001, PR-REGISTRAR-001, PR-APPROVAL-AUDIT-001, PR-HOLDER-NOTICE-001 |
| `30_transfer_retention_llm.yaml` | PR-CROSSBORDER-001/002, PR-LLM-TRANSFER-001, PR-RETENTION-001 |
| `context_schema.yaml` | **machine-readable context contract** (namespaces, keys, defaults, functions, decision semantics) |
| `examples.yaml` | context -> expected fired rules / decision fixtures (QA + engine tests) |

## Rule fields

Required (§4.3): `id, checkpoint, when, action, message_fr, message_en, justified_by, pipeline_node, readiness_dims`.
Optional: `kind` (`legal` | `legal_interpretation` | `proposal`), `proposal: true`, `justification_note` (mandatory when
`proposal: true` — says what is ours and what real text is closest), `note`, `guards_node` (Y.3172 node the checkpoint protects),
`gap_id` (seed gap P*/S* this rule relates to — GapAnalyst links runtime gaps to it), `params` (machine-readable parameters /
obligations, e.g. `retention_days: 90`, `notify_holder: true`), `scenarios` (S1–S7 that exercise it).

* `kind: legal` — implements an obligation read directly in a binding KB text.
* `kind: legal_interpretation` — our operationalisation of a binding text (the chunk is cited, the interpretation is ours).
* `kind: proposal` — our own governance rule, **not law** (`proposal: true`). Examples: 20-holders/hour limit, 90-day retention,
  wording ban, registrar gate, 0.6 confidence threshold.

## Checkpoints and actions

| Checkpoint | Meaning |
|---|---|
| `before_ingest` | a document/upload is about to be stored (no rule yet — see gap P3 / runtime gaps) |
| `before_search` | a verification over archives is about to run |
| `before_report` | a verdict / evidence pack / explanation is about to be built |
| `before_share` | a generated output is about to be released to a recipient |
| `before_export_abroad` | data/derived output/prompt is about to leave Cameroon (foreign requester, cloud LLM) |

Actions: `allow | redact | require_consent | block | escalate`. Final decision of a checkpoint = strongest fired action
(`block > escalate > require_consent > redact > allow`); nothing fired = `allow` with `fired: []`. A fired `allow` rule is still
recorded (its `params` are obligations such as the transfer flag). On evaluation error: fail closed (`escalate` + audit + gap).

## Evaluation contract (IMPORTANT for the engine author)

`when` is a Python-syntax expression evaluated with **`simpleeval.EvalWithCompoundTypes`** (tested with simpleeval 1.0.8):

* **Use `EvalWithCompoundTypes`**, not `SimpleEval`: the plain evaluator raises `FeatureNotAvailable: List` for `x in ['a','b']`.
* Context = nested **plain dicts**; attribute syntax works on dicts (`request.consent_token`, verified) so no `SimpleNamespace`
  wrapper is needed. Subscript syntax (`request['consent_token']`) also works.
* **Every namespace and every key must always be present** (None / 0 / `[]` / `False` when unknown): a missing key raises
  `AttributeDoesNotExist`, a missing namespace `NameNotDefined`. `context_schema.yaml -> defaults` gives the None-filled context.
* Namespaces: `request, verifier, upload, verdict, review, output, llm` (keys documented in `context_schema.yaml`).
* Allowed constructs: `and/or/not`, comparisons, `is None`, `is not None`, `is not True`, `in`/`not in` with list literals, `len()`,
  `+`. **Not allowed**: comprehensions, lambdas, method calls on values (e.g. `.strip()`), anything else. Pre-compute helpers
  in the context instead (`review.justification_length`, `verdict.unknown_checks`).
* Functions the engine must register: `len(x)`, `contains_any(text, words)` (case- and accent-insensitive substring, `None` -> False),
  `intersects(list_a, list_b)`.
* `loader.validate_rules()` statically checks that every `namespace.key` and function used in a rule exists in the schema, so a
  typo in a rule fails CI instead of failing at run time.

Minimal engine core (this is exactly what the reference test uses):

```python
from simpleeval import EvalWithCompoundTypes
ev = EvalWithCompoundTypes(names=context, functions={"len": len, "contains_any": ..., "intersects": ...})
fired = [r for r in rules_for_checkpoint(cp) if ev.eval(r["when"])]
```

## Context keys (summary — authoritative list in `context_schema.yaml`)

| Namespace | Keys |
|---|---|
| `request` | `consent_token, consent_valid, legal_basis, purpose, requester_type, requester_country, batch_size, scope` |
| `verifier` | `id, queries_last_hour` |
| `upload` | `age_days` |
| `verdict` | `label, needs_human, confidence, unknown_checks, evidence_count` |
| `review` | `status, action, justification, justification_length` |
| `output` | `text, fields, audience, scope` |
| `llm` | `provider, sends_personal_data` |

## Notes for the other agents

* **PR-NOACCUSE-001** scans `output.text` (FR+EN concatenated) for: faux, fake, fraud(e/uleux), falsifi*, forged, forgery, contrefa*,
  counterfeit, usurp*, imposteur, fraudster. The Reporter's neutral templates must avoid these words entirely (also in
  negations such as "aucun faux n'a été détecté").
* **PR-RETENTION-001** needs a purge job reading `params.retention_days` (90, *our proposal*).
* **PR-BULK-001**: `verifier.queries_last_hour` counts distinct holders queried by the verifier in the previous 60 minutes, excluding the current request.
* **PR-APPROVAL-AUDIT-001**: `params.alert: supervisor` — the engine/scenario runner must raise a supervisor alert (S7).
* `justified_chunks` (added by `loader.load_rules()`) gives the resolved chunk ids to display in the UI ("click a rule -> KB text").
* Rules that cite a *summary* chunk or a comparison text (GDPR, AI Act, UAE) say so in `note`/`justification_note`; those texts are not binding in Cameroon.

## Honest limits

* The `justified_by` anchors point to chunks whose text genuinely supports the cited idea (checked by regex at build time —
  an anchor is created only if the regex matches a real chunk). Cameroon's Law 2024/017 was read from an **OCR** of the Presidency's
  certified copy; OCR errors are possible, verify against the Journal officiel before legal use.
* Several rules are proposals precisely because no Cameroonian text covers the decision (see `docs/gaps.md`).
