"""A naive Bayes classifier over the cache, trained from the user's corrections (#23).

Pure functions and a small model class; the cache (store/db.py) keeps the
counts in two tables and calls `predict` when the rules are unsure.

Features of a message are tokens: the sender's address, local part and
domain, words of the subject and the preview (three letters or more), the
list and automation headers that are present, and the user's behaviour
towards the sender (never opened, mostly unread, deleted unread, replied),
so the model can learn that a "friendly" sender the user never reads is a
notice, which the rules cannot tell (#40).  Training data are the user's
corrections (weighted) and the rules' confident verdicts, so the model
agrees with the rules where they are sure and generalises from the
corrections where they are not.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .rules import CATEGORIES, CLASSIFY_HEADERS, sender_address

MIN_TRAINING_DOCS = 20        # below this the model stays silent
MIN_CORRECTIONS = 3           # ... and it needs a few corrections to be worth asking
MIN_PROBABILITY = 0.85        # how sure the model must be to override an unsure rules verdict
CORRECTION_WEIGHT = 4         # a correction counts this many times a rules verdict
MAX_TOKENS_PER_DOC = 80
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def tokens(email: dict, behaviour: dict | None = None) -> list[str]:
    """The features of one list-property Email; `behaviour` is the sender's
    stats (count, unread, deleted_unread, replied) when known."""
    out: list[str] = []
    addr = sender_address(email)
    if addr:
        local, _, domain = addr.partition("@")
        out.append(f"from:{addr}")
        out.append(f"domain:{domain}")
        out.append(f"local:{local}")
        for label in domain.split(".")[:-1]:
            if len(label) >= 3:
                out.append(f"dom:{label}")
    frm = (email.get("from") or [{}])[0] if email.get("from") else {}
    for w in _WORD_RE.findall(((frm or {}).get("name") or "").lower())[:6]:
        out.append(f"name:{w}")
    subject = (email.get("subject") or "").lower()
    out += [f"s:{w}" for w in _WORD_RE.findall(subject)[:20]]
    preview = (email.get("preview") or "").lower()
    out += [f"p:{w}" for w in _WORD_RE.findall(preview)[:30]]
    for h in CLASSIFY_HEADERS:
        v = email.get(h)
        if isinstance(v, str) and v.strip():
            out.append("h:" + h.split(":")[1].lower())
    if behaviour:
        count = int(behaviour.get("count") or 0)
        unread = int(behaviour.get("unread") or 0)
        if count >= 3 and unread == count:
            out.append("b:never-opened")
        elif count >= 3 and unread / count >= 0.7:
            out.append("b:mostly-unread")
        elif count >= 3 and unread / count <= 0.2:
            out.append("b:mostly-read")
        if int(behaviour.get("deleted_unread") or 0) >= 2:
            out.append("b:deleted-unread")
        if behaviour.get("replied"):
            out.append("b:replied")
    return list(dict.fromkeys(out))[:MAX_TOKENS_PER_DOC]


@dataclass
class Prediction:
    category: str
    probability: float
    evidence: list[tuple[str, float]]   # the tokens that spoke loudest for the category, with their log-odds

    @property
    def reason(self) -> str:
        top = ", ".join(t.split(":", 1)[1] if ":" in t else t for t, _ in self.evidence[:4])
        return f"learned from your corrections ({top})" if top else "learned from your corrections"


class BayesModel:
    """Multinomial naive Bayes with Laplace smoothing over the token counts."""

    def __init__(self) -> None:
        self.docs: Counter[str] = Counter()                     # category -> weighted documents
        self.counts: dict[str, Counter[str]] = defaultdict(Counter)   # category -> token -> weighted count
        self.totals: Counter[str] = Counter()                   # category -> sum of token counts
        self.vocabulary: set[str] = set()
        self.corrections = 0

    # ------------------------------------------------------------ training

    def add(self, category: str, toks: list[str], weight: int = 1) -> None:
        if category not in CATEGORIES or not toks:
            return
        self.docs[category] += weight
        for t in toks:
            self.counts[category][t] += weight
            self.totals[category] += weight
            self.vocabulary.add(t)

    @property
    def size(self) -> int:
        return sum(self.docs.values())

    @property
    def ready(self) -> bool:
        return self.size >= MIN_TRAINING_DOCS and self.corrections >= MIN_CORRECTIONS and len(self.docs) >= 2

    # ------------------------------------------------------------ predicting

    def predict(self, toks: list[str]) -> Prediction | None:
        if not self.docs or not toks:
            return None
        total_docs = self.size
        v = len(self.vocabulary) or 1
        scores: dict[str, float] = {}
        per_token: dict[str, dict[str, float]] = {}
        for c in self.docs:
            prior = math.log(self.docs[c] / total_docs)
            denom = self.totals[c] + v
            s = prior
            per_token[c] = {}
            for t in toks:
                lp = math.log((self.counts[c].get(t, 0) + 1) / denom)
                per_token[c][t] = lp
                s += lp
            scores[c] = s
        best = max(scores, key=scores.get)
        m = max(scores.values())
        z = sum(math.exp(s - m) for s in scores.values())
        prob = math.exp(scores[best] - m) / z
        others = [c for c in scores if c != best]
        evidence = []
        if others:
            for t in toks:
                rest = max(per_token[c][t] for c in others)
                evidence.append((t, per_token[best][t] - rest))
            evidence.sort(key=lambda e: -e[1])
            evidence = [e for e in evidence if e[1] > 0][:6]
        return Prediction(best, prob, evidence)

    # ------------------------------------------------------------ storage

    def rows(self) -> list[tuple[str, str, int]]:
        return [(c, t, n) for c, cs in self.counts.items() for t, n in cs.items()]

    @classmethod
    def from_rows(cls, docs: dict[str, int], rows: list[tuple[str, str, int]], corrections: int) -> BayesModel:
        m = cls()
        for c, n in docs.items():
            m.docs[c] = n
        for c, t, n in rows:
            m.counts[c][t] += n
            m.totals[c] += n
            m.vocabulary.add(t)
        m.corrections = corrections
        return m
