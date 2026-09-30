"use client";

import { useState } from "react";
import type { DiffBlock, SectionDiff } from "@/lib/api/endpoints";
import { formatDate, formatInteger } from "@/lib/format";

type Run = { kind: "same"; blocks: DiffBlock[] } | { kind: "change"; block: DiffBlock };

/** Group consecutive unchanged paragraphs so the changes stand out. */
export function diffRuns(blocks: DiffBlock[]): Run[] {
  const runs: Run[] = [];
  for (const block of blocks) {
    const last = runs[runs.length - 1];
    if (block.kind === "same") {
      if (last?.kind === "same") last.blocks.push(block);
      else runs.push({ kind: "same", blocks: [block] });
    } else {
      runs.push({ kind: "change", block });
    }
  }
  return runs;
}

function Unchanged({ blocks }: { blocks: DiffBlock[] }) {
  const [open, setOpen] = useState(false);
  if (open || blocks.length <= 1) {
    return <>{blocks.map((b, i) => <p className="diff-same" key={i}>{b.after}</p>)}</>;
  }
  return (
    <button className="diff-collapsed" onClick={() => setOpen(true)} type="button">
      {formatInteger(blocks.length)} unchanged paragraphs — show
    </button>
  );
}

function Change({ block }: { block: DiffBlock }) {
  if (block.kind === "added") {
    return <p className="diff-added"><span className="diff-tag">Added</span>{block.after}</p>;
  }
  if (block.kind === "removed") {
    return <p className="diff-removed"><span className="diff-tag">Removed</span><del>{block.before}</del></p>;
  }
  return (
    <p className="diff-changed">
      <span className="diff-tag">Changed</span>
      {block.words.map((word, index) =>
        word.op === "insert" ? <ins key={index}>{word.text}</ins>
          : word.op === "delete" ? <del key={index}>{word.text}</del>
            : <span key={index}>{word.text}</span>)}
    </p>
  );
}

export function SectionDiffView({ diff }: { diff: SectionDiff }) {
  if (!diff.comparable || !diff.summary) {
    return (
      <p className="panel-copy">
        The {diff.previous.form} filed {formatDate(diff.previous.filed_date)} has no section matching “{diff.title}”,
        so there is nothing to compare.
      </p>
    );
  }
  const { summary } = diff;
  return (
    <div className="section-diff">
      <p className="diff-summary">
        Compared with the {diff.previous.form} filed {formatDate(diff.previous.filed_date)}:{" "}
        <b className="diff-count-added">{summary.added} added</b> ·{" "}
        <b className="diff-count-removed">{summary.removed} removed</b> ·{" "}
        <b className="diff-count-changed">{summary.changed} changed</b> · {summary.same} unchanged paragraphs
        ({formatInteger(summary.words_added)} words added, {formatInteger(summary.words_removed)} removed)
      </p>
      {summary.added + summary.removed + summary.changed === 0 && (
        <p className="panel-copy">The section text is identical apart from whitespace.</p>
      )}
      {diffRuns(diff.blocks).map((run, index) =>
        run.kind === "same" ? <Unchanged blocks={run.blocks} key={index} /> : <Change block={run.block} key={index} />)}
    </div>
  );
}
