# Meeting webhook payload

Each file in this directory is one normalized meeting. Treat them as already-parsed notetaker webhooks — this assignment does not include raw Zoom/Meet/Teams payloads.

## Shape

```json
{
  "id": "string",
  "name": "string",
  "occurred_at": "ISO-8601",
  "duration_seconds": 0,
  "attendees": [{"name": "string", "email": "string"}],
  "notes": "string",
  "action_items": [{"text": "string", "owner_email": "string"}],
  "transcript": [{"speaker": "string", "text": "string", "timestamp": 0}]
}
```

| Field | Notes |
| --- | --- |
| `id` | Provider meeting id. Stable for a given meeting; a retry from the provider reuses the same id. |
| `name` | Calendar title. |
| `occurred_at` | ISO-8601 datetime with offset. |
| `duration_seconds` | Non-negative integer. |
| `attendees` | Calendar invitees (`name`, `email`). Helios sellers appear as attendees on customer calls. |
| `notes` | Notetaker summary. |
| `action_items` | `text` plus `owner_email` (usually an attendee). |
| `transcript` | Turns with `speaker`, `text`, and `timestamp` (seconds from meeting start). |

There are no other fields on the payload.
