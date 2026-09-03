# Den Mail tour

Every picture here comes from the fake account the test suite uses
(`tests/fake_server.py`), captured headlessly at 1280×720 in the dark theme.
Real accounts look the same, with your own mail in it.

![Walkthrough: inbox, group by sender, fold, select, search, compose](../data/screenshots/tour.gif)

## Reading mail

Three panes: folders and labels, the conversation list, the open conversation.
Labels show as chips on each row and in the conversation header; removing the
Inbox chip archives. Newsletters get an **Unsubscribe** button, and remote
images stay blocked until you allow them for that message or trust the sender.

![The inbox with a newsletter open](../data/screenshots/inbox-dark.png)

The light theme, same view. Dark mode adapts light-coloured HTML mail; the sun
button in the message header switches a message back to its original colours.

![The inbox in the light theme](../data/screenshots/inbox-light.png)

## Grouping and bulk actions

**Group by sender** (or by organisation, which merges `lippu.vr.fi` and
`tili.vr.fi`) works on top of any sort order. Each sender is a row: click it
to select the whole group, use the arrow to fold it, and the button in the
header folds or unfolds everything.

![The list grouped by sender, two groups folded](../data/screenshots/group-sender.png)

The **Select** button turns on checkboxes and a bar with the bulk actions.
Outside that mode, Ctrl-click and Shift-click extend the selection as usual.

![Selection mode with three conversations checked](../data/screenshots/selection.png)

Labels and folders are one click away from the toolbar, the right-click menu,
or the `l` and `v` keys. Everything can be undone from the toast.

| Labels popover | Context menu |
| --- | --- |
| ![Labels popover](../data/screenshots/labels.png) | ![Right-click menu on a conversation](../data/screenshots/context-menu.png) |

## Search

The search box understands `from:`, `to:`, `subject:`, `is:unread`,
`is:flagged`, `has:attachment`, `before:` and `after:`, scoped to the current
folder or to all mail. With the scope set to all mail and no query, the list
shows the whole account, which is where grouping by sender is most useful.

![Search for has:attachment](../data/screenshots/search.png)

## Writing mail

The From field lists your starred identities first; "Show all identities…"
expands to every alias and wildcard address in the account.

![New message with the From list open](../data/screenshots/compose.png)

## Identities and Masked Email

The identities dialog is where you star the aliases you actually send from.
Masked Email addresses can be created, described, disabled and deleted.

| Identities & aliases | Masked Email |
| --- | --- |
| ![Identities dialog](../data/screenshots/identities.png) | ![Masked Email dialog](../data/screenshots/masked.png) |

## Preferences

![Preferences](../data/screenshots/preferences.png)

## Making these

`docs/DEVELOPMENT.md` explains the autopilot and the headless cage session
used to produce these captures without touching the desktop.
