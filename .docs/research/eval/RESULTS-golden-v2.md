# Golden-v2 results

48 queries over this repo. The original 8 fixtures from
`code-harness.fixture.yaml` sit at the top of
`code-harness-v2.fixture.yaml` unchanged. All runs used local MiniLM +
Chroma + the default cross-encoder, `--no-query-cache`, no LLM keys.

Suite path: `.docs/research/eval/code-harness-v2.fixture.yaml`. k=10.
Generated 2026-10-23. Each pending branch was scored from a temp
worktree; only the golden file was copied in. Nothing was committed on
those branches.

## Set composition

| Category | n | What it asks |
|----------|---|--------------|
| symbol | 7 | Bare identifier (`QueryLoop`, `redact_text`, …) |
| nl | 10 | “How does X work?” (includes the original context/parser/chunker/RRF rows) |
| cross-file | 6 | Index pipeline, retrieve→rerank→pack, eval scoring, loop grade, serve+redact, memory extract |
| cli | 5 | `cmd_*` / default config (includes original `q-cli-query`) |
| docs | 4 | README quick start, eval recipe, `.env.example`, LICENSE |
| hard | 9 | Near-miss traps (HyDE-is-not-LLM, TurboVec-is-not-default, no LangGraph, …) plus the original who-calls / exposes rows |
| ticket | 7 | Jira-shaped bodies (`ABC-101` … `ABC-107`) |

CLI / docs / hard are extra mix; the merge table below also splits
symbol / NL / cross-file / ticket.

Labels are paths I actually opened. Docs questions gold the doc that
answers them, not a fusion roadmap note. Several golds are entity ids
(`func:` / `class:` / `method:`). Path-only citation is easier than
entity-id recall — that gap shows up in the ticket row.

## Noise band

`main` 7881963, same index, three back-to-back evals:

| Metric | mean | spread (max−min) | raw |
|--------|------|------------------|-----|
| Recall@10 | 0.3351 | **0** | 0.3351, 0.3351, 0.3351 |
| nDCG@10 | 0.3101 | **0** | 0.3101, 0.3101, 0.3101 |
| citation-path | 0.6083 | **0** | 0.6083, 0.6083, 0.6083 |
| packed tokens | 1751.1 | **6.2** | 1750.1, 1748.5, 1754.7 |

Ranking on the default stack is deterministic given a fixed Chroma
snapshot. Tokens move by a few counts (packer / tie order). Treat a
Recall/nDCG claim smaller than **0.01** as noise on this stack, and
treat anything under **~0.03** as noise once context-expansion (R5) is
on — that arm is the one that actually jittered (see quick-wins).

Why ranking can move at all: no Python `random` in the default
retrieve path. Residual risk is Chroma HNSW, CE float ties, and
set/dict walk order in neighbor expansion. Query cache is off by
default on eval.

What I changed on this branch (cheap, default-off behavior preserved):

- `seed_eval_determinism()` at the start of `run_eval` (stdlib random,
  NumPy, Torch). `PYTHONHASHSEED` is set only if unset; it cannot
  rehash a running interpreter.
- Stable sort key `(-score, chunk.id)` on retriever fusion / CE / BM25
  / wiki / graph and on the packer lexical boost.

That does **not** make R5 deterministic on the quick-wins branch —
those worktrees ran their own harness without this patch.

The original 8, scored as a subset of v2 on this same main index:
Recall@10 **0.3125**, nDCG **0.263**, cite **0.50**. The committed
8-query A/B table (`RESULTS.md`) still says chromadb 0.2500 / 0.2246 /
0.50 on an older 3060-chunk snapshot. Different index, same 8 labels.
n=8 is still too small to steer a merge; the 48-query mean is the one
to use.

## Main category breakdown

| Category | n | Recall@10 | nDCG@10 | citation-path |
|----------|---|-----------|---------|---------------|
| symbol | 7 | 1.000 | 1.000 | 1.000 |
| nl | 10 | 0.300 | 0.254 | 0.800 |
| cross-file | 6 | 0.264 | 0.193 | 0.533 |
| ticket | 7 | 0.214 | 0.198 | 0.857 |
| cli | 5 | 0.000 | 0.000 | 0.000 |
| docs | 4 | 0.375 | 0.403 | 0.500 |
| hard | 9 | 0.167 | 0.132 | 0.333 |

Bare symbol lookup is solved. Ticket *paths* usually land in the pack
(cite 0.86) but the entity-id gold often does not (recall 0.21). Every
`cmd_*` / “where is the X CLI defined in main.py” query is a miss —
`main.py` is one huge file with many near-identical command handlers.
That is a real retrieve failure, not a bad label.

## Tables (mean ± spread)

Compare a branch to **its own baseline**, not to main, when the branch
indexes extra files. Ablation/validity/loop-stall add modules, so their
“full” mean sits a few thousandths away from main even when retrieve
logic is unchanged.

