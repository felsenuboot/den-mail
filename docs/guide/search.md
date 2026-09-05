# Search

## Search

The search box is scoped to the current folder or to all mail. With the
scope set to all mail and no query, the list shows the whole account, which
is where grouping by sender is most useful. Bare words search everything;
`"a quoted phrase"` must appear as written, and quotes work inside an
operator too (`from:"Anna Berger"`).

| Operator | Meaning |
| --- | --- |
| `from:` `to:` `cc:` `subject:` | match one header |
| `is:unread` `is:read` `is:flagged` `is:unflagged` | by flag (`is:starred` works too) |
| `has:attachment` | with an attachment |
| `label:Receipts` `in:Work/Projects` | in that mailbox, whatever the scope says: a name, a path, or `inbox`, `sent`, `archive`, `spam`, `trash`; case, hyphens and underscores don't matter |
| `in:trash` `in:spam` `in:anywhere` | Trash and Spam are otherwise left out of an all-mail search |
| `before:2026-09-04` `after:2026-09` `after:2025` | a date; a month or a year means its first day |
| `older_than:7d` `newer_than:2w` | a span in `h`ours, `d`ays, `w`eeks, `m`onths or `y`ears ago (`before:7d` means the same) |

Several operators combine with AND. A label that names no mailbox gives an
empty list that says so; a date that does not parse is searched as text.

![Search for has:attachment](../../data/screenshots/search.png)

---
[Guide index](README.md) · [Tour](../TOUR.md)
