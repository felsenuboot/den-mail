# Den Mail tour

<table>
<tr>
<td width="160" valign="top"><img src="../den_mail/den-calligraphy.png" width="140" alt="伝"></td>
<td valign="top">

**伝** 〔でん · *den*〕 noun
1. legend; tradition
2. biography; life
3. method; way
4. horseback transportation and communication relay system used in ancient Japan

**伝** 〔つて · *tsute*〕 noun
1. means of making contact; intermediary; go-between
2. connections; influence; pull; good offices

<sub>usually written using kana alone; also 伝手, ツテ · [jisho.org](https://jisho.org/search/%E4%BC%9D)</sub>

</td>
</tr>
</table>

## The first ten minutes

1. **Sign in** with a Fastmail API token (Mail read/write; add the Contacts
   scope for your address book). The Inbox lists at once and keeps syncing
   over push.
2. **Read and act.** `j`/`k` move, `e` archives, `#` deletes, `r` replies;
   labels and folders are one key away (`l`, `v`). Everything undoes from the
   toast.
3. **Let the categories work.** Every message is sorted locally into
   Transactions, Security, Updates, Newsletters and so on; the *Views* in the
   sidebar answer "what is a newsletter here" without a search.
4. **Clean up.** *Clean up…* in the main menu ranks the senders you never
   read; tick a few and archive, delete or unsubscribe in one go. *Always for
   this sender…* in a conversation's context menu turns a decision into a
   rule.
5. **Make it yours** in Preferences: the screener for strangers, a lock, an
   assistant for summaries, logos, keeping the app running in the background.

The empty conversation pane shows a tip and quick links to these tools on
every start.

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
`tili.vr.fi`) works on top of any sort order. Each sender is a card: its
header row selects the whole group, the arrow at its end folds it, and the
button in the header bar folds or unfolds everything.

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

![Search for has:attachment](../data/screenshots/search.png)

## Categories

Every message is sorted locally, with no server round trip and nothing sent
anywhere, into Primary, Transactions, Security, Updates, Newsletters, Lists or
Promotions. The rules read the list headers (`List-Post`, `List-Unsubscribe`,
`Precedence`, `Feedback-ID`), automated senders (`Auto-Submitted`, `noreply@`),
English and German wording for codes, sign-ins, receipts, orders and shipping,
and whether you have ever written to the sender. Rows show the category as a
chip (Primary shows none), and the funnel in the list header narrows the list
to one category; the list keeps loading pages until enough of that category is
on screen.

A message that carries schema.org data, as shops, carriers, airlines and
booking sites add to theirs, is Transactions for sure and shows a line above
its body summing it up: "Parcel in transit · DHL · expected 6 Sep 18:00 ·
00340…", "Flight LH 123 · FRA → HEL · 12 Oct 09:40", with a button that
copies the tracking or reservation number. Only bodies already in the cache
are read; nothing is fetched for it.

When the rules get one wrong, right-click the conversation and choose
*Categorise as…*: your word is kept over the rules, and after a few such
corrections the app learns from them. The learned layer only speaks where
the rules were unsure (a message sorted into Primary for lack of signals,
say) and only when it is confident, and it sees what you do with a sender's
mail (never opened, deleted unread, written back), so a "friendly" sender
you never read stops counting as a newsletter. The message details, behind
the header, say why a message got its category: the rule that fired, your
choice, or what the model learned.

![Category filter menu](../data/screenshots/categories.png)

## Views

The *Views* section of the sidebar holds lists that no folder on the server
answers: Newsletters, Transactions, Security and Updates from the categories,
*Never read* for senders with two or more cached messages you have never
opened, the oldest at least two months old, and *Big attachments* for mail
with an attachment of 5 MB or more. They are SQL queries over the local cache,
so they open at once and cost no request; the badge counts what the cache
knows, which is every conversation listed since the app was installed, not
the whole account. A view behaves like a mailbox in the list: the same sort
menu, unread filter, multi-select, drag and drop, context menu and actions,
and a search typed while a view is shown narrows the view itself with the
operators above. Archiving from a view keeps the conversation in it (it is
still a newsletter); deleting or reporting spam removes it. Trash and Spam
never appear. *Views in the sidebar* in Preferences turns the section off.

![The Newsletters view](../data/screenshots/views.png)

## Newsletters

