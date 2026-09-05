# Writing mail

## Writing mail

The From field lists your starred identities first; "Show all identities…"
expands to every alias and wildcard address in the account.

![New message with the From list open](../../data/screenshots/compose.png)

## Send later

The clock next to Send offers this afternoon, this evening, tomorrow morning
or afternoon, Monday morning, or any time from a calendar. The server holds
the message until then (Fastmail allows up to a year ahead) and it waits in
the Scheduled folder, where opening it shows when it goes and a *Cancel
send* button that puts it back in Drafts. Snoozing is not offered: Fastmail
does not expose it to third-party clients over JMAP.

## Offline

Without a connection the cache still shows every folder and message it has
listed. Archiving, labelling, deleting and sending keep working: the change
is applied locally, queued in the cache, and sent with the first sync after
the connection returns; the sidebar says how many changes are waiting. A
queued change the server then rejects is reported in a toast and dropped.
A draft saved offline is kept in the cache and listed in Drafts; the next
sync creates it on the server, and a compose window still open carries on
against the server's copy.

## Address book

With a token that has the Contacts scope, the app keeps your Fastmail
address book in its cache: recipient completion offers your contacts first
(by name or address), then the addresses seen in cached mail, and a contact
with a photo shows it in the list and the conversation instead of the
sender domain's logo. Without the scope nothing changes; the Account page in
Preferences says which it is.

## Identities and Masked Email

The identities dialog is where you star the aliases you actually send from.
Masked Email addresses can be created, described, switched off and deleted;
clicking one copies it.

| Identities & aliases | Masked Email |
| --- | --- |
| ![Identities dialog](../../data/screenshots/identities.png) | ![Masked Email dialog](../../data/screenshots/masked.png) |

---
[Guide index](README.md) · [Tour](../TOUR.md)
