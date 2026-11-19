# Calitrans bug and fix tracking workflow

Use GitHub Issues plus one GitHub Project. Keep the process small enough for the Calitrans team while retaining traceability from a failed test to a verified fix.

## Labels and project fields

Create the requested priority labels `priority:P0` through `priority:P4`; type labels `type:bug`, `type:security`, `type:data-integrity`, `type:reliability`, `type:test-gap`, `type:technical-debt`, `type:documentation`, `type:enhancement`; subsystem labels `area:auth`, `area:loads`, `area:dispatch`, `area:inbox`, `area:documents`, `area:sms`, `area:workers`, `area:realtime`, `area:web`, `area:streamlit`, `area:database`, `area:integrations`, `area:operations`; status labels `status:needs-triage`, `status:confirmed`, `status:ready`, `status:in-progress`, `status:blocked`, `status:ready-for-retest`, `status:verified`; and environment labels `env:development`, `env:staging`, `env:production`.

Project fields: Status, Priority, Type, Subsystem, Environment, Severity, Owner, Target release, Found in commit, Fixed in commit, Regression test, Verification status.

Flow: `Reported → Needs Triage → Confirmed → Ready → In Progress → In Review → Ready for Retest → Verified → Closed`.

| State | Entry criteria | Exit criteria |
|---|---|---|
| Reported | Repro information exists | Triage owner assigned |
| Needs Triage | Commit/environment/test ID known | Reproduced or explicitly unable to reproduce |
| Confirmed | Evidence and severity agreed | Root cause and acceptance criteria written |
| Ready | Dependencies resolved; safe test data identified | Engineer begins scoped work |
| In Progress | Linked branch/commit exists | Regression test and fix ready |
| In Review | Diff, security/data impact, migration notes, test evidence attached | Review approved |
| Ready for Retest | Fixed commit deployed to named environment | Original test plus regression test executed |
| Verified | Expected UI/API/DB/event behavior proven | Documentation complete; no dependent blocker |
| Closed | Verification evidence linked | Reopen only with new evidence/regression |

## Severity

- P0: active incident, production data loss, or system-wide outage.
- P1: critical workflow, authorization, unsafe database behavior, duplicate financial/operational side effect, or unrecoverable corruption.
- P2: important incorrect/unreliable behavior with a workaround.
- P3: limited defect, usability/validation weakness, or maintainability risk.
- P4: cosmetic or low-priority cleanup.

## Fix requirements

Every fix should include a linked issue, root cause, automated regression test where practical, exact test evidence, migration/rollback notes, security/authorization assessment, documentation update, manual retest instructions, and no unrelated refactor unless justified. Do not close solely because code merged.

## Daily testing workflow

1. Load/reset a named QA dataset.
2. Record the exact commit and sanitized database identity.
3. Select stable IDs from `FUNCTIONAL_TEST_MATRIX.md`.
4. Test with the designated role.
5. Record Pass, Fail, Blocked, or Not Run.
6. Attach screenshots and sanitized request/correlation IDs.
7. Create one issue per independently fixable root cause.
8. Link issue, test ID, sample record, and commit.
9. Retest on the exact fixed commit.
10. Close only after automated regression and manual verification pass.

## Bug report template

```markdown
## Summary

## Environment
- Environment / sanitized database identity:
- Build/commit:
- User role:
- Test ID and QA dataset/record IDs:

## Preconditions

## Steps to reproduce
1.

## Expected result

## Actual result

## Frequency

## Proposed severity

## Evidence
- Screenshots/logs (redacted):
- Sanitized request/correlation ID:
- Database evidence (no customer/secret data):

## Workaround

## Root cause (owner fills after confirmation)

## Regression-test requirement

## Acceptance criteria
```

## Short Codex intake format

```text
Test ID:
Screen/page:
User role:
What I did:
What I expected:
What happened:
Error text (redacted):
Screenshot attached:
Approximate time/time zone:
Sample dataset + record ID:
Commit/environment:
```

Ask Codex to reproduce read-only first, state whether you authorize implementation, and never paste credentials, real phone numbers, customer emails, document contents, or full database URLs.
