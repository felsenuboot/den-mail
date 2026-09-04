"""Label suggestions learned from the user's labelled mail (#60).

One binary naive Bayes model per label, trained on the cached messages that
carry the label against a sample of recent ones that do not, over the same
tokens as the category model. A label is suggested for a message when its
model is confident enough and the message does not carry the label yet; the
chip on the conversation applies it with one click. Folders (role
mailboxes) are never suggested, only labels.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

MIN_POSITIVES = 8          # labelled messages a label needs before its model says anything
MIN_PROBABILITY = 0.92     # how sure a model must be to suggest its label
NEGATIVE_RATIO = 4         # unlabelled examples per labelled one, at most
MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class Suggestion:
    label_id: str
    probability: float
    evidence: tuple[str, ...]   # the tokens that spoke loudest for the label


class LabelModel:
    """Per label: weighted document counts and token counts for "has it" and "has not"."""

    def __init__(self) -> None:
        self.docs: dict[str, Counter[str]] = defaultdict(Counter)                 # label -> {"yes": n, "no": n}
        self.counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))  # label -> side -> token -> n
        self.totals: dict[str, Counter[str]] = defaultdict(Counter)               # label -> side -> sum of counts
        self.vocabulary: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------ training

    def add(self, label_id: str, side: str, toks: list[str]) -> None:
        if not toks or side not in ("yes", "no"):
            return
        self.docs[label_id][side] += 1
        for t in toks:
            self.counts[label_id][side][t] += 1
            self.totals[label_id][side] += 1
            self.vocabulary[label_id].add(t)

    @classmethod
    def train(cls, examples: Iterable[tuple[list[str], set[str]]], labels: Iterable[str]) -> LabelModel:
        """`examples` are (tokens, the message's label ids), newest first; `labels` the ids
        worth a model. Every message is a positive for its labels and, up to the ratio,
        a negative for the others."""
        labels = list(labels)
        model = cls()
        negatives: Counter[str] = Counter()
        positives: Counter[str] = Counter()
        rows = list(examples)
        for toks, present in rows:
            for label in labels:
                if label in present:
                    positives[label] += 1
                    model.add(label, "yes", toks)
        for toks, present in rows:
            for label in labels:
                if label not in present and negatives[label] < positives[label] * NEGATIVE_RATIO:
                    negatives[label] += 1
                    model.add(label, "no", toks)
        for label in labels:
            if positives[label] < MIN_POSITIVES or negatives[label] == 0:
                model.forget(label)
        return model

    def forget(self, label_id: str) -> None:
        for d in (self.docs, self.counts, self.totals, self.vocabulary):
            d.pop(label_id, None)

    @property
    def labels(self) -> list[str]:
        return [label for label, docs in self.docs.items() if docs["yes"] >= MIN_POSITIVES and docs["no"] > 0]

    @property
    def ready(self) -> bool:
        return bool(self.labels)

    # ------------------------------------------------------------ predicting

    def probability(self, label_id: str, toks: list[str]) -> tuple[float, list[tuple[str, float]]]:
        """P(label | tokens) with Laplace smoothing, and each token's pull towards the label."""
        docs = self.docs[label_id]
        total = docs["yes"] + docs["no"]
        if not total or not toks:
            return 0.0, []
        v = len(self.vocabulary[label_id]) or 1
        scores = {}
        pull: list[tuple[str, float]] = []
        per_side: dict[str, dict[str, float]] = {}
        for side in ("yes", "no"):
            denom = self.totals[label_id][side] + v
            per_side[side] = {t: math.log((self.counts[label_id][side].get(t, 0) + 1) / denom) for t in toks}
            scores[side] = math.log(docs[side] / total) + sum(per_side[side].values())
        m = max(scores.values())
        prob = math.exp(scores["yes"] - m) / (math.exp(scores["yes"] - m) + math.exp(scores["no"] - m))
        pull = sorted(((t, per_side["yes"][t] - per_side["no"][t]) for t in toks), key=lambda e: -e[1])
        return prob, [e for e in pull if e[1] > 0][:6]

    def suggest(self, toks: list[str], present: set[str] | None = None,
                threshold: float = MIN_PROBABILITY) -> list[Suggestion]:
        """The labels worth offering for a message that carries `present`, surest first."""
        out = []
        for label in self.labels:
            if present and label in present:
                continue
            prob, pull = self.probability(label, toks)
            if prob >= threshold:
                out.append(Suggestion(label, prob, tuple(t for t, _w in pull)))
        out.sort(key=lambda s: -s.probability)
        return out[:MAX_SUGGESTIONS]

    # ------------------------------------------------------------ storage

    def doc_rows(self) -> list[tuple[str, int, int]]:
        return [(label, docs["yes"], docs["no"]) for label, docs in self.docs.items()]

    def token_rows(self) -> list[tuple[str, str, int, int]]:
        out = []
        for label in self.counts:
            toks = set(self.counts[label]["yes"]) | set(self.counts[label]["no"])
            out += [(label, t, self.counts[label]["yes"].get(t, 0), self.counts[label]["no"].get(t, 0)) for t in toks]
        return out

    @classmethod
    def from_rows(cls, docs: list[tuple[str, int, int]], tokens: list[tuple[str, str, int, int]]) -> LabelModel:
        m = cls()
        for label, yes, no in docs:
            m.docs[label]["yes"] = yes
            m.docs[label]["no"] = no
        for label, t, yes, no in tokens:
            for side, n in (("yes", yes), ("no", no)):
                if n:
                    m.counts[label][side][t] = n
                    m.totals[label][side] += n
            m.vocabulary[label].add(t)
        return m
