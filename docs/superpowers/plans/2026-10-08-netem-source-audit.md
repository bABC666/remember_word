# NETEM source audit and usable dictionary display

> Execute the authorized bounded design in this chat. Use test-driven implementation and independent source-audit batches; preserve production until the new plan is previewed and isolated checks pass.

**Goal:** Audit retained NETEM dictionary fragments so reliable meanings remain usable, POS/IPA keep their own evidence, and unresolved source defects do not hide all dictionary content.

**Architecture:** Keep the existing extraction table and core-meaning model. Produce fingerprint-bound decisions for every retained candidate, independently accepting meaning and source POS or quarantining defects. Source-level meanings shared across English senses remain explicitly word-level translations, never fabricated separate senses. No POS inferred from common knowledge. Preserve originals, previous decisions and licences. Read production only for this stage.

**Spec:** User approved automatic source comparison, usable meanings without POS, explicit unresolved-source gaps, and keeping review detail out of normal learning content.

- [x] Inventory all current payloads and source fingerprints; separate semantic quality, mapping ambiguity, POS defects, licence/template noise and missing originals.
- [x] Independently audit disjoint word batches against complete retained English glosses/Chinese fragments; bind every decision to file/body/locator/text. All decisions remain automated, not human/core confirmation.
- [x] Add fail-first tests for independently accepted meaning/unknown POS, erroneous fragment rejection, shared word translations, stale/duplicate/missing decision guards and pronunciation metadata.
- [x] Implement audit-plan builder and API display contract without changing entry/user/core fields. Produce a per-word preview with retained/rejected/unresolved reasons.
- [x] Add frontend regressions for useful ungrouped meanings, safe source fallback, absent confirmed-core records, collapsed evidence and sensible IPA/POS headings.
- [x] Validate about 100 diverse words in a freshly cloned isolated database, then full plan; preserve all original business rows, test idempotence and actual-batch rollback.
- [x] Run related API/parser/update regressions, complete frontend tests/build, actual desktop/mobile QA against an isolated API and record representative original comparisons.
- [x] Deliver final coverage, source gaps, reviewed examples and exact new update/rollback plan. Do not reuse the previous release plan's publication approval for a changed plan.

Results: `docs/NETEM-SOURCE-AUDIT-VALIDATION-2026-10-08.md`; full candidate SHA `6ff4887afeaaa22d293937c4ae44373c4981e637c0e09ad131a606d77cb3a83b`. Production remains unchanged pending the user's explicit final confirmation of this new candidate.

Review artifacts and temporary databases belong under `test-artifacts/netem-source-audit-20261008/`; the four existing history drafts remain untouched.
