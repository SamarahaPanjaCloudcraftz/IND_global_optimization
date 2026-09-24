# Backtest Optimization Engine — Design

2026-09-21 · @Samaraha

## The space

The parameter space is always a **sum of products**:

- A top-level **sum** of independent **axes**. Independent means optimizing one axis does not change the winning value of another.
- Each axis is a **product** grid of one or more parameters that interact and are tested as every combination.
- The leaves of a product are named value-sets: one parameter and its values to test.

Independent axes sit on top, interacting grids underneath, never the reverse. A single-parameter axis is just a product of one thing.

A **shared parameter** multiplied across several axes (e.g. leverage × (axisA + axisB)) is the only case where a product sits above a sum. It distributes back to a sum of products (leverage×axisA + leverage×axisB), so the space stays in canonical form.

**Invariant:** every parameter's value-set includes its baseline value. This makes "does this axis beat doing nothing?" an in-block comparison, and it is what forces the dedup pass in Job 1.

## Node types

One abstract base, two concrete kinds. A node is almost pure structure; the variable behaviour lives in strategy objects hanging off it.

- **Leaf** — a parameter name plus its set of values. It knows its size and how to list its values. It makes no choices and combines nothing.
- **Internal** — an ordered list of children (leaves or other internals) plus one **combiner strategy**, either `sum` or `product`. The node delegates everything to its combiner.

Sum and product are not separate classes; they are two combiner strategies plugged into the same `Internal` node. There is no third node type.

## Job 1 — the run plan

Produce the full set of backtest jobs, each a complete config. Two operations on the tree, then two passes outside it.

**`count`** — how many variants a subtree contributes. Uniformly recursive: leaf returns its size, product multiplies children's counts, sum adds them. Cheap, no enumeration.

**`generate`** — the list of *partial* configs a subtree produces (each names only the parameters it touches). Uniformly recursive: leaf emits one single-key dict per value; product cross-multiplies children's partials and merges each combination (keys are disjoint across product-siblings, so merges never collide); sum concatenates children's partial-lists. The root returns partials, because a sum branch stays silent about the other axes' parameters.

Two passes live outside the nodes, to keep nodes pure:

1. **Overlay** — there is one **baseline** (a complete config, one value per parameter) and one **standalone control** run (the origin). Complete each partial by overlaying it on the baseline: `full = {**baseline, **partial}`. This is the only place baseline enters and the only place unmentioned parameters get filled.
2. **Dedup** — because every axis includes its baseline value, the all-baseline config is emitted once per axis. Dedup on the completed configs collapses these to the single standalone control.

End to end: `generate → overlay → dedup → backtest jobs`.

## Job 2 — choosing winners

Once runs come back scored, pick the overall best config. The rule follows entirely from "sum-child = independent axis":

**Selection fires once per child of the sum.** Each child is one independent axis. Take that child's full scored variant-set, `select` the best one, then **compose** the per-axis winners into one config.

- A **leaf** axis: its variants are its values — pick the best value.
- A **product** axis: its variants are the full grid — pick the best *whole cell*. Never pick per-parameter inside a product; the parameters interact, so only complete combinations are meaningful candidates.

`select` is the only place domain judgment lives: scored variants in, one winner out. Default is argmax on the backtest metric; later swap in margin-over-control, robustness, etc., without touching anything structural.

Two things keep this well-defined:

- Every axis's candidate set includes the shared control, so "this axis doesn't beat doing nothing" is a legal outcome (its winner is the baseline value).
- The control was run once but its score feeds every axis's `select`.

**Confirmation run.** Composing winners gives a config that was (in general) never actually run — each axis winner was measured with the others at baseline. Do one run of the composed config. If its score matches what the per-axis results imply, the independence groupings held; if it drifts, two axes you called independent actually interact — learned for the cost of one run. This is both the final validation and the cheapest test of the independence assumption the sum-structure rests on.

**Routing.** Each generated config is tagged with its originating axis. After runs finish, group scored runs by that tag to feed `select`. The control is tagged standalone but included in every axis's candidate set.

## Design seams

Three knobs, for three kinds of future change:

- **Tree shape** — the hypothesis about what interacts (product) vs. what is independent (sum). Changing your mind about interactions is a change of shape, not code.
- **Combiner** — how children fold (sum / product). Rarely touched; sum and product are the whole algebra.
- **`select`** — what "best" means. Touched most (argmax → margin-over-control → robustness). Lives on the sum combiner, or hoisted to the driver since with one sum it barely recurses.

**The one asymmetry to hold onto:** `count` and `generate` recurse uniformly through the whole tree; `select` does **not**. It stops at the sum's children and treats each product as an opaque bag of variants (reusing `generate`), never recursing into it. That asymmetry *is* the semantics: products are chosen jointly, sums are chosen per-axis. If `select` ever recurses into a product, interacting parameters are being optimized separately — the core bug this design exists to prevent.