*Newsletters…* in the main menu scans the account for mail that carries a
List-Unsubscribe header and lists one row per sender: how many messages, how
many unread, when the last one came, and how the sender lets you leave
(one-click request, an unsubscribe mail, or a web page). Tick the senders
you are done with, choose whether to keep, archive or delete their mail,
and press Unsubscribe. Requests go out one after another; a one-click
request that fails falls back to the sender's other methods, and a sender
you have already left shows the date.

![Newsletters dialog](../data/screenshots/newsletters.png)

## Screener

*Screen first-time senders* in Preferences (off by default) holds mail from
anyone the app has never seen, as a sender, a recipient or someone you
wrote to, in a *Screener* view instead of the Inbox, without a
notification. Open a conversation there and choose *Let through* (their
mail reaches the Inbox, this and every later one) or *Screen out* (their
mail is archived now, and a rule archives it from then on). The same two
choices sit in the conversation's context menu. The Inbox subtitle counts
what is being held. Only mail that arrives while the app runs is screened;
the backlog is never touched.

![The screener's question](../data/screenshots/screener.png)

## Clean up

*Clean up…* in the main menu lists every sender in the local cache, the
most pointless first: how many messages, how many unread, when the last one
came, whether you ever wrote back, how much of their mail you threw away
unread, and whether they offer an unsubscribe method. The score behind the
order rewards volume that is never opened or deleted unread and drops below
zero for anyone you have replied to. Narrow the list to a category, sort by
volume, unread, date or size, and open a sender to see their newest
messages. Tick senders and press Mark read, Archive, Delete or Unsubscribe;
the first three reach every message from them on the server, not only the
cached ones, and can be undone from the toast. *Always…* adds a rule per
selected sender for their future mail and applies it now.

![Clean up dialog](../data/screenshots/cleanup.png)

## Rules

*Always for this sender…* in a conversation's context menu makes a rule: for
this address or for everyone at its domain, label the mail, archive it, mark
it as read or delete it, and optionally do that to their existing mail right
away. *Rules…* in the main menu lists the rules with how often each fired,
removes them, and adds rules on a list id or a whole category. Rules run in
the app when new mail lands in the Inbox, so they only work while Den Mail is
open; for rules that run on the server whether the app is open or not, the
dialog links to Fastmail's own rules settings.

![Always for this sender](../data/screenshots/rules.png)

## Offline

Without a connection the cache still shows every folder and message it has
listed. Archiving, labelling, deleting and sending keep working: the change
is applied locally, queued in the cache, and sent with the first sync after
the connection returns; the sidebar says how many changes are waiting. A
queued change the server then rejects is reported in a toast and dropped.
A draft saved offline is kept in the cache and listed in Drafts; the next
sync creates it on the server, and a compose window still open carries on
against the server's copy.

## Send later

The clock next to Send offers this afternoon, this evening, tomorrow morning
or afternoon, Monday morning, or any time from a calendar. The server holds
the message until then (Fastmail allows up to a year ahead) and it waits in
the Scheduled folder, where opening it shows when it goes and a *Cancel
send* button that puts it back in Drafts. Snoozing is not offered: Fastmail
does not expose it to third-party clients over JMAP.
## Address book

With a token that has the Contacts scope, the app keeps your Fastmail
address book in its cache: recipient completion offers your contacts first
(by name or address), then the addresses seen in cached mail, and a contact
with a photo shows it in the list and the conversation instead of the
sender domain's logo. Without the scope nothing changes; the Account page in
Preferences says which it is.

## Writing mail

The From field lists your starred identities first; "Show all identities…"
expands to every alias and wildcard address in the account.

![New message with the From list open](../data/screenshots/compose.png)

## Identities and Masked Email

The identities dialog is where you star the aliases you actually send from.
Masked Email addresses can be created, described, switched off and deleted;
clicking one copies it.

| Identities & aliases | Masked Email |
| --- | --- |
| ![Identities dialog](../data/screenshots/identities.png) | ![Masked Email dialog](../data/screenshots/masked.png) |

## Assistant and summaries

*Assistant* in Preferences (off by default) points the app at a language
model: Ollama on this machine, any server that speaks the OpenAI API (LM
Studio, llama.cpp, vLLM, OpenAI, Mistral, Groq, OpenRouter …) or Anthropic.
The server, the model, a key kept in the keyring, a requests-per-day limit
and a *Test* button that reaches the server without spending a request; a
row says whether the mail text stays on this machine or leaves it for the
chosen server. Thinking models are asked to skip the thinking on local
servers, so they answer instead of reasoning in silence.

![The Assistant page of Preferences](../data/screenshots/assistant.png)

With the assistant on, the sparkle in a conversation's header (or
Ctrl+Shift+S) sums the thread up in a few lines above the messages, quoted
history left out and older messages cut first so a long thread still fits a
small model. The answer is cached per thread: a second look is free, a new
reply asks again. In Clean up, an expanded sender row starts with a one-line
description of their newest message, so you can decide without opening
anything.

