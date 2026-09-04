"""Deterministic categoriser: headers, sender and content rules, no dependencies (#18).

Pure functions over the list-property Email object the cache holds (subject,
preview, addresses and a handful of headers).  The first rule that fires wins;
they are ordered by how much a signal can be trusted:

1. ``List-Post`` (a list one can write to)            -> lists
2. bulk headers (``List-Unsubscribe``, ``Precedence``, ``Feedback-ID``, ``List-Id``)
   -> promotions when the wording sells something, else newsletters
   (a receipt or a sign-in alert that happens to carry them keeps its category)
3. security / transaction wording in the subject       -> security / transactions
4. a sender the user has written to                     -> primary
5. ``Auto-Submitted`` or a no-reply style address       -> updates
   (their preview may still say it is a code or a receipt)
6. weaker content matches in subject and preview
7. everything else                                      -> primary
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
    r"kundenservice|benachrichtigung(?:en)?|newsletter|news|marketing|promo(?:tions?)?|offers?|angebote?)"
    r"(?:[-_.+]|$)",
    re.IGNORECASE,
)
# Local parts that are still marketing even without list headers.
_PROMO_LOCAL_RE = re.compile(r"^(?:marketing|promo(?:tions?)?|offers?|angebote?|deals?|sale|sales)(?:[-_.+]|$)",
                             re.IGNORECASE)

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
    r"\b(?:unusual|suspicious|unrecognized|unrecognised|unknown|new)\s+(?:activity|sign-?in|login|access|attempt|device)\b",
    r"\bsecurity\s+(?:alert|notice|notification|warning|update|key|check)\b",
    r"\b(?:verify|confirm|activate)\s+your\s+(?:e-?mail|email address|account|identity|address|registration|phone)\b",
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
    r"\b(?:receipt|invoice|e-?invoice|bill|billing|statement|refund|reimbursement|payout|payment|purchase|transaction|subscription|renewal)\b",
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


def is_automated(email: dict) -> bool:
    """Auto-Submitted (anything but "no") or a no-reply style sender."""
    auto = _header(email, H_AUTO_SUBMITTED).lower()
    if auto and auto != "no" and not auto.startswith("no "):
        return True
    return is_noreply(sender_address(email))


def bulk_reason(email: dict) -> str:
    """Which header marks this as bulk mail (newsletter or campaign), or ""."""
    if _header(email, H_LIST_UNSUBSCRIBE):
        return "List-Unsubscribe"
    prec = _header(email, H_PRECEDENCE).lower()
    if prec in ("bulk", "list", "junk"):
        return f"Precedence: {prec}"
    if _header(email, H_FEEDBACK_ID):
        return "Feedback-ID"
    if _header(email, H_LIST_ID):
        return "List-Id"
    return ""


def is_discussion_list(email: dict) -> bool:
    """List-Post names an address to write to; "NO" means a one-way list (RFC 2369)."""
    post = _header(email, H_LIST_POST)
    return bool(post) and post.upper() != "NO"


def security_text(text: str) -> bool:
    return bool(text) and bool(_SECURITY_RE.search(text))


def transaction_text(text: str) -> bool:
    return bool(text) and bool(_TRANSACTION_RE.search(text))


def promotion_text(text: str) -> bool:
    return bool(text) and bool(_PROMO_RE.search(text))


def code_digits(text: str) -> bool:
    return bool(text) and bool(_CODE_DIGITS_RE.search(text))


def classify(email: dict, written_to: Callable[[str], bool] | None = None) -> Classification:
    """Category of one list-property Email; `written_to(address)` says whether the
    user has ever sent mail to that address (a Sent-folder signal the cache keeps)."""
    subject = " ".join((email.get("subject") or "").split())
    preview = " ".join((email.get("preview") or "").split())
    sender = sender_address(email)

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
        return Classification(NEWSLETTERS, 0.8, bulk)

    if security_text(subject):
        return Classification(SECURITY, 0.85, "security wording in the subject")
    if transaction_text(subject):
        return Classification(TRANSACTIONS, 0.8, "transaction wording in the subject")

    if sender and written_to is not None and written_to(sender):
        return Classification(PRIMARY, 0.8, "a sender you have written to")

    if is_automated(email):
        if code_digits(subject) or security_text(preview) or code_digits(preview):
            return Classification(SECURITY, 0.7, "automated sender, security wording")
        if transaction_text(preview):
            return Classification(TRANSACTIONS, 0.65, "automated sender, transaction wording")
        if promotion_text(subject) or _PROMO_LOCAL_RE.match(_local_part(sender) or "-"):
            return Classification(PROMOTIONS, 0.65, "automated sender, sales wording")
        reason = "Auto-Submitted" if _header(email, H_AUTO_SUBMITTED) else "no-reply sender"
        return Classification(UPDATES, 0.75, reason)

    if code_digits(subject) or security_text(preview):
        return Classification(SECURITY, 0.6, "security wording")
    if transaction_text(preview):
        return Classification(TRANSACTIONS, 0.55, "transaction wording in the text")
    if promotion_text(subject):
        return Classification(PROMOTIONS, 0.55, "sales wording in the subject")
    return Classification(PRIMARY, 0.5, "no automated-mail signals")
