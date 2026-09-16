"""Render the GNN result charts used in the README.

Numbers come from ml/RESULTS.md (measured on the loaded HI-Small graph) and are
kept here as literals so the charts can be regenerated without the DB or the
trained checkpoints:

    python3 scripts/plot_results.py            # → docs/images/*.png

Design notes: horizontal bars (the labels are long), one hue per series assigned
in fixed order, values direct-labelled so identity is never colour-alone, and
recessive axes — no gridlines competing with the marks.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "images"

# Design-system tokens (Frontend/src/index.css), so the README matches the UI.
SURFACE = "#fafbfc"
INK, INK_2, INK_3, INK_4 = "#131c2b", "#3e4d66", "#71809a", "#a4b0c4"
# Categorical pair — validated: CVD ΔE 25.5 (deutan), normal-vision ΔE 28.9.
ACCENT, INDIGO = "#0d9d72", "#3b4bc0"

# (label, test PR-AUC) — the improvement chain, in the order the changes were made.
PROGRESSION = [
    ("baseline (log scaler, in-neighbours)", 0.057),
    ("+ bidirectional message passing", 0.082),
    ("+ quantile normalization", 0.282),
    ("+ structural features (k-core…)", 0.409),
    ("+ capacity (hidden 192)", 0.422),
    ("+ motif features", 0.460),
    ("+ mini-batch training", 0.650),
    ("+ capacity (hidden 256)", 0.664),
    ("+ 3 layers  (champion)", 0.724),
    ("3-seed ensemble", 0.735),
]

# (typology, champion recall %, ensemble recall %, reachable by cycle detection?)
TYPOLOGIES = [
    ("SCATTER-GATHER", 91.6, 94, False),
    ("GATHER-SCATTER", 91.1, 94, False),
    ("FAN-OUT", 75.5, 81, False),
    ("CYCLE", 52.4, 62, True),
    ("RANDOM", 50.7, 57, False),
    ("FAN-IN", 50.6, 59, False),
    ("STACK", 24.0, 28, False),
    ("BIPARTITE", 12.0, 14, False),
]


def _style(ax):
    """Recessive frame: no box, no gridlines, muted tick text."""
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="both", length=0, labelsize=9, colors=INK_2)
    ax.xaxis.set_visible(False)


def plot_progression(path: Path) -> None:
    labels = [lbl for lbl, _ in PROGRESSION]
    values = [v for _, v in PROGRESSION]
    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    y = range(len(values))
    # The champion and the ensemble are the point of the chart; the rest are the climb.
    colors = [INK_4] * (len(values) - 2) + [ACCENT, INDIGO]
    ax.barh(list(y), values, height=0.62, color=colors, zorder=2)
    for i, v in enumerate(values):
        ax.text(v + 0.012, i, f"{v:.3f}", va="center", ha="left",
                fontsize=9, color=INK, fontweight="semibold")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) * 1.16)
    _style(ax)
    ax.set_title("Test PR-AUC on held-out (temporal) accounts", fontsize=12,
                 color=INK, fontweight="semibold", loc="left", pad=24)
    ax.text(0, 1.035, "IBM AML HI-Small · 0.32% test prevalence · every gain from a diagnosed cause",
            transform=ax.transAxes, fontsize=8.5, color=INK_3, va="bottom")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {path}")


def plot_typology_recall(path: Path) -> None:
    names = [t[0] for t in TYPOLOGIES]
    champ = [t[1] for t in TYPOLOGIES]
    ens = [t[2] for t in TYPOLOGIES]
    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    h = 0.36
    y = range(len(names))
    top = [i - h / 2 - 0.012 for i in y]     # 2px-equivalent gap between the pair
    bottom = [i + h / 2 + 0.012 for i in y]
    ax.barh(top, champ, height=h, color=ACCENT, zorder=2, label="Champion (v10_L3)")
    ax.barh(bottom, ens, height=h, color=INDIGO, zorder=2, label="3-seed ensemble")
    for ys, vals in ((top, champ), (bottom, ens)):
        for yy, v in zip(ys, vals):
            ax.text(v + 1.2, yy, f"{v:.0f}%", va="center", ha="left", fontsize=8.5, color=INK)
    ax.set_yticks(list(y))
    # Mark what cycle detection could reach at all — the point of the GNN.
    ax.set_yticklabels([f"{n}  ·  loop-free" if not reach else f"{n}  ·  cycles"
                        for n, _, _, reach in TYPOLOGIES])
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    _style(ax)
    ax.set_title("Recall by laundering typology (whole graph, F1 threshold)", fontsize=12,
                 color=INK, fontweight="semibold", loc="left", pad=24)
    ax.text(0, 1.035, "The detectors find 3.9% overall and cannot represent any loop-free pattern at any depth",
            transform=ax.transAxes, fontsize=8.5, color=INK_3, va="bottom")
    leg = ax.legend(loc="lower right", frameon=False, fontsize=9, handlelength=1.1, handleheight=0.9)
    for text in leg.get_texts():
        text.set_color(INK_2)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    plot_progression(args.out / "pr-auc-progression.png")
    plot_typology_recall(args.out / "recall-by-typology.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