### main 7881963 (baseline)

| Metric | mean ± spread |
|--------|----------------|
| Recall@10 | 0.3351 ± 0 |
| nDCG@10 | 0.3101 ± 0 |
| citation-path | 0.6083 ± 0 |
| tokens | 1751.1 ± 6.2 |

### feature/harness-ablation-eval (2×)

| Arm | Recall@10 | nDCG@10 | citation-path | tokens |
|-----|-----------|---------|---------------|--------|
| full | 0.3281 ± 0 | 0.3010 ± 0 | 0.6188 ± 0 | 1827.0 ± 0 |
| no_bm25 | 0.4184 ± 0 | 0.3799 ± 0 | 0.7062 ± 0 | 1970.1 ± 0 |
| no_rerank | 0.4184 ± 0 | 0.3473 ± 0 | 0.6854 ± 0 | 1890.5 ± 0 |
| no_dense | 0.2517 ± 0 | 0.2350 ± 0 | 0.5250 ± 0 | 1589.8 ± 0 |

Turning BM25 **off** or the cross-encoder **off** each gains **+0.090**
recall vs full. Turning dense off loses **−0.076**. On this set the CE
is a net dropper (`rerank_drop` is the common failure label). Dense is
the stage that is actually carrying.

no_rerank vs full, by category (one run each):

| Category | full R | no_rerank R |
|----------|--------|-------------|
| symbol | 1.000 | 1.000 |
| nl | 0.300 | 0.450 |
| cross-file | 0.208 | 0.264 |
| ticket | 0.214 | 0.500 |

### feature/harness-validity-check (3×)

| Metric | mean ± spread |
|--------|----------------|
| Recall@10 | 0.3385 ± 0 |
| nDCG@10 | 0.2992 ± 0 |
| citation-path | 0.6188 ± 0 |
| tokens | 1827.2 ± 4.3 |

Default eval does not enable `--validity`. The +0.003 vs main is extra
indexed files on the stacked branch, not the validity gate.

### feature/harness-loop-stall (3×)

| Metric | mean ± spread |
|--------|----------------|
| Recall@10 | 0.3281 ± 0 |
| nDCG@10 | 0.2964 ± 0 |
| citation-path | 0.6188 ± 0 |
| tokens | 1899.2 ± 12.7 |

Matches ablation `full` (this branch stacks on it). One-shot
`max_loops=0` does not exercise stall. Token spread is the usual packer
jitter, larger than main because the tree is fatter.

### feature/retrieval-quick-wins (3× per arm)

Fair baseline is **this branch with every knob off**, not main.

| Arm | Recall@10 | nDCG@10 | citation-path | tokens |
|-----|-----------|---------|---------------|--------|
| baseline-off | 0.3073 ± 0 | 0.2758 ± 0 | 0.6083 ± 0 | 1655.2 ± 11.5 |
| R1 enrich-index-text | 0.3351 ± 0 | 0.2976 ± 0 | 0.6604 ± 0 | 1984.8 ± 2.7 |
| R2 per-file-cap=3 | 0.3073 ± 0 | 0.2758 ± 0 | 0.6083 ± 0 | 1556.4 ± 0.8 |
| R3 multi-query | 0.3177 ± 0 | 0.2822 ± 0 | 0.5979 ± 0 | 1740.5 ± 1.0 |
| R5 context-expansion | 0.3819 ± 0.0104 | 0.3528 ± 0.0154 | 0.6354 ± 0 | 1218.9 ± 26.2 |
| shipped R2+R3+R5 | 0.3715 ± 0.0312 | 0.3456 ± 0.0263 | 0.6354 ± 0 | 896.8 ± 20.6 |

R1 needs a re-index. R5 and shipped are the noisy arms.

R5 vs baseline-off by category (one run):

| Category | off R | R5 R | off cite | R5 cite |
|----------|-------|------|----------|---------|
| symbol | 1.000 | 1.000 | 1.000 | 1.000 |
| nl | 0.250 | 0.250 | 0.800 | 0.600 |
| cross-file | 0.208 | 0.083 | 0.533 | 0.250 |
| ticket | 0.143 | 0.714 | 0.857 | 0.857 |

R5’s headline recall is almost entirely the ticket subset. Cross-file
and NL citation get worse. Shipped looks the same: tickets up,
cross-file down, plus a 0.03 recall swing across repeats.

### feature/external-context-x0-x1

This branch already has R2+R3+R5 on, so it inherits shipped noise.
Code-only = the full 48. Ticket subset = the 7 `ABC-*` rows.

