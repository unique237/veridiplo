# VeriDiplo - A/B results (Version A ungoverned vs Version B policy-governed)

_Generated 2026-11-11T05:42:39+00:00 by `make demo` - SPECIMEN - SYNTHETIC DATA. LLM_PROVIDER=none._

## Summary

| Scenario | Version A (off) | Version B (on) | Expectations A | Expectations B |
|---|---|---|---|---|
| **S1** Genuine Licence, consent present | CONSISTENT / done | CONSISTENT / done | PASS (2/2) | PASS (23/23) |
| **S2** Mention altered (Passable → Bien), average raised | INCONSISTENT / done | INCONSISTENT / pending_review | PASS (3/3) | PASS (20/20) |
| **S3** HR firm: 200 names, no consent | bulk_records / done | BLOCKED / blocked | PASS (3/3) | PASS (17/17) |
| **S4** Request from a UAE employer via an embassy | CONSISTENT / done | CONSISTENT / done | PASS (3/3) | PASS (20/20) |
| **S5** 1998 PV missing | INCONSISTENT / done | INSUFFICIENT_EVIDENCE / done | PASS (4/4) | PASS (15/15) |
| **S6** OCR-hard name / reversed name order | INCONSISTENT / done | INSUFFICIENT_EVIDENCE / pending_review | PASS (2/2) | PASS (17/17) |
| **S7** Registrar approves without justification | INCONSISTENT / reviewed | INCONSISTENT / pending_review | PASS (4/4) | PASS (18/18) |

## S1 - Genuine Licence, consent present

_Licence authentique, consentement présent_

A genuine diploma specimen with a valid holder consent token. Both modes should return CONSISTENT; mode B must add citations, fired policy rules and a hash-chained audit trail, and surface gap P3 (no retention schedule).

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | done | done |  |
| verdict | CONSISTENT | CONSISTENT |  |
| blocked |  |  |  |
| needs_registrar |  |  |  |
| holder_notified |  |  |  |
| transfer_flag |  |  |  |
| output_scope | full_record | evidence_pack | yes |
| rules_fired |  | ['PR-REDACT-001'] | yes |
| gaps |  | ['P3'] | yes |
| evidence_cited | 5 | 5 |  |
| audit_events | 9 | 14 | yes |

**Key quotes**

- A: > OK - valid diploma. Record found: DAIROU Anne-Marie.
- B: > The diploma information is consistent with the institutional sources consulted (5 item(s) cited). Confidence: 0.92. [E1] [E2] [E3] [E4] [E5]
- B rule PR-REDACT-001 (redact): Non-essential data removed from the report: the verifier receives only the verdict, institution, diploma, session and evidence references.

