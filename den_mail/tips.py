"""Tips shown under the placeholder while nothing is selected: one per start, in turn."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tip:
    title: str
    text: str
    action: str | None = None   # a window action, e.g. "win.cleanup"
    button: str = ""


TIPS: tuple[Tip, ...] = (
    Tip("Clean up the senders you never read",
        "Clean up lists every sender, the most pointless first: how much they send, how much of it you open, "
        "whether you ever wrote back. Tick a few and archive, delete, mark read or unsubscribe in one go.",
        "win.cleanup", "Open Clean up"),
    Tip("Decide once what happens to a sender",
        "Right-click a conversation and choose “Always for this sender…”: label, archive, read or delete their "
        "mail as it arrives, and their old mail too if you like. Rules… in the main menu lists what you set up.",
        "win.rules", "Rules…"),
    Tip("Leave newsletters many at a time",
        "Newsletters… finds every sender with an unsubscribe header. Tick the ones you are done with and the app "
        "sends the requests, optionally archiving or deleting their mail.",
        "win.newsletters", "Open Newsletters"),
    Tip("Keep strangers out of the Inbox",
        "With “Screen first-time senders” on, mail from anyone you have never heard from waits in a Screener "
        "view until you let them through or screen them out.",
        "win.preferences-inbox", "Inbox preferences"),
    Tip("The Views in the sidebar are questions to your mail",
        "Never read shows senders whose mail you have not opened in months; Big attachments what is worth "
        "deleting for space; the category views what the app sorted as newsletters, receipts, codes or notices."),
    Tip("Search with operators",
        "from:anna is:unread has:attachment older_than:7d label:work — combine them, quote phrases, and switch "
        "the scope to all mail to search the whole account.",
        "win.search", "Search"),
    Tip("Let a language model read the long thread",
        "With an assistant set up, the sparkle in the conversation header sums a thread up in a few lines, and "
        "Clean up shows a one-line description of a sender's newest message. Ollama on this machine keeps "
        "the mail text local; other providers plug in.",
        "win.preferences-assistant", "Assistant preferences"),
    Tip("Keyboard first",
        "j and k move, e archives, # deletes, r replies, s flags, l labels, v moves, / searches, g then i goes "
        "to the Inbox. The full list is in the shortcuts dialog.",
        "win.shortcuts", "Keyboard shortcuts"),
    Tip("Group by sender",
        "The sort menu can group the list by sender or by organisation: one card per sender, folded or open, "
        "which you can select and act on as a whole."),
    Tip("Narrow the list to one category",
        "The funnel in the list header shows only Newsletters, Transactions, Security, Updates, Lists, "
        "Promotions or Primary; the category chip on each row says what the app decided."),
    Tip("Take a sent message back",
        "A sent message waits behind an Undo toast for a few seconds before it really goes out. The delay is "
        "in Preferences under Composing.",
        "win.preferences", "Preferences"),
    Tip("A different address for every sign-up",
        "Masked Email creates addresses that forward to you and can be switched off when one starts leaking.",
        "win.masked", "Masked Email"),
    Tip("Drag conversations onto labels",
        "Drop a conversation on a label or folder in the sidebar to move it; hold Ctrl to add the label and "
        "leave it where it is."),
    Tip("Send from any of your addresses",
        "Identities & Aliases lists every address you may send from, wildcards included; star the ones you use "
        "so the From list stays short.",
        "win.identities", "Identities & Aliases"),
    Tip("Remote images stay off until you say so",
        "HTML mail loads no remote content by default, since loading it tells the sender you opened the "
        "message. Allow it once, or always for a sender you trust.",
        "win.preferences", "Preferences"),
)


def tip_for(index: int) -> Tip:
    return TIPS[index % len(TIPS)]