| Arm | n | Recall@10 | nDCG@10 | citation-path | tokens |
|-----|---|-----------|---------|---------------|--------|
| external off (code-only) | 48 | 0.3681 ± 0.0312 | 0.3405 ± 0.0263 | 0.6562 ± 0 | 913.3 ± 9.6 |
| external on (code-only) | 48 | 0.3646 ± 0.0312 | 0.3387 ± 0.0263 | 0.6354 ± 0 | 855.0 ± 10.9 |
| ticket off | 7 | 0.7143 ± 0 | 0.5383 ± 0 | 1.000 ± 0 | 1273.7 ± 23.9 |
| ticket on | 7 | 0.7143 ± 0 | 0.5383 ± 0 | 0.8571 ± 0 | 894.7 ± 92.8 |

On/off recall delta on code-only is **inside the 0.03 noise band**.
Citation drops when the sidecar is on (0.656 → 0.635 code-only;
1.000 → 0.857 tickets). The ingested `tests/fixtures/external/tickets`
files did not lift the ticket-style code queries; they crowded a cited
path.

## Verdicts

| Branch / arm | Verdict | Why |
|--------------|---------|-----|
| main 7881963 | baseline | Deterministic ranking; use 0.3351 / 0.3101 / 0.6083 / 1751 as the v2 anchor. |
| feature/harness-ablation-eval | **merge** | Eval-only. Default retrieve is unchanged. The leave-one-out table is the first honest signal we have that CE and BM25 *hurt* this set. Do not flip production defaults off the back of n=48, but land the harness. |
| feature/harness-validity-check | **merge** | Opt-in (`--validity` / `CODEHARNESS_VERIFY_VALIDITY`). Default numbers match a slightly fatter tree, not a retrieve change. |
| feature/harness-loop-stall | **merge** | Stall math only runs when the loop runs; default is still one-shot. Ranking matches ablation `full`. |
| quick-wins R1 | **don't merge as default** | +0.028 recall, +0.052 cite, +330 tokens, mandatory re-index. Real, deterministic, and not worth making the default embed text longer. Keep the flag. |
| quick-wins R2 | **merge only as a packer cap** | Recall/nDCG/cite identical to off (gain is **inside the noise band** — it is zero). Tokens 1655 → 1556. Fine as a context budget knob. Do not advertise a retrieve win. |
| quick-wins R3 | **don't merge as default** | +0.010 recall, cite 0.608 → 0.598. That +1 pt is the noise floor I set above, and citation moved the wrong way. |
| quick-wins R5 | **needs work** | Mean +0.075 recall vs off, outside the 0.01 band, but the gain is ticket-only and cross-file/NL citation regress. Also the first arm that is not repeat-stable (0.010 recall spread). Do not ship until expansion is seeded/tied and the cross-file hit is not collateral. |
| quick-wins shipped R2+R3+R5 | **don't merge** | Mean +0.064 vs off, but spread **0.031** (raw 0.354 / 0.375 / 0.385). Tokens look great (~897) because expansion+cap starve the pack. Same ticket-vs-cross-file trade-off as R5 alone, noisier. The current defaults on that branch should not land. |
| external-context X0 off vs on (code-only) | **don't merge** | Recall delta (−0.0035) is **inside the noise band**. Cite gets worse. External RRF on a code-only suite is dead weight. |
| external-context ticket subset | **don't merge** | Recall tied at 0.714. Cite 1.00 → 0.86 when on. The sidecar did not resolve `ABC-*` to the labeled code files; it displaced a packed path. |

## What surprised me

1. **Cross-encoder is a liability on this set.** `no_rerank` and
   `no_bm25` both jump to 0.418. The old 8-query table could not have
   shown that cleanly.
2. **R5 looks like a retrieve win until you split categories.** Ticket
   recall 0.14 → 0.71; cross-file 0.21 → 0.08. A single mean would have
   blessed the wrong default.
3. **CLI queries are a complete miss** (5/5 at 0). Anything that only
   says “where is `cmd_eval` in main.py” loses to the rest of `main.py`.
4. **Default ranking is already deterministic** without the seed patch.
   The seed + stable ties are insurance. The live jitter is
   context-expansion on the quick-wins branch, which this branch does
   not contain.
5. Packed-token means are not comparable across branches with different
   trees. Use within-branch deltas.

## How to reproduce

```bash
python3 main.py index .
python3 main.py eval . --suite .docs/research/eval/code-harness-v2.fixture.yaml --no-query-cache
```

Pending-branch reruns: copy this YAML into a temp worktree, index that
tree, then eval. Quick-wins arms use `--no-multi-query`,
`--no-context-expansion`, `--per-file-chunk-cap`, `--enrich-index-text`
as in that branch’s README. Ablation arms used
`harness.eval_ablate.apply_ablation` for `full` / `no_bm25` /
`no_rerank` / `no_dense` only (the other five `--ablate` columns were
not re-run). External used `external ingest` + `external link` on
`tests/fixtures/external/tickets`, then `--external`.