**Expectations** (from `data/scenarios/S1.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | verdict | PASS |  |
| A | failing_checks | PASS |  |
| B | verdict | PASS |  |
| B | failing_checks | PASS |  |
| B | rule_fired:PR-REDACT-001 | PASS |  |
| B | rule_not_fired:PR-RETENTION-001 | PASS |  |
| B | rule_not_fired:PR-CONSENT-001 | PASS |  |
| B | rule_not_fired:PR-BULK-001 | PASS |  |
| B | rule_not_fired:PR-CROSSBORDER-001 | PASS |  |
| B | rule_not_fired:PR-NOACCUSE-001 | PASS |  |
| B | gap:P3 | PASS |  |
| B | citations_min | PASS |  |
| B | evidence_expected:pv/UFN_LINFO_2024.pdf | PASS |  |
| B | audit_chain_valid | PASS |  |
| B | registrar_review_required | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |

Audit chain: A valid (9 events), B valid (14 events). Cases: A `e867e85c-8001-4a25-bbd3-ec0c35c7b48d`, B `91594ca0-91ba-4821-91ac-8e631e7d838d`.

## S2 - Mention altered (Passable → Bien), average raised

_Mention modifiée (Passable → Bien) et moyenne relevée_

Tampered diploma: mention and average raised compared with the PV. Version A uses accusatory wording ('FAKE'); version B returns INCONSISTENT without accusation, escalates to the registrar and notifies the holder.

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | done | pending_review | yes |
| verdict | INCONSISTENT | INCONSISTENT |  |
| blocked |  |  |  |
| needs_registrar |  | True | yes |
| holder_notified |  | True | yes |
| transfer_flag |  |  |  |
| output_scope | full_record | none | yes |
| rules_fired |  | ['PR-NOACCUSE-001', 'PR-REDACT-001', 'PR-REGISTRAR-001'] | yes |
| gaps |  | ['P1', 'P3', 'P6'] | yes |
| evidence_cited | 5 | 5 |  |
| audit_events | 9 | 17 | yes |

**Key quotes**

- A: > FAKE diploma / FAUX DIPLÔME: the average, the mention does not match. Record found: Bertrand BELINGA. Fraud likely, forgery detected.
- B: > A documentary inconsistency was detected on the average, the mention compared with the records consulted (5 item(s) cited). This result describes a discrepancy between documents; it does not characterise the diploma or its holder. The case is referred to the institution's registrar for human review 
- B rule PR-REDACT-001 (redact): Non-essential data removed from the report: the verifier receives only the verdict, institution, diploma, session and evidence references.
- B rule PR-NOACCUSE-001 (block): Forbidden wording: the system never uses 'fake', 'fraud' or equivalents. It reports a documentary inconsistency and refers to the registrar; criminal qualification is for the competent authorities.
- B rule PR-REGISTRAR-001 (escalate): INCONSISTENT verdict held: approval by a human registrar is mandatory before any communication.

**Expectations** (from `data/scenarios/S2.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | verdict | PASS |  |
| A | failing_checks | PASS |  |
| A | text_contains:FAKE | PASS |  |
| B | verdict | PASS |  |
| B | failing_checks | PASS |  |
| B | rule_fired:PR-NOACCUSE-001 | PASS |  |
| B | rule_fired:PR-REGISTRAR-001 | PASS |  |
| B | gap:P1 | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |
| B | escalated_to_registrar | PASS |  |
| B | holder_notified | PASS |  |
| B | registrar_review_required | PASS |  |
| B | communicated_before_registrar_approval | PASS |  |
| B | citations_min | PASS |  |

Audit chain: A valid (9 events), B valid (17 events). Cases: A `e15d5774-dc23-4963-ba96-f5d14e7a594b`, B `54d32a6e-7a39-4c88-8c6c-2db9fea257df`.

## S3 - HR firm: 200 names, no consent

_Cabinet RH : 200 noms sans consentement_

A recruitment firm submits a bulk request for 200 holders (150 exist in the archive, 50 do not) without any consent token or legal basis. Version A returns the matching records; version B blocks the request (consent + bulk limit) and records gap P5.

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | done | blocked | yes |
| verdict |  | BLOCKED | yes |
| blocked |  | True | yes |
| needs_registrar |  |  |  |
| holder_notified |  |  |  |
| transfer_flag |  |  |  |
| output_scope | bulk_records | none | yes |
| rules_fired |  | ['PR-BULK-001', 'PR-CONSENT-001'] | yes |
| gaps |  | ['P5'] | yes |
| evidence_cited |  |  |  |
| audit_events | 5 | 5 |  |

**Key quotes**

- A: > 200 records returned.
- B: > Request refused by the governance policy: Search refused: no holder consent or legal basis. Limit exceeded: more than 20 holders per hour per verifier. Request blocked and flagged.
- B rule PR-CONSENT-001 (block): Search refused: no holder consent or legal basis.
- B rule PR-BULK-001 (block): Limit exceeded: more than 20 holders per hour per verifier. Request blocked and flagged.

**Expectations** (from `data/scenarios/S3.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | outcome | PASS |  |
| A | records_returned_min | PASS |  |
| A | blocked | PASS |  |
| B | outcome | PASS |  |
| B | blocked | PASS |  |
| B | records_returned | PASS |  |
| B | rule_fired:PR-CONSENT-001 | PASS |  |
| B | rule_fired:PR-BULK-001 | PASS |  |
| B | gap:P5 | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |
| B | no_personal_data_in_response | PASS |  |

Audit chain: A valid (5 events), B valid (5 events). Cases: A `284b78ba-88b0-4c34-84c0-9e1c6cdda6b3`, B `ad51c0e6-8833-45ef-a509-c9bdbc60b850`.

## S4 - Request from a UAE employer via an embassy

_Demande d'un employeur des Émirats via l'ambassade_

Genuine diploma verified for a requester located in the UAE (employer acting through an embassy), with consent. Version A returns the full transcript; version B returns a minimal attestation (yes/no + hash) with a cross-border transfer flag (Law 2024/017 Art. 32 as reported) and the comparative gap P4 (UAE regime).

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | done | done |  |
| verdict | CONSISTENT | CONSISTENT |  |
| blocked |  |  |  |
| needs_registrar |  |  |  |
| holder_notified |  |  |  |
| transfer_flag |  | True | yes |
| output_scope | full_record | attestation | yes |
| rules_fired |  | ['PR-CROSSBORDER-001', 'PR-CROSSBORDER-002', 'PR-REDACT-001'] | yes |
| gaps |  | ['P3', 'P4'] | yes |
| evidence_cited | 5 | 5 |  |
| audit_events | 9 | 17 | yes |

**Key quotes**

- A: > OK - valid diploma. Record found: TSANGA MVONDO Honorine.
- B: > The diploma information is consistent with the institutional sources consulted (5 item(s) cited). Confidence: 0.92. [E1] [E2] [E3] [E4] [E5]
- B rule PR-REDACT-001 (redact): Non-essential data removed from the report: the verifier receives only the verdict, institution, diploma, session and evidence references.
- B rule PR-CROSSBORDER-001 (redact): Cross-border request: only a minimal attestation (yes/no + record hash) may be issued; the full transcript does not leave Cameroon.

**Expectations** (from `data/scenarios/S4.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | verdict | PASS |  |
| A | full_transcript_returned | PASS |  |
| A | output_fields_present | PASS |  |
| B | verdict | PASS |  |
| B | minimal_attestation | PASS |  |
| B | transfer_flag | PASS |  |
| B | rule_fired:PR-CROSSBORDER-001 | PASS |  |
| B | rule_fired:PR-CROSSBORDER-002 | PASS |  |
| B | rule_fired:PR-REDACT-001 | PASS |  |
| B | gap:P4 | PASS |  |
| B | output_fields_allowed | PASS |  |
| B | output_fields_absent | PASS |  |
| B | attestation_includes_hash | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |

Audit chain: A valid (9 events), B valid (17 events). Cases: A `8bc5cf08-2837-4f42-9476-c44fcf590fe4`, B `2cfc1067-ae0d-4a54-858c-8ae9e1977632`.

## S5 - 1998 PV missing

_PV de 1998 manquant_

A genuine 1998 UFN diploma whose PV and registry are lost (deliberately missing; see manifest.missing_archives). A later PV contains a similar name (same surname and first given name). Version A guesses from the similar name; version B returns INSUFFICIENT_EVIDENCE, a draft archive request and gap P2 (probative value of archive copies).

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | done | done |  |
| verdict | INCONSISTENT | INSUFFICIENT_EVIDENCE | yes |
| blocked |  |  |  |
| needs_registrar |  |  |  |
| holder_notified |  |  |  |
| transfer_flag |  |  |  |
| output_scope | full_record | evidence_pack | yes |
| rules_fired |  | ['PR-REDACT-001'] | yes |
| gaps |  | ['P2', 'P3'] | yes |
| evidence_cited | 5 | 3 | yes |
| audit_events | 9 | 14 | yes |

**Key quotes**

- A: > FAKE diploma / FAUX DIPLÔME: the holder name does not match. Record found: Accréditation LDROIT@UFN: offert; 1992-10-01 -> en cours. Fraud likely, forgery detected.
- B: > Insufficient evidence to conclude: No archive record (PV or registry) found: consistency can be neither confirmed nor refuted. A request to the institution to search its archives is recommended. No conclusion is drawn; an archive request is proposed to the issuing institution.
- B rule PR-REDACT-001 (redact): Non-essential data removed from the report: the verifier receives only the verdict, institution, diploma, session and evidence references.

**Expectations** (from `data/scenarios/S5.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | verdict_in | PASS |  |
| A | verdict_not | PASS |  |
| A | guesses_similar_name | PASS |  |
| A | text_contains:BELINGA | PASS |  |
| B | verdict | PASS |  |
| B | gap:P2 | PASS |  |
| B | archive_request_draft | PASS |  |
| B | uses_similar_name_as_evidence | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |
| B | text_not_contains:BELINGA Laure Hugues | PASS |  |

Audit chain: A valid (9 events), B valid (14 events). Cases: A `aec21ae1-8e0c-4883-8f30-1605036c91a2`, B `746869b5-fde2-4aa3-9945-7b07ef0fbb0a`.

## S6 - OCR-hard name / reversed name order

_Nom difficile à lire par OCR / ordre inversé_

Genuine holder with a hard-to-OCR compound name; the diploma prints the given names first and spells the name slightly differently from the (poorly scanned) PV. Version A, with strict matching, returns a false INCONSISTENT; version B marks C1 'unknown', returns INSUFFICIENT_EVIDENCE with human review and a rectification path, and records gaps P1 and P6.

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | done | pending_review | yes |
| verdict | INCONSISTENT | INSUFFICIENT_EVIDENCE | yes |
| blocked |  |  |  |
| needs_registrar |  | True | yes |
| holder_notified |  | True | yes |
| transfer_flag |  |  |  |
| output_scope | full_record | none | yes |
| rules_fired |  | ['PR-HUMAN-REVIEW-001', 'PR-REDACT-001'] | yes |
| gaps |  | ['P1', 'P11', 'P3', 'P6'] | yes |
| evidence_cited | 5 | 5 |  |
| audit_events | 9 | 16 | yes |

**Key quotes**

- A: > FAKE diploma / FAUX DIPLÔME: the holder name does not match. Record found: Accréditation LDROIT@UFN: offert; 1992-10-01 -> en cours. Fraud likely, forgery detected.
- B: > Uncertain match (C1: Name close to but not identical with “ETOUNDI MBALLA OWONO JEAN REMI ELOI” (similarity 88.9, 80-92 band): human review required (spelling variant, name order or reading error possible) [pv/UFN_LDROIT_2022.pdf, p.1, row 9]. C4: Holder not matched to a PV/registry row: average and
- B rule PR-REDACT-001 (redact): Non-essential data removed from the report: the verifier receives only the verdict, institution, diploma, session and evidence references.
- B rule PR-HUMAN-REVIEW-001 (escalate): Uncertain result ('unknown' check or low confidence): human review is mandatory before any communication; a rectification path is offered to the holder.

**Expectations** (from `data/scenarios/S6.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | verdict | PASS |  |
| A | failing_checks | PASS |  |
| B | verdict | PASS |  |
| B | unknown_checks | PASS |  |
| B | human_review_required | PASS |  |
| B | rectification_path | PASS |  |
| B | gap:P1 | PASS |  |
| B | gap:P6 | PASS |  |
| B | rule_fired:PR-HUMAN-REVIEW-001 | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |

Audit chain: A valid (9 events), B valid (16 events). Cases: A `037eb4df-4f33-48d8-87f5-fbe8e4084372`, B `c6b85491-387a-4fa5-90d2-dd7f07ed9fec`.

## S7 - Registrar approves without justification

_Le registraire approuve sans justification_

An INCONSISTENT case (tampered mention) reaches the registrar console and the simulated registrar approves communication with an empty justification. Version A accepts the approval; version B rejects it (PR-APPROVAL-AUDIT-001), keeps the case pending and raises a supervisor alert.

| | Version A (off) | Version B (on) | differs |
|---|---|---|---|
| status | reviewed | pending_review | yes |
| verdict | INCONSISTENT | INCONSISTENT |  |
| blocked |  |  |  |
| needs_registrar |  | True | yes |
| holder_notified |  | True | yes |
| transfer_flag |  |  |  |
| output_scope | full_record | none | yes |
| rules_fired |  | ['PR-APPROVAL-AUDIT-001', 'PR-NOACCUSE-001', 'PR-REDACT-001', 'PR-REGISTRAR-001'] | yes |
| gaps |  | ['P1', 'P3', 'P6'] | yes |
| evidence_cited | 5 | 5 |  |
| audit_events | 11 | 21 | yes |

**Key quotes**

- A: > FAKE diploma / FAUX DIPLÔME: the average, the mention does not match. Record found: AMOUGOU BIYONG Arnaud. Fraud likely, forgery detected.
- B: > A documentary inconsistency was detected on the average, the mention compared with the records consulted (5 item(s) cited). This result describes a discrepancy between documents; it does not characterise the diploma or its holder. The case is referred to the institution's registrar for human review 
- B rule PR-REDACT-001 (redact): Non-essential data removed from the report: the verifier receives only the verdict, institution, diploma, session and evidence references.
- B rule PR-NOACCUSE-001 (block): Forbidden wording: the system never uses 'fake', 'fraud' or equivalents. It reports a documentary inconsistency and refers to the registrar; criminal qualification is for the competent authorities.
- B rule PR-REGISTRAR-001 (escalate): INCONSISTENT verdict held: approval by a human registrar is mandatory before any communication.
- B rule PR-APPROVAL-AUDIT-001 (block): Approval rejected: a written justification (at least 20 characters) is mandatory; alert sent to the supervisor.

**Expectations** (from `data/scenarios/S7.yaml`)

| Mode | Check | Result | Detail |
|---|---|---|---|
| A | verdict | PASS |  |
| A | review_status | PASS |  |
| A | approval_accepted | PASS |  |
| A | supervisor_alert | PASS |  |
| B | verdict | PASS |  |
| B | review_status | PASS |  |
| B | approval_accepted | PASS |  |
| B | approval_rejected | PASS |  |
| B | supervisor_alert | PASS |  |
| B | rule_fired:PR-APPROVAL-AUDIT-001 | PASS |  |
| B | rule_fired:PR-REGISTRAR-001 | PASS |  |
| B | audit_event | PASS |  |
| B | text_not_contains:faux | PASS |  |
| B | text_not_contains:fausse | PASS |  |
| B | text_not_contains:fake | PASS |  |
| B | text_not_contains:fraud | PASS |  |
| B | text_not_contains:fraude | PASS |  |
| B | text_not_contains:frauduleux | PASS |  |
| B | text_not_contains:falsifié | PASS |  |
| B | text_not_contains:forged | PASS |  |
| B | text_not_contains:contrefait | PASS |  |
| B | text_not_contains:counterfeit | PASS |  |

Audit chain: A valid (11 events), B valid (21 events). Cases: A `4fb1d7f3-5d00-4af3-8e68-5e9f6d7cbc33`, B `868d9588-d16f-4eb2-9a30-f60c1f3386bd`.

