# Meeting to Ergo CRM

## What I focused on

I focused on safely reconciling a meeting to an existing CRM Deal. The assignment calls out that a
wrong write is worse than a missed one, so I kept the automatic write surface deliberately small:
deterministic code finds plausible CRM records, Luna makes an evidence-backed selection, and a
policy layer verifies that selection before anything is written. Ambiguous meetings, net-new
opportunities, and closed Deals go to review. Approved meetings can update supported Deal custom
fields and append one replay-safe note.

That slice let me spend the time on the parts I think matter most here: record resolution,
idempotency, partial failure, and making unsafe decisions easy to inspect.

## Running it

Python 3.10+ is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'

export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5.6-luna'
export ERGO_CRM_URL='http://127.0.0.1:8787'
```

The app reads configuration from the environment; it does not load `.env` files automatically.
All available settings are listed in [`.env.example`](.env.example).

Start the supplied CRM in one terminal:

```bash
python3 -m ergo_crm serve
```

Then plan and apply a meeting in another terminal:

```bash
meeting-to-crm plan fixtures/m01.json
meeting-to-crm apply fixtures/m01.json
```

`plan` may call Luna and saves the approved plan, but it never writes to the CRM. `apply` reuses
that saved plan. Pass several fixture paths to process them in order, or add `--json` for structured
output.

The journal defaults to `.meeting_to_crm/state.sqlite3`. For a clean demo:

```bash
export MEETING_CRM_STATE_PATH="/tmp/meeting-to-crm-$(date +%s).sqlite3"
```

## How it works

1. **Resolve candidates.** Corporate email addresses use exact Contact and Company lookups, then
   follow CRM associations to Deals. Personal addresses fall back to full-name search without
   silently choosing the first result.
2. **Ask Luna to decide.** Luna receives only the meeting and the candidate set. Its structured
   response must select supplied IDs and cite exact meeting evidence. Meeting text is treated as
   untrusted input.
3. **Apply policy in code.** The model cannot write. Deterministic checks enforce candidate
   membership, Company/Contact/Deal associations, an open Deal, high certainty, source-correct
   evidence, the live CRM field schema, and field-specific rules.
4. **Execute from a journal.** SQLite stores the payload hash, plan, and each operation before CRM
   writes begin. This makes retries and partial runs inspectable and resumable.

I do not automatically create Companies, Contacts, or Deals. Safe creation needs a durable external
ID or CRM idempotency primitive that this API does not provide. I also leave standard Deal fields
such as pipeline, stage, amount, and status alone.

## Write safety

An approved meeting produces at most two operations:

- update changed, schema-supported Deal custom fields;
- add a deterministic note containing `[meeting-to-crm:<meeting-id>]`.

Before a real write, the executor rereads the Deal. It refuses to continue if the Deal was closed,
moved to another pipeline, or if a field in the plan's before-snapshot changed. This check also
applies to note-only plans. The supplied CRM has no compare-and-swap operation, so this is a
best-effort optimistic check rather than a claim of transactional isolation.

Retries are safe in the failure cases covered here:

- The same meeting ID and payload reuses its journaled result.
- A completed ID with changed content is treated as a duplicate and never written again (`m14`).
- An already-applied field update is recognized from current CRM state.
- An existing note marker prevents a duplicate note.
- A partial run resumes only pending operations.
- Transient CRM failures retry up to three times; later operations stop if retries are exhausted.

JSON events go to stderr and include meeting, candidate, model, operation, retry, and outcome
metadata. Human-readable results go to stdout. API keys and full transcripts are not logged.

## Fixture scope

| Result | Fixtures |
| --- | --- |
| Update an existing open Deal | `m01`, `m06`, `m07`, `m08`, `m11`, `m12`, `m15` |
| Safely skip | `m04`, `m05` |
| Deduplicate `m01` | `m14` |
| Require review, with no writes | `m02`, `m03`, `m09`, `m10`, `m13` |

These outcomes come from the resolution and policy rules, not fixture-specific IDs in application
code. In particular, the implementation keeps both Apex contexts, both Cobalt Deals, and both
Morgan Blake Contacts available until the decision step.

## Tests

The normal suite is offline. It injects decisions through a `DecisionEngine` interface and exercises
the real supplied HTTP CRM on ephemeral ports.

```bash
pytest -q
ruff check meeting_to_crm tests
ruff format --check meeting_to_crm tests
```

Coverage includes ambiguous resolution, protected and closed Deals, cross-company selection,
pipeline and field changes between plan/apply, duplicate delivery, retries, partial recovery, and
the gap between a CRM write and its journal update.

There are two opt-in model checks. The first makes one Luna request and checks the structured-output
contract:

```bash
pytest -m live -q
```

The second sends the full fixture set through Luna and can make up to 13 paid requests:

```bash
RUN_LIVE_FIXTURE_MATRIX=1 pytest -m live_matrix -q
```

Keeping those tests opt-in separates deterministic software correctness from model behavior and
avoids spending the interview API budget during normal development.

## What I would do next

For production I would add a real review queue, database-backed worker leases, tenant/environment
identity in the journal, and CRM-supported idempotency keys for record creation. I would also build a
labeled resolution set to measure false positives and false negatives by prompt version. Those are
more valuable next steps than widening the automatic write surface before its safety can be measured.