![A conversation summarised above the messages](../data/screenshots/summary.png)

## Label suggestions

The app learns from the mail you have labelled. Once a label has enough
examples and the model is sure about a conversation that lacks it, a dashed
chip such as *Work?* appears next to the labels; one click applies it. The
tooltip says how sure the model is and which words spoke for it. Folders are
never suggested; *Suggest labels* under *Inbox* in Preferences turns the chips
off.

## Two conversations side by side

On a wide window (an ultrawide monitor, 2200 pixels or more) *Open beside*
in a conversation's context menu, or `b`, pins the conversation in a second
column next to the reading pane, with its own Reply, Archive, labels, Move
and Summarise, while the list keeps driving the first pane. On a narrower
window the same command opens the conversation in a window of its own, as
double-click and Return always did.

## Running in the background

*Keep running when the window is closed* under *Account* in Preferences
(off by default) makes closing the window hide it instead: mail keeps
syncing and notifications keep coming, a click on one opens that message,
and *Quit* in the main menu (Ctrl+Q) ends the app.

## Tips

While nothing is selected, the conversation pane shows the app's name as a
dictionary entry and, underneath, a tip: what Clean up does, how rules and
the screener work, the search operators, the shortcuts, and so on, each
with a button to the thing it describes. A new tip comes with every start,
and *Next tip* moves on. Under the tip, a row of quick links opens Clean up,
Newsletters, Rules, the search and the shortcuts, whatever tip is showing.

## Lock

*Lock* in the main menu, or Ctrl+Shift+L, hides the mail behind a lock page
and hides the compose and conversation windows with it; a notification that
arrives meanwhile says "New mail" and nothing else. Turn the lock on under
*Account → Lock* in Preferences, choose to lock after some idle time and
when the desktop session locks, and choose what *Unlock* asks: the system's
own authentication prompt (password, fingerprint, whatever PAM is set up
for) where the polkit policy file is installed, which the AUR package does;
the keyring daemon's prompt for a *Den Mail* keyring of its own, which
works inside a Flatpak and never touches the login keyring; or a passphrase
or PIN set right there. This is a privacy screen, not a security boundary:
the cache and the token are not encrypted by it.

![The Lock section of the Account page](../data/screenshots/lock.png)

## Preferences

Four pages. *General* holds the theme, where sender logos come from (each
sender's site, DuckDuckGo's icon service, BIMI only, or off), remote images
and trusted senders, reading and composing options. *Inbox* is where the
cleanup tools live: rows that open Clean up, Rules and Newsletters with a
line on what each does, the Views switch, the screener and label
suggestions. *Assistant* is the language model. *Account* has the lock,
notifications, background running, the poll interval, the server's
capabilities, the cache and sign-out. The same tools sit under *Inbox* in the main menu, behind the broom
at the bottom of the sidebar, and the Newsletters, Updates and Never read
views offer Clean up for their senders in a banner above the list.

![Preferences](../data/screenshots/preferences.png)

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `j` / `k`, arrows | next / previous conversation |
| `Return` / `o` | open conversation (narrow layout) |
| `c`, `Ctrl+N` | new message |
| `r` / `a` / `f` | reply / reply all / forward |
| `e` | archive |
| `#`, `Delete` | delete |
| `!` | mark as spam |
| `s` | flag / unflag |
| `Shift+U` / `Shift+I` | mark unread / read |
| `l` / `v` | labels / move to |
| `b` | open beside (wide window) or in a new window |
| `Ctrl+Shift+S` | summarise the conversation (assistant) |
| `Ctrl+Shift+L` | lock |
| `/`, `Ctrl+F` | search |
| `g` then `i` / `d` | go to Inbox / Drafts |
| `F5`, `Ctrl+R` | refresh |
| `Ctrl+Return` / `Ctrl+S` | send / save draft (compose) |
| `Escape` | back (narrow layout) |
| `Ctrl+A` | select all |
| `Ctrl+,` / `Ctrl+?` / `Ctrl+Q` | preferences / shortcuts dialog / quit |
