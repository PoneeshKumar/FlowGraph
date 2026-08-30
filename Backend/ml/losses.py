"""
Loss functions for the GNN risk classifier.

Focal Loss is the whole reason this module exists. With fraud at ~1% of
accounts, plain cross-entropy lets the model coast: predicting "low risk" for
everything scores ~99% accuracy and catches nothing. Focal Loss reshapes the
objective so gradient concentrates on the rare, hard examples.
"""

import logging
from typing import Optional, Sequence, Union

import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)


class FocalLoss(nn.Module):
    r"""Multi-class Focal Loss (Lin et al., 2017).

        FL(p_t) = -alpha_t * (1 - p_t)^gamma * log(p_t)

    where p_t is the predicted probability of the true class.

    The (1 - p_t)^gamma term is the whole trick. An example already predicted
    correctly at p_t = 0.9 with gamma = 2 contributes (0.1)^2 = 0.01 of its
    normal loss — a 100x down-weighting. One the model is getting wrong at
    p_t = 0.1 keeps (0.9)^2 = 0.81. So the easy majority stops dominating the
    gradient and the rare fraud cases drive learning.

    Note on `alpha`, because CLAUDE.md's 0.25 needs a caveat:
    in the original paper alpha is a *binary* foreground/background weight.
    With four classes a single scalar multiplies every term equally, so it
    only rescales the loss and does not rebalance anything. Pass a per-class
    sequence to get real rebalancing — e.g. a low weight for `low` and higher
    weights for `high`/`critical`.

    Args:
        gamma:        Focusing strength. 0.0 reduces to weighted cross-entropy;
                      2.0 is the paper's default and CLAUDE.md's choice.
        alpha:        Scalar (uniform, rescales only) or one weight per class.
        reduction:    'mean' | 'sum' | 'none'
        ignore_index: Targets with this value contribute nothing. Use it to
                      drop unlabelled accounts — pass
                      y.masked_fill(~labelled_mask, ignore_index) so the model
                      is never taught that "nobody looked" means "safe".
    """

    def __init__(
        self,
        gamma: float = 2.0,
        alpha: Union[float, Sequence[float], torch.Tensor, None] = 0.25,
        reduction: str = "mean",
        ignore_index: int = -100,
    ) -> None:
        super().__init__()
        if gamma < 0:
            raise ValueError(f"gamma must be >= 0, got {gamma}")
        if reduction not in ("mean", "sum", "none"):
            raise ValueError(f"unknown reduction {reduction!r}")

        self.gamma = float(gamma)
        self.reduction = reduction
        self.ignore_index = ignore_index

        if alpha is None:
            self.register_buffer("alpha", None)
        elif isinstance(alpha, (int, float)):
            self.register_buffer("alpha", torch.tensor(float(alpha)))
        else:
            weights = torch.as_tensor(alpha, dtype=torch.float)
            if weights.ndim != 1:
                raise ValueError("per-class alpha must be 1-D")
            self.register_buffer("alpha", weights)

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """
        Args:
            logits: [N, C] raw scores — NOT softmaxed. log_softmax happens here,
                    which is both correct and numerically stabler than doing it
                    upstream.
            target: [N] int64 class indices.
        """
        if logits.ndim != 2:
            raise ValueError(f"expected logits of shape [N, C], got {tuple(logits.shape)}")
        if target.ndim != 1 or target.shape[0] != logits.shape[0]:
            raise ValueError(
                f"target shape {tuple(target.shape)} does not match "
                f"logits shape {tuple(logits.shape)}"
            )

        num_classes = logits.shape[1]
        keep = target != self.ignore_index
        if not bool(keep.any()):
            # Every node masked out. Return a real zero that still carries a
            # grad_fn, so an optimizer step over an all-unlabelled batch is a
            # no-op rather than a crash.
            return logits.sum() * 0.0

        logits = logits[keep]
        target = target[keep]

        if int(target.max()) >= num_classes or int(target.min()) < 0:
            raise ValueError(
                f"target values must be in [0, {num_classes - 1}]; got "
                f"[{int(target.min())}, {int(target.max())}]"
            )

        log_probs = F.log_softmax(logits, dim=-1)
        log_p_t = log_probs.gather(1, target.unsqueeze(1)).squeeze(1)
        p_t = log_p_t.exp()

        focal = (1.0 - p_t).pow(self.gamma)
        loss = -focal * log_p_t

        if self.alpha is not None:
            if self.alpha.ndim == 0:
                loss = loss * self.alpha
            else:
                if self.alpha.numel() != num_classes:
                    raise ValueError(
                        f"alpha has {self.alpha.numel()} weights but there are "
                        f"{num_classes} classes"
                    )
                loss = loss * self.alpha.to(loss.device)[target]

        if self.reduction == "mean":
            return loss.mean()
        if self.reduction == "sum":
            return loss.sum()
        return loss


def class_balanced_alpha(
    labels: torch.Tensor,
    num_classes: int,
    ignore_index: int = -100,
    beta: Optional[float] = None,
) -> torch.Tensor:
    """Per-class alpha weights derived from the observed label distribution.

    This is what makes alpha actually rebalance rather than merely rescale.
    Two schemes:

      beta=None (default) — inverse frequency, normalized to mean 1.0.
      beta in (0,1)       — "effective number of samples" reweighting
                            (Cui et al., 2019): (1-beta) / (1-beta^n). Gentler
                            than raw inverse frequency, which can over-weight a
                            class with only a handful of examples into
                            dominating every update.

    Classes absent from `labels` get weight 1.0 rather than infinity.
    """
    if num_classes < 1:
        raise ValueError("num_classes must be >= 1")

    valid = labels[labels != ignore_index]
    counts = torch.bincount(valid.to(torch.long), minlength=num_classes).float()
    counts = counts[:num_classes]

    present = counts > 0
    weights = torch.ones(num_classes, dtype=torch.float)

    if not bool(present.any()):
        return weights

    if beta is None:
        weights[present] = counts[present].sum() / counts[present]
    else:
        if not 0.0 < beta < 1.0:
            raise ValueError(f"beta must be in (0, 1), got {beta}")
        effective = (1.0 - beta ** counts[present]) / (1.0 - beta)
        weights[present] = 1.0 / effective

    # Normalize so the loss magnitude stays comparable to unweighted, which
    # keeps a learning rate tuned without weights roughly valid.
    weights = weights / weights[present].mean()
    return weights
