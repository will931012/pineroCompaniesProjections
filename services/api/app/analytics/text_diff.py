"""Deterministic paragraph-level comparison of two versions of a filing section.

Paragraphs are compared after whitespace and quote normalisation. Within a run of replaced
paragraphs, each new paragraph is paired, in order, with the most similar remaining old
paragraph when their word overlap is at least PAIR_THRESHOLD; paired paragraphs are
"changed" and carry a word-level diff, the rest are "added" or "removed".
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

PAIR_THRESHOLD = 0.5
# Pairing is quadratic; larger rewrites are reported as removed + added.
MAX_PAIRING_BLOCK = 80

BlockKind = Literal["same", "added", "removed", "changed"]
WordOpKind = Literal["equal", "insert", "delete"]

_WHITESPACE = re.compile(r"\s+")
_TOKENS = re.compile(r"\S+\s*")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


@dataclass(frozen=True)
class WordOp:
    op: WordOpKind
    text: str


@dataclass(frozen=True)
class DiffBlock:
    kind: BlockKind
    before: str | None
    after: str | None
    words: list[WordOp] = field(default_factory=list)


@dataclass(frozen=True)
class DiffSummary:
    same: int
    added: int
    removed: int
    changed: int
    words_added: int
    words_removed: int


def _normalise(paragraph: str) -> str:
    return _WHITESPACE.sub(" ", paragraph.translate(_QUOTES)).strip()


def _words(paragraph: str) -> list[str]:
    return _normalise(paragraph).split(" ")


def similarity(before: str, after: str) -> float:
    matcher = SequenceMatcher(None, _words(before), _words(after), autojunk=False)
    return matcher.ratio() if matcher.quick_ratio() >= PAIR_THRESHOLD else 0.0


def word_diff(before: str, after: str) -> list[WordOp]:
    old = _TOKENS.findall(before)
    new = _TOKENS.findall(after)
    matcher = SequenceMatcher(
        None, [t.strip() for t in old], [t.strip() for t in new], autojunk=False
    )
    ops: list[WordOp] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append(WordOp("equal", "".join(new[j1:j2])))
            continue
        if i2 > i1:
            ops.append(WordOp("delete", "".join(old[i1:i2])))
        if j2 > j1:
            ops.append(WordOp("insert", "".join(new[j1:j2])))
    return ops


def _replace_block(before: list[str], after: list[str]) -> list[DiffBlock]:
    if len(before) * len(after) > MAX_PAIRING_BLOCK * MAX_PAIRING_BLOCK:
        return [DiffBlock("removed", p, None) for p in before] + [
            DiffBlock("added", None, p) for p in after
        ]
    blocks: list[DiffBlock] = []
    # Unpaired new paragraphs wait so removals at the same position are listed first.
    pending: list[DiffBlock] = []
    next_old = 0
    for new in after:
        scored = [(similarity(before[k], new), k) for k in range(next_old, len(before))]
        best = max(scored, default=(0.0, -1))
        if best[0] < PAIR_THRESHOLD:
            pending.append(DiffBlock("added", None, new))
            continue
        match = best[1]
        blocks.extend(DiffBlock("removed", p, None) for p in before[next_old:match])
        blocks.extend(pending)
        pending = []
        blocks.append(DiffBlock("changed", before[match], new, word_diff(before[match], new)))
        next_old = match + 1
    blocks.extend(DiffBlock("removed", p, None) for p in before[next_old:])
    blocks.extend(pending)
    return blocks


def diff_paragraphs(before: list[str], after: list[str]) -> list[DiffBlock]:
    matcher = SequenceMatcher(
        None, [_normalise(p) for p in before], [_normalise(p) for p in after], autojunk=False
    )
    blocks: list[DiffBlock] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            blocks.extend(
                DiffBlock("same", before[i], after[j])
                for i, j in zip(range(i1, i2), range(j1, j2), strict=True)
            )
        elif tag == "delete":
            blocks.extend(DiffBlock("removed", p, None) for p in before[i1:i2])
        elif tag == "insert":
            blocks.extend(DiffBlock("added", None, p) for p in after[j1:j2])
        else:
            blocks.extend(_replace_block(before[i1:i2], after[j1:j2]))
    return blocks


def summarise(blocks: list[DiffBlock]) -> DiffSummary:
    def count(kind: BlockKind) -> int:
        return sum(1 for b in blocks if b.kind == kind)

    words_added = sum(len(_words(b.after or "")) for b in blocks if b.kind == "added")
    words_removed = sum(len(_words(b.before or "")) for b in blocks if b.kind == "removed")
    for block in blocks:
        if block.kind == "changed":
            words_added += sum(len(op.text.split()) for op in block.words if op.op == "insert")
            words_removed += sum(len(op.text.split()) for op in block.words if op.op == "delete")
    return DiffSummary(
        count("same"), count("added"), count("removed"), count("changed"), words_added,
        words_removed,
    )  # fmt: skip
