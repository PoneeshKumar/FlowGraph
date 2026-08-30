"""
Neighbour-sampling mini-batch machinery for training the GNN.

WHY THIS EXISTS
---------------
Full-batch training computes one embedding for the whole graph and takes ONE
optimizer step per epoch. On this graph that badly under-trains the model: every
champion peaked at its epoch cap, and switching to mini-batches took test PR-AUC
from 0.46 to 0.60 — the single biggest gain measured, and it needs no new
features.

Two things full-batch cannot do, both here:

  1. Hundreds of gradient steps per epoch. Each batch samples a few hundred seed
     nodes, gathers a bounded k-hop neighbourhood, and does one step — so an
     epoch is `steps_per_epoch` updates instead of one.
  2. Class-balanced batches. At ~0.7% prevalence a random batch of 512 holds ~3
     fraud seeds; `pos_frac` oversamples the minority so every step actually sees
     fraud. This is where most of the gain comes from.

No `pyg-lib` / `torch-sparse`: the sampler is vectorized numpy over CSR adjacency,
deliberately, so it installs nowhere. Sampling is with replacement (duplicate
edges just up-weight a message slightly) which keeps it branch-free and fast.

The samples are the message-passing edges (the standard GraphSAGE computation
graph): for a 2-layer model, sampling two hops of in- and out-neighbours gives
each seed a correct-in-expectation receptive field. Evaluation stays full-graph,
because SAGEConv is inductive and the whole graph fits in memory for a forward
pass.
"""

import logging
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)

# CSR adjacency: (indptr [n+1], neighbours [num_edges]).
CSR = Tuple[np.ndarray, np.ndarray]


def build_adjacency(edge_index: np.ndarray, num_nodes: int) -> Tuple[CSR, CSR]:
    """Out- and in-neighbour CSR lists from a [2, E] directed edge index."""
    src = np.ascontiguousarray(edge_index[0])
    dst = np.ascontiguousarray(edge_index[1])

    order_out = np.argsort(src, kind="stable")
    out_neigh = dst[order_out].astype(np.int64)
    out_indptr = np.zeros(num_nodes + 1, dtype=np.int64)
    np.add.at(out_indptr, src + 1, 1)
    out_indptr = np.cumsum(out_indptr)

    order_in = np.argsort(dst, kind="stable")
    in_neigh = src[order_in].astype(np.int64)
    in_indptr = np.zeros(num_nodes + 1, dtype=np.int64)
    np.add.at(in_indptr, dst + 1, 1)
    in_indptr = np.cumsum(in_indptr)

    return (out_indptr, out_neigh), (in_indptr, in_neigh)


def _sample_one_direction(
    frontier: np.ndarray, csr: CSR, k: int, rng: np.random.Generator
) -> Tuple[np.ndarray, np.ndarray]:
    """Sample <=k neighbours (with replacement) of each frontier node.

    Returns the frontier node ids (repeated) and their sampled neighbours,
    aligned. Nodes with no neighbours in this direction drop out.
    """
    indptr, neigh = csr
    deg = indptr[frontier + 1] - indptr[frontier]
    live = deg > 0
    if not live.any():
        return np.empty(0, np.int64), np.empty(0, np.int64)
    f = frontier[live]
    d = deg[live]
    start = indptr[f]
    # random offset in [0, deg) for each of k draws per node
    offsets = (rng.random(len(f) * k) * np.repeat(d, k)).astype(np.int64)
    sampled = neigh[np.repeat(start, k) + offsets]
    return np.repeat(f, k), sampled


def sample_subgraph(
    seeds: np.ndarray,
    adj_out: CSR,
    adj_in: CSR,
    num_nodes: int,
    k: int = 10,
    hops: int = 2,
    rng: np.random.Generator = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a bounded k-hop subgraph around `seeds`.

    Returns:
        nodes:      global ids present in the subgraph (seeds first via unique).
        edge_index: [2, E] edges relabelled to local (0..len(nodes)-1) indices,
                    both directions represented.
        seed_local: local indices of the seed nodes, aligned with `seeds`.
    """
    if rng is None:
        rng = np.random.default_rng()
    e_src, e_dst = [], []
    frontier = np.asarray(seeds, dtype=np.int64)
    for _ in range(hops):
        of, on = _sample_one_direction(frontier, adj_out, k, rng)  # frontier -> neigh
        inf, inn = _sample_one_direction(frontier, adj_in, k, rng)  # neigh -> frontier
        if len(of):
            e_src.append(of); e_dst.append(on)
        if len(inf):
            e_src.append(inn); e_dst.append(inf)
        touched = [a for a in (on, inn) if len(a)]
        frontier = np.unique(np.concatenate([frontier] + touched)) if touched else frontier

    if e_src:
        es = np.concatenate(e_src)
        ed = np.concatenate(e_dst)
    else:
        es = ed = np.empty(0, np.int64)

    nodes = np.unique(np.concatenate([np.asarray(seeds, np.int64), es, ed]))
    remap = np.full(num_nodes, -1, dtype=np.int64)
    remap[nodes] = np.arange(len(nodes))
    edge_index = np.vstack([remap[es], remap[ed]]) if len(es) else np.zeros((2, 0), np.int64)
    return nodes, edge_index, remap[np.asarray(seeds, np.int64)]


def balanced_seed_batch(
    train_pos: np.ndarray,
    train_neg: np.ndarray,
    batch_size: int,
    pos_frac: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """A batch of seed nodes with `pos_frac` of them positive (oversampled with
    replacement, since positives are scarce)."""
    n_pos = max(1, int(round(batch_size * pos_frac)))
    n_neg = batch_size - n_pos
    pos = rng.choice(train_pos, n_pos, replace=True)
    neg = rng.choice(train_neg, n_neg, replace=n_neg > len(train_neg))
    return np.concatenate([pos, neg])
