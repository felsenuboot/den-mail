"""Deterministic categoriser: headers, sender and content rules, no dependencies (#18).

Pure functions over the list-property Email object the cache holds (subject,
preview, addresses and a handful of headers).  The first rule that fires wins;
they are ordered by how much a signal can be trusted:

0. mail from one of the user's own addresses             -> primary
1. ``List-Post`` from a person (a list one can write to) -> lists
2. list headers (``List-Unsubscribe``, ``Precedence``, ``List-Id``)
   -> promotions when the wording sells something; updates when a no-reply
   sender shows no newsletter cue (Indeed matches, Google notices, monthly
   reports); else newsletters.  A receipt or a sign-in alert that happens to
   carry them keeps its category.
3. security / transaction wording in the subject         -> security / transactions
4. a ``[list:1234]`` subject tag                         -> lists
5. a sender the user has written to                      -> primary
6. ``Auto-Submitted``, ``Feedback-ID`` or a no-reply style address -> updates
   (their preview may still say it is a code or a receipt)
7. weaker content matches in subject and preview
8. everything else                                       -> primary

``Feedback-ID`` on its own is not a newsletter signal: large senders stamp it
on order confirmations, comment notifications and support replies, and some
providers on the user's own sent mail.  Together with ``List-Unsubscribe`` it
is what every real newsletter carries, so it only counts as "automated".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

PRIMARY = "primary"
TRANSACTIONS = "transactions"
SECURITY = "security"
UPDATES = "updates"
NEWSLETTERS = "newsletters"
LISTS = "lists"
PROMOTIONS = "promotions"

CATEGORIES = (PRIMARY, TRANSACTIONS, SECURITY, UPDATES, NEWSLETTERS, LISTS, PROMOTIONS)
CATEGORY_NAMES = {
    PRIMARY: "Primary",
    TRANSACTIONS: "Transactions",
    SECURITY: "Security",
    UPDATES: "Updates",
    NEWSLETTERS: "Newsletters",
    LISTS: "Lists",
    PROMOTIONS: "Promotions",
}

# JMAP property names for the headers the rules read.  ``asRaw`` because Fastmail
# rejects ``asText`` for at least List-Unsubscribe, and presence is all that matters.
H_LIST_POST = "header:List-Post:asRaw"
H_LIST_ID = "header:List-Id:asRaw"
H_LIST_UNSUBSCRIBE = "header:List-Unsubscribe:asRaw"
H_PRECEDENCE = "header:Precedence:asRaw"
H_AUTO_SUBMITTED = "header:Auto-Submitted:asRaw"
H_FEEDBACK_ID = "header:Feedback-ID:asRaw"
CLASSIFY_HEADERS = (H_LIST_POST, H_LIST_ID, H_LIST_UNSUBSCRIBE, H_PRECEDENCE, H_AUTO_SUBMITTED, H_FEEDBACK_ID)

SOURCE_RULES = "rules"
SOURCE_USER = "user"
# Bump when a rule changes: a cache classified by an older version is run through the rules again.
RULES_VERSION = "2"


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: float
    reason: str


def _rx(*alternatives: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{a})" for a in alternatives), re.IGNORECASE)


# Local parts of automated senders.  "noreply" anywhere (newsletter-noreply@),
# the others only as the whole first label (alerts@, notifications@).
_NOREPLY_RE = re.compile(r"no[-_.]?reply|do[-_.]?not[-_.]?reply|no[-_.]?response|nicht[-_.]?antworten", re.IGNORECASE)
_AUTOMATED_LOCAL_RE = re.compile(
    r"^(?:notifications?|notify|notification-?system|alerts?|alarm|mailer-daemon|postmaster|bounces?|"
    r"automated|automat(?:ic|isch)|system|auto-?confirm|robot|bot|daemon|updates?|info-?mail|service|"
    r"kundenservice|benachrichtigung(?:en)?|newsletter|news|marketing|promo(?:tions?)?|offers?|angebote?|"
    r"security|accounts?|billing|invoices?|receipts?|orders?|order-?(?:update|confirmation|status)|"
    r"bestell\w*|rechnung\w*|shipping|ship-?confirm|versand\w*|delivery|tracking|verify|verification|"
    r"confirm(?:ation)?|password|passwort|comments?-noreply|drive-shares\w*|team-?noreply)"
    r"(?:[-_.+]|$)",
    re.IGNORECASE,
)
# Local parts that are still marketing even without list headers.
_PROMO_LOCAL_RE = re.compile(r"^(?:marketing|promo(?:tions?)?|offers?|angebote?|deals?|sale|sales)(?:[-_.+]|$)",
                             re.IGNORECASE)

# Signs that list mail is an editorial or marketing send rather than a notice:
# the sender's mailbox, name or domain, the subject, or the "view in browser"
# line most campaign tools put first.
_NEWSLETTER_SENDER_RE = re.compile(
    r"news|newsletter|briefing|digest|bulletin|weekly|daily|monthly|magazin|magazine|journal|editor|redaktion|"
    r"freibrief|rundbrief|infobrief|letter|subscri|campaign|mailing|hello|hi\b|team|community|crew|studio|"
    r"press|blog|insights?|report|review|recap|roundup|tips|inspiration|club|friends|members|rewards|store|shop",
    re.IGNORECASE,
)
_NEWSLETTER_SUBJECT_RE = _rx(
    r"\b(?:newsletter|digest|briefing|bulletin|roundup|round-up|recap|edition|issue\s*#?\s*\d+|vol\.?\s*\d+|"
    r"this week|last week|weekly|monthly|quarterly|ausgabe|rundbrief|infobrief|wochenrückblick|kw\s?\d{1,2}|"
    r"what's new|neuigkeiten|top stories|in the news)\b",
)
_VIEW_IN_BROWSER_RE = _rx(
    r"\b(?:view|read|open|see)\s+(?:this\s+(?:email|e-mail|message)\s+)?(?:it\s+)?(?:in|on)\s+(?:your\s+|a\s+)?(?:browser|web)\b",
    r"\b(?:view|read)\s+(?:online|as a web ?page)\b|\bweb[- ]?version\b|\bonline[- ]?version\b",
    r"\bim browser\b|\bbrowser-?ansicht\b|\bonline ansehen\b|\bweb-?version\b|\bhier klicken für die web\b|\bwird diese (?:nachricht|e-?mail) nicht richtig\b",
    r"\bafficher dans\b|\bver en el navegador\b|\bvisualizza nel browser\b|\bin browser bekijken\b",
)
# Automated notices that campaign tools send with list headers: application receipts, account notices.
_NOTICE_RE = _rx(
    r"\b(?:thank(?:s| you) for (?:your )?(?:applying|application|interest in)|your application|application (?:received|confirmation|status|update)|"
    r"we(?:'ve| have) received your|application at|applied to|candidate account|interview (?:request|invitation|confirmation))\b",
    r"\b(?:deine|ihre|eure)\s+bewerbung\b|\bbewerbung\s+(?:eingegangen|erhalten|bei)\b|\b(?:danke|vielen dank) für (?:deine|ihre) bewerbung\b|\bbewerbungseingang\b",
    r"\b(?:account|konto)\s+(?:wird|will be|has been|wurde)\s+(?:gelöscht|deleted|geschlossen|closed|deaktiviert|deactivated)\b",
    r"\b(?:bitte bewerten|rate your|how was your|wie war)\b",
    r"\b(?:postfach|new (?:documents?|messages?)\s+(?:in|available|waiting)|neue (?:dokumente|nachrichten?)|ungelesene nachrichten?)\b",
    r"\b(?:account|konto|kundenkonto|profile|profil)\s+(?:has been |was |wurde |erfolgreich )?(?:created|erstellt|angelegt|updated|aktualisiert)\b",
    r"^(?:updated )?(?:invitation|einladung|accepted|angenommen|declined|abgelehnt|tentative|canceled event|abgesagt)\s*:",
)
# "[gsba-bkyomu:35917]": the numbered subject tag of list software that sets no list headers
_LIST_TAG_RE = re.compile(r"\[[A-Za-z][\w.-]{1,40}:\d{2,7}\]")

# --- Security: codes, passwords, sign-ins (EN + DE)
_SECURITY_RE = _rx(
    r"\b(?:verification|verify|confirmation|security|login|log-in|sign-?in|authentication|auth|access|one-?time|2fa|two-?factor|otp|recovery)\s+(?:code|pin|passcode|password|link)\b",
    r"\b(?:code|passcode|pin)\s+(?:is|to|for)\b",
    r"\bis your\s+(?:\w+\s+)?(?:code|passcode|pin)\b",
    r"\byour\s+(?:\w+\s+)?(?:code|passcode|otp)\b",
    r"\bone[-\s]?time\s+(?:password|passcode|pin|code)\b",
    r"\b(?:reset|change|changed|update|updated|forgot(?:ten)?)\s+(?:your\s+)?password\b",
    r"\bpassword\s+(?:reset|change|changed|updated|expir\w*|recovery)\b",
    r"\bnew\s+(?:sign-?in|log-?in|login|device|browser|location)\b",
    r"\b(?:signed|logged)\s+in\s+(?:from|on|to)\b",
    r"\b(?:unusual|suspicious|unrecognized|unrecognised|unfamiliar|unknown|new)\s+(?:activity|sign-?in|login|access|attempt|device)\b",
    r"\bsecurity\s+(?:alert|notice|notification|warning|update|key|check)\b",
    r"\b(?:verify|confirm|activate)\s+your\s+(?:\w+\s+){0,2}(?:e-?mail|email address|account|identity|address|registration|phone)\b",
    r"\b(?:email|e-mail|account)\s+(?:verification|confirmation|activation)\b",
    r"\bmagic\s+link\b",
    r"\btwo[-\s]?factor\b|\b2fa\b|\bmfa\b",
    r"\baccount\s+(?:locked|suspended|compromised|recovery|access)\b",
    r"\b(?:passkey|api key|access token)\b",
    # German
    r"\b(?:bestätigungs|verifizierungs|sicherheits|anmelde|login|einmal|zugangs|freischalt|wiederherstellungs|authentifizierungs)-?(?:code|pin|passwort|kennwort|link)\b",
    r"\b(?:ihr|dein|der)\s+(?:\w+\s+)?(?:code|einmalcode|einmalpasswort|pin)\b",
    r"\bcode\s+(?:lautet|ist|zur|für)\b",
    r"\b(?:passwort|kennwort)\s+(?:zurücksetzen|zuruecksetzen|ändern|geändert|aendern|geaendert|vergessen|erneuern|abgelaufen)\b",
    r"\b(?:neues|dein neues|ihr neues)\s+(?:passwort|kennwort)\b|\banmeldewarnung\b|\banmeldeversuch\b",
    # Japanese and Chinese: verification codes, passwords, new logins
    r"検証コード|認証コード|確認コード|ワンタイム|パスワード(?:の)?(?:再設定|リセット|変更)|新しいデバイス|新しいログイン|验证码|驗證碼|重置密码|新设备登录",
    r"\b(?:neues?|neuer)\s+(?:anmeldung|login|gerät|geraet|browser|standort)\b",
    r"\banmeldung\s+(?:von|über|ueber|auf|mit)\s+(?:einem\s+)?(?:neuen|unbekannten)\b",
    r"\b(?:verdächtige|verdaechtige|ungewöhnliche|ungewoehnliche|unbekannte)\s+(?:aktivität|aktivitaet|anmeldung|zugriff)\b",
    r"\bsicherheits(?:warnung|hinweis|benachrichtigung|meldung|überprüfung|ueberpruefung|schlüssel|schluessel)\b",
    r"\b(?:e-?mail(?:-adresse)?|konto|account|identität|identitaet|registrierung|telefonnummer)\s+(?:bestätigen|bestaetigen|verifizieren|aktivieren)\b",
    r"\b(?:bestätigen|bestaetigen|verifizieren|aktivieren)\s+sie\s+(?:ihre?|dein|deine)\b",
    r"\b(?:bestätige|bestaetige)\s+(?:deine|dein)\b",
    r"\bzwei[-\s]?faktor\b",
    r"\bkonto\s+(?:gesperrt|wiederherstellen|wiederherstellung)\b",
)
# A short number next to "code": "123456 is your code", "Code: 4711".
_CODE_DIGITS_RE = re.compile(r"\b\d{4,8}\b.{0,40}\b(?:code|pin|passcode)\b|\b(?:code|pin|passcode)\b.{0,40}\b\d{4,8}\b",
                             re.IGNORECASE | re.DOTALL)

# --- Transactions: receipts, orders, shipping, tickets, bookings (EN + DE)
_TRANSACTION_RE = _rx(
    r"\b(?:receipt|invoice|e-?invoice|bill|billing|statement|refund|reimbursement|payout|payment|purchase|transaction|subscription|renewal|renewed)\b",
    r"\b(?:your|an|the|new)\s+order\b|\border\s+(?:confirmation|confirmed|update|status|shipped|received|placed|number|no\.?|#|is|has|was|details)\b|#\s?\d{4,}\b.*\border\b|\border\b.*#\s?\d{4,}\b",
    r"\bthank(?:s| you) for (?:your|the) (?:order|purchase|payment|booking|reservation)\b",
    r"\b(?:has|have|is|was|been)\s+(?:shipped|dispatched|delivered|sent out|despatched)\b|\bshipp(?:ed|ing)\b|\bdispatch(?:ed)?\b",
    r"\b(?:out for|scheduled for|estimated|expected)\s+delivery\b|\bdeliver(?:y|ed)\b(?!\s+(?:of|the)\s+(?:news|update))",
    r"\b(?:your|the)\s+(?:package|parcel|shipment|delivery|item|items)\b|\bon (?:its|the) way\b|\btracking\s+(?:number|no|info|link|update)\b|\btrack your\b",
    r"\b(?:your|e-?|mobile|train|flight|event|concert)\s*tickets?\b|\btickets?\s+(?:confirmed|attached|for|are|is)\b",
    r"\b(?:booking|reservation)\s+(?:confirmation|confirmed|details|reference|number|updated|cancel\w*)\b|\byour\s+(?:booking|reservation|stay|trip|flight|journey|itinerary|rental)\b",
    r"\b(?:boarding pass|itinerary|check-?in\s+(?:is|now|open|reminder)|e-?ticket)\b",
    r"\b(?:return|returns)\s+(?:label|confirmed|received|instructions)\b|\bpre-?order\b|\bback-?order\b",
    r"\b(?:direct debit|bank transfer|wire transfer|card payment|payment method|charged|charge of|amount due|now due|past due|overdue)\b",
    r"\bappointment\s+(?:confirmed|confirmation|reminder|booked|scheduled)\b",
    r"\b(?:ihr|dein|der|zum|ihrem|deinem)\s+[\w.-]*auftrag\b|\bauftrags?(?:nummer|nr|bestätigung|bestaetigung|eingang|status)\b|\S-auftrag\b",
    r"^(?:ordered|delivered|shipped|dispatched|bestellt|geliefert|versandt|versendet|zugestellt|unterwegs)\b",
    r"\bpre-?flight\b|\bflight\s+(?:reminder|details|confirmation|confirmed|change)\b",
    # Japanese and Chinese: orders, receipts, shipping, bookings
    r"注文|領収書|請求書|発送|配送|お届け|予約確認|ご予約|お支払い|订单|发票|发货|收据|预订",
    # German
    r"\b(?:rechnung|e-?rechnung|quittung|beleg|kaufbeleg|kassenbon|gutschrift|erstattung|rückerstattung|rueckerstattung|auszahlung|zahlung|zahlungseingang|zahlungsbestätigung|zahlungsbestaetigung|zahlungserinnerung|mahnung|kontoauszug|abrechnung|abonnement|abo-?verlängerung|verlaengerung)\b",
    r"\b(?:ihre|deine|neue|die)\s+bestellung\b|\bbestell(?:ung|bestätigung|bestaetigung|nummer|status|eingang)\b|\bauftrags?(?:bestätigung|bestaetigung|nummer|eingang)\b",
    r"\b(?:vielen\s+)?dank\s+für\s+(?:ihre|deine|die)\s+(?:bestellung|zahlung|buchung|reservierung)\b",
    r"\b(?:wurde|ist|sind|wurden)\s+(?:versandt|verschickt|versendet|geliefert|zugestellt|abgeschickt)\b|\bversand(?:bestätigung|bestaetigung|benachrichtigung|status|mitteilung)\b",
    r"\b(?:lieferung|zustellung|sendung|sendungsverfolgung|sendungsnummer|paket|päckchen|paeckchen|lieferstatus|zustellstatus|liefertermin)\b",
    r"\b(?:ihr|dein|das)\s+paket\b|\bunterwegs\s+zu\s+(?:ihnen|dir)\b|\bist\s+unterwegs\b",
    r"\b(?:fahrkarte|fahrschein|bahnticket|flugticket|bordkarte|eintrittskarte|online-?ticket|handy-?ticket)\b|\b(?:ihr|dein)\s+ticket\b|\btickets?\s+(?:bestätigt|bestaetigt|anbei|im anhang|für)\b",
    r"\b(?:buchung|reservierung)s?(?:bestätigung|bestaetigung|nummer|details|code)?\b(?!\s*(?:jetzt|heute))|\b(?:ihre|deine)\s+(?:buchung|reise|reservierung|fahrt|flug|unterkunft)\b",
    r"\b(?:retoure|rücksendung|ruecksendung|rücksendeetikett|retourenschein|vorbestellung)\b",
    r"\b(?:lastschrift|abbuchung|überweisung|ueberweisung|zahlungsart|belastet|fällig|faellig|überfällig|ueberfaellig|zahlungsziel)\b",
    r"\btermin(?:bestätigung|bestaetigung|erinnerung)\b|\btermin\s+(?:bestätigt|bestaetigt|gebucht|vereinbart)\b",
)

# --- Promotions: sales talk (EN + DE), used to split bulk mail from newsletters
_PROMO_RE = _rx(
    r"\d+\s?%(?:\s*(?:off|discount|rabatt|reduziert|günstiger|guenstiger|sparen|weniger))?",
    r"\b(?:sale|deal|deals|offer|offers|special offer|discount|coupon|voucher|promo|promotion|promo code|clearance|bargain|savings?|cashback|reward|rewards|bonus)\b",
    r"\b(?:save|get)\s+(?:up to\s+)?(?:\$|€|£)?\s?\d+",
    r"\b(?:free|complimentary)\s+(?:shipping|delivery|gift|trial|month|sample)\b|\bbuy\s+one\b|\bbogo\b|\b2\s?for\s?1\b",
    r"\b(?:last chance|final hours|ends?\s+(?:soon|tonight|today|tomorrow|sunday|midnight)|limited[-\s]time|limited offer|while (?:stocks?|supplies) last|don't miss|hurry|only\s+(?:today|until|\d+\s+(?:hours|days)\s+left)|today only|this week(?:end)? only|expires?\s+(?:soon|today|tonight|in)|flash sale)\b",
    r"\b(?:black friday|cyber monday|prime day|singles' day|boxing day|summer sale|winter sale|spring sale|holiday sale|new year sale|christmas (?:sale|deals|offers))\b",
    r"\b(?:new (?:arrivals|collection|in|season)|just (?:dropped|landed|arrived)|introducing|shop now|shop the|order now|book now|treat yourself|exclusive(?:ly)? (?:for|offer|deal|access)|members? only|vip)\b",
    r"\b(?:giveaway|win a|chance to win|sweepstakes|prize draw|competition)\b",
    r"\b(?:bestsellers?|best sellers|trending|top picks|recommended for you|you might (?:also )?like|picked for you|back in stock|price drop|now (?:only|just|from)|from (?:only|just)\s*(?:\$|€|£))\b",
    r"\b(?:unlock|upgrade)\s+(?:your|to)\b|\bupgrade now\b|\bstart your (?:free )?trial\b|\btry .{0,20}free\b",
    # German
    r"\b(?:rabatt|rabatte|rabattcode|gutschein|gutscheincode|gutscheine|aktion|aktionen|aktionscode|angebot|angebote|sonderangebot|sonderangebote|schnäppchen|schnaeppchen|ausverkauf|schlussverkauf|sale|deal|deals|preisnachlass|preissenkung|preisvorteil|ersparnis|prozente|sparpreis|sparen|spare|sparaktion|bonus|prämie|praemie|cashback|nur für kurze zeit|solange der vorrat reicht)\b",
    r"\b(?:jetzt|heute)\s+(?:sparen|sichern|zugreifen|shoppen|bestellen|entdecken|kaufen|buchen|zuschlagen|profitieren)\b|\bjetzt\s+(?:nur|ab|für)\s*(?:€|eur)?\s?\d",
    r"\b(?:kostenlose?r?|gratis|geschenkt|umsonst)\s+(?:versand|lieferung|geschenk|testen|probe|monat|zugabe)\b|\bversandkostenfrei\b|\bgratis\b",
    r"\b(?:letzte chance|letzter tag|nur (?:noch )?(?:heute|bis|kurz|\d+\s+(?:tage|stunden))|endet (?:bald|heute|morgen|sonntag)|nur für kurze zeit|begrenzte zeit|limitiert|exklusiv(?:e|es|er)?|neu eingetroffen|neu im (?:shop|sortiment)|neuheiten|neue kollektion|bestseller|top-?angebote|unsere (?:angebote|highlights|empfehlungen|bestseller|favoriten)|für dich ausgewählt|für sie ausgewählt|wieder (?:da|verfügbar|verfuegbar)|reduziert|preis gesenkt|ab nur|schon ab)\b",
    r"\b(?:gewinnspiel|gewinne|verlosung|mitmachen und gewinnen)\b",
    r"\b(?:jetzt (?:upgraden|freischalten|testen)|kostenlos testen|probemonat|testphase)\b",
    # Japanese and Chinese: sales, discounts, coupons
    r"セール|割引|クーポン|キャンペーン|期間限定|特価|促销|折扣|优惠券|限时",
)


def _header(email: dict, key: str) -> str:
    value = email.get(key)
    return value.strip() if isinstance(value, str) else ""


def _local_part(addr: str) -> str:
    return addr.split("@", 1)[0].lower() if "@" in addr else addr.lower()


def sender_address(email: dict) -> str:
    frm = email.get("from") or []
    addr = (frm[0].get("email") if frm and isinstance(frm[0], dict) else "") or ""
    return addr.strip().lower()


def is_noreply(addr: str) -> bool:
    """noreply@, no-reply@, donotreply@ and the other automated-sender local parts."""
    local = _local_part(addr)
    return bool(local) and (bool(_NOREPLY_RE.search(local)) or bool(_AUTOMATED_LOCAL_RE.match(local)))


def is_auto_submitted(email: dict) -> bool:
    auto = _header(email, H_AUTO_SUBMITTED).lower()
    return bool(auto) and auto != "no" and not auto.startswith("no ")


def is_automated(email: dict) -> bool:
    """Machine-sent: Auto-Submitted (anything but "no"), Feedback-ID, or a no-reply style sender."""
    return is_auto_submitted(email) or bool(_header(email, H_FEEDBACK_ID)) or is_noreply(sender_address(email))


def bulk_reason(email: dict) -> str:
    """Which header marks this as list mail (newsletter, campaign or notice run), or ""."""
    if _header(email, H_LIST_UNSUBSCRIBE):
        return "List-Unsubscribe"
    prec = _header(email, H_PRECEDENCE).lower()
    if prec in ("bulk", "list", "junk"):
        return f"Precedence: {prec}"
    if _header(email, H_LIST_ID):
        return "List-Id"
    return ""


def is_discussion_list(email: dict) -> bool:
    """List-Post names an address to write to; "NO" means a one-way list (RFC 2369).
    A no-reply sender with List-Post (GitHub, Jira) is a notice, not a list."""
    post = _header(email, H_LIST_POST)
    return bool(post) and post.upper() != "NO" and not is_noreply(sender_address(email))


def newsletter_cue(email: dict, subject: str, preview: str) -> bool:
    """Something that says "editorial or campaign send": the sender's mailbox,
    name or domain, the subject, or a "view in browser" line."""
    frm = (email.get("from") or [{}])[0] if email.get("from") else {}
    addr = ((frm or {}).get("email") or "").lower()
    name = ((frm or {}).get("name") or "").lower()
    local, _, domain = addr.partition("@")
    labels = " ".join(domain.split(".")[:-1])  # every label but the TLD
    if _NEWSLETTER_SENDER_RE.search(f"{local} {name} {labels}"):
        return True
    return bool(_NEWSLETTER_SUBJECT_RE.search(subject)) or bool(_VIEW_IN_BROWSER_RE.search(preview))


def security_text(text: str) -> bool:
    return bool(text) and bool(_SECURITY_RE.search(text))


def transaction_text(text: str) -> bool:
    return bool(text) and bool(_TRANSACTION_RE.search(text))


def promotion_text(text: str) -> bool:
    return bool(text) and bool(_PROMO_RE.search(text))


def code_digits(text: str) -> bool:
    return bool(text) and bool(_CODE_DIGITS_RE.search(text))


def classify(email: dict, written_to: Callable[[str], bool] | None = None,
             is_own: Callable[[str], bool] | None = None) -> Classification:
    """Category of one list-property Email.  `written_to(address)` says whether the
    user has ever sent mail to that address (a Sent-folder signal the cache keeps);
    `is_own(address)` whether it is one of the user's identities."""
    subject = " ".join((email.get("subject") or "").split())
    preview = " ".join((email.get("preview") or "").split())
    sender = sender_address(email)

    if sender and is_own is not None and is_own(sender):
        return Classification(PRIMARY, 0.9, "sent by you")

    if is_discussion_list(email):
        return Classification(LISTS, 0.95, "List-Post")

    bulk = bulk_reason(email)
    if bulk:
        if promotion_text(subject) or _PROMO_LOCAL_RE.match(_local_part(sender) or "-"):
            return Classification(PROMOTIONS, 0.85, f"{bulk}, sales wording in the subject")
        if security_text(subject):
            return Classification(SECURITY, 0.8, f"security wording in the subject ({bulk})")
        if transaction_text(subject):
            return Classification(TRANSACTIONS, 0.75, f"transaction wording in the subject ({bulk})")
        if promotion_text(preview):
            return Classification(PROMOTIONS, 0.7, f"{bulk}, sales wording in the text")
        if _NOTICE_RE.search(subject):
            return Classification(UPDATES, 0.7, f"notice wording in the subject ({bulk})")
        if (is_noreply(sender) or is_auto_submitted(email)) and not newsletter_cue(email, subject, preview):
            return Classification(UPDATES, 0.7, f"{bulk} from a no-reply sender, no newsletter cue")
        return Classification(NEWSLETTERS, 0.8, bulk)

    if security_text(subject):
        return Classification(SECURITY, 0.85, "security wording in the subject")
    if transaction_text(subject):
        return Classification(TRANSACTIONS, 0.8, "transaction wording in the subject")
    if _LIST_TAG_RE.search(subject):
        return Classification(LISTS, 0.6, "numbered list tag in the subject")

    if sender and written_to is not None and written_to(sender):
        return Classification(PRIMARY, 0.8, "a sender you have written to")

    if is_automated(email):
        if code_digits(subject) or security_text(preview) or code_digits(preview):
            return Classification(SECURITY, 0.7, "automated sender, security wording")
        if transaction_text(preview):
            return Classification(TRANSACTIONS, 0.65, "automated sender, transaction wording")
        if promotion_text(subject) or _PROMO_LOCAL_RE.match(_local_part(sender) or "-"):
            return Classification(PROMOTIONS, 0.65, "automated sender, sales wording")
        reason = ("Auto-Submitted" if is_auto_submitted(email) else
                  "no-reply sender" if is_noreply(sender) else "Feedback-ID")
        return Classification(UPDATES, 0.75, reason)

    if code_digits(subject) or security_text(preview):
        return Classification(SECURITY, 0.6, "security wording")
    if _NOTICE_RE.search(subject):
        return Classification(UPDATES, 0.6, "notice wording in the subject")
    if transaction_text(preview):
        return Classification(TRANSACTIONS, 0.55, "transaction wording in the text")
    if promotion_text(subject):
        return Classification(PROMOTIONS, 0.55, "sales wording in the subject")
    return Classification(PRIMARY, 0.5, "no automated-mail signals")
