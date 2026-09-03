# Den Mail tour

<img src="../data/den-calligraphy.png" width="140" alt="伝" align="left" hspace="24">

> **伝** 〔でん · *den*〕 noun
> 1. legend; tradition
> 2. biography; life
> 3. method; way
> 4. horseback transportation and communication relay system used in ancient Japan
>
> <sub>[jisho.org](https://jisho.org/word/%E4%BC%9D-1)</sub>

<br clear="all">

## Reading mail

Three panes: folders and labels, the conversation list, the open conversation.
Labels show as chips on each row and in the conversation header; removing the
Inbox chip archives. Newsletters get an **Unsubscribe** button, and remote
images stay blocked until you allow them for that message or trust the sender.

![The inbox with a newsletter open](../data/screenshots/inbox-dark.png)

The same view in both themes. Dark mode adapts light-coloured HTML mail; the
sun button in the message header switches a message back to its original
colours.

![The inbox, split diagonally between the light and the dark theme](../data/screenshots/theme-split.png)

## Grouping and bulk actions

**Group by sender** (or by organisation, which merges `lippu.vr.fi` and
`tili.vr.fi`) works on top of any sort order. Each sender is a row: click it
to select the whole group, use the arrow to fold it, and the button in the
header folds or unfolds everything.

![The list grouped by sender, two groups folded](../data/screenshots/group-sender.png)

Cleaning out one sender in four steps: group, fold everything, tick the
sender, archive. Undo is in the toast.

![Group by sender, fold all, select a sender, archive](../data/screenshots/group-archive.gif)

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
