"""Small deterministic statistical candidates; no archive, broker or network access.

Values supplied to ``fit_candidate`` are training-only arrays. Labels have
explicit masks and knowledge times; unknown finite placeholders never enter a
fit. Exact money, order selection and subsequent execution belong to the runner.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

FEATURE_NAMES = (
    "market_logit",
    "spread",
    "mid_change_1h",
    "mid_change_3h",
    "observed_panel_mid_mass",
    "panel_quote_fraction",
    "bracket_rank",
    "season_sin",
    "season_cos",
    "series_KXHIGHCHI",
    "series_KXHIGHDEN",
    "series_KXHIGHLAX",
    "series_KXHIGHMIA",
    "series_KXHIGHNY",
    "series_KXHIGHPHIL",
)
CANDIDATE_NAMES = ("cash", "midpoint", "logistic_offset", "ridge_net_return")
ARTIFACT_VERSION = 1
SCALE_FLOOR = 1e-12


def _features(values, feature_names):
    names = tuple(feature_names)
    if names != FEATURE_NAMES:
        raise ValueError("Feature names/order differ from the fixed candidate schema")
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 2 or result.shape[1] != len(names):
        raise ValueError("X must have the exact two-dimensional feature schema")
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite input feature; missing vectors must be excluded explicitly")
    return result


def _mask(values, shape):
    result = np.asarray(values)
    if result.shape != shape or result.dtype.kind != "b":
        raise ValueError("Known mask must be an explicit boolean array with the label shape")
    return result


def event_balanced_weights(source_days, event_ids, known_mask=None):
    """Each known day totals one; events, contracts and known heads share it.

    One-dimensional masks produce row weights. For a two-head mask the result
    has the same shape, with each contract's weight divided over its known heads.
    Unknown rows/heads receive zero. Event IDs may repeat across contracts but
    cannot refer to multiple source days. No target value enters these weights.
    """
    days, events = tuple(source_days), tuple(event_ids)
    if len(days) != len(events) or not days:
        raise ValueError("Nonempty equal-length day/event vectors are required")
    if any(not isinstance(v, str) or not v for v in days + events):
        raise ValueError("Day and event identities must be nonempty strings")
    mask = np.ones(len(days), dtype=bool) if known_mask is None else np.asarray(known_mask)
    if mask.dtype.kind != "b" or mask.ndim not in (1, 2) or mask.shape[0] != len(days):
        raise ValueError("Weight mask must have one row per event observation")
    if mask.ndim == 2 and mask.shape[1] != 2:
        raise ValueError("Only the declared YES/NO return heads are supported")
    active = mask if mask.ndim == 1 else mask.any(axis=1)
    if not active.any():
        raise ValueError("No known rows available for event-balanced weights")
    event_days = {}
    for day, event in zip(days, events, strict=True):
        if event in event_days and event_days[event] != day:
            raise ValueError("An event belongs to conflicting source days")
        event_days[event] = day
    grouped = {}
    for i, (day, event) in enumerate(zip(days, events, strict=True)):
        if active[i]:
            grouped.setdefault(day, {}).setdefault(event, []).append(i)
    result = np.zeros(mask.shape, dtype=np.float64)
    for day in sorted(grouped):
        day_events = grouped[day]
        for event in sorted(day_events):
            rows = day_events[event]
            contract_weight = 1 / (len(day_events) * len(rows))
            for i in rows:
                if mask.ndim == 1:
                    result[i] = contract_weight
                else:
                    result[i, mask[i]] = contract_weight / mask[i].sum()
    return result


def _training_arrays(name, X, y, weights, label_known_ts, fit_cutoff_ts, known_mask):
    heads = 1 if name == "logistic_offset" else 2
    shape = (len(X),) if heads == 1 else (len(X), heads)
    if len(X) < 2:
        raise ValueError("A fitted candidate requires at least two training rows")
    if known_mask is None:
        if heads == 2:
            raise ValueError("Return labels require an explicit known mask")
        known = np.ones(shape, dtype=bool)
    else:
        known = _mask(known_mask, shape)
    times = np.asarray(label_known_ts, dtype=np.float64)
    if times.shape != shape:
        raise ValueError("Label knowledge times must have the label shape")
    if isinstance(fit_cutoff_ts, bool) or fit_cutoff_ts is None or not np.isfinite(fit_cutoff_ts):
        raise ValueError("A finite fit cutoff is required")
    # Gate knowledge before reading label values or fitting any feature transform.
    if not np.isfinite(times[known]).all() or (times[known] >= fit_cutoff_ts).any():
        raise ValueError("Known training label is missing a time or is not strictly before fit cutoff")
    values = np.asarray(y, dtype=np.float64)
    if values.shape != shape or not np.isfinite(values).all():
        raise ValueError("Labels must be finite with explicit masks for unknown placeholders")
    w = np.asarray(weights, dtype=np.float64)
    if heads == 2 and w.shape == (len(X),):
        w = np.repeat(w[:, None] / 2, heads, axis=1)
    if w.shape != shape or not np.isfinite(w).all() or (w < 0).any():
        raise ValueError("Weights must be finite, nonnegative and match rows or label heads")
    w = np.where(known, w, 0.0)
    if heads == 1:
        values, w, times, known = (v[:, None] for v in (values, w, times, known))
    for head in range(heads):
        used = w[:, head] > 0
        if used.sum() < 2 or not np.isfinite(w[used, head].sum()):
            raise ValueError("Each head needs at least two known positive-weight rows and finite weight")
        if name == "logistic_offset" and set(values[used, head]) != {0.0, 1.0}:
            raise ValueError("Settlement calibration requires both binary classes")
    return values, w, times


def _scaler(X, row_weights):
    used = row_weights > 0
    # Ignored rows are removed before arithmetic, including multiplication by zero.
    x, w = X[used], row_weights[used]
    total = w.sum()
    if not np.isfinite(total) or total <= 0:
        raise ValueError("Degenerate training weight")
    with np.errstate(over="raise", invalid="raise", divide="raise"):
        try:
            center = (x * (w / total)[:, None]).sum(axis=0)
            variance = (((x - center) ** 2) * (w / total)[:, None]).sum(axis=0)
            scale = np.sqrt(variance)
        except FloatingPointError as exc:
            raise ValueError("Training scale overflow") from exc
    scale = np.where(scale <= SCALE_FLOOR, 1.0, scale)
    if not np.isfinite(center).all() or not np.isfinite(scale).all():
        raise ValueError("Nonfinite training transform")
    return center, scale


def _design(X, center, scale):
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        result = np.column_stack((np.ones(len(X)), (X - center) / scale))
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite standardized features")
    return result


def _logistic_objective(beta, z, offset, target, weight, penalty_diagonal):
    logits = offset + z @ beta
    loss = np.sum(weight * (np.logaddexp(0, logits) - target * logits))
    loss += 0.5 * np.sum(penalty_diagonal * beta**2)
    gradient = z.T @ (weight * (expit(logits) - target)) + penalty_diagonal * beta
    return loss, gradient


def fit_candidate(
    name,
    X,
    y=None,
    *,
    weights=None,
    label_known_ts=None,
    fit_cutoff_ts=None,
    known_mask=None,
    feature_names=FEATURE_NAMES,
    penalty=1.0,
):
    """Return a JSON-serializable fitted artifact or a stateless baseline.

    No source data are loaded here. Fitted models require labels strictly known
    before ``fit_cutoff_ts``. Return targets are (n,2), columns YES then NO;
    unknown labels use an explicit false mask and a finite placeholder. Weights
    may be (n,) or (n,2); row weights are divided equally between return heads.
    The registered first batch fixes penalty1; changing it requires a new design.
    """
    if name not in CANDIDATE_NAMES:
        raise ValueError("Unknown statistical candidate")
    x = _features(X, feature_names)
    if isinstance(penalty, bool) or not np.isfinite(penalty) or penalty <= 0:
        raise ValueError("A finite positive ridge penalty is required")
    artifact = {
        "schema_version": ARTIFACT_VERSION,
        "candidate": name,
        "feature_names": list(feature_names),
        "penalty": float(penalty),
        "prediction_kind": "cash" if name == "cash" else "probability",
        "fitted": False,
    }
    if name in ("cash", "midpoint"):
        if any(v is not None for v in (y, weights, label_known_ts, fit_cutoff_ts, known_mask)):
            raise ValueError("Stateless baselines must not receive training outcomes")
        return artifact
    values, w, times = _training_arrays(name, x, y, weights, label_known_ts, fit_cutoff_ts, known_mask)
    row_weights = w.sum(axis=1)
    center, scale = _scaler(x, row_weights)
    coefficients, diagnostics = [], []
    penalty_diagonal = np.ones(x.shape[1] + 1) * penalty
    penalty_diagonal[0] = 0
    for head in range(w.shape[1]):
        used = w[:, head] > 0
        z = _design(x[used], center, scale)
        target, weight = values[used, head], w[used, head]
        if name == "logistic_offset":
            offset = x[used, 0]
            solution = minimize(
                _logistic_objective,
                np.zeros(z.shape[1]),
                args=(z, offset, target, weight, penalty_diagonal),
                method="L-BFGS-B",
                jac=True,
                options={"maxiter": 200, "ftol": 1e-12, "gtol": 1e-8},
            )
            if not solution.success or not np.isfinite(solution.x).all() or not np.isfinite(solution.fun):
                raise ValueError("Logistic calibration failed to converge: " + str(solution.message))
            beta = solution.x
            diagnostic = {
                "solver": "L-BFGS-B",
                "converged": True,
                "iterations": int(solution.nit),
                "gradient_max_abs": float(np.max(np.abs(solution.jac))),
            }
        else:
            with np.errstate(over="raise", invalid="raise"):
                try:
                    gram = z.T @ (weight[:, None] * z) + np.diag(penalty_diagonal)
                    rhs = z.T @ (weight * target)
                    beta = np.linalg.solve(gram, rhs)
                except (FloatingPointError, np.linalg.LinAlgError) as exc:
                    raise ValueError("Return ridge solve failed") from exc
            if not np.isfinite(beta).all():
                raise ValueError("Nonfinite return model coefficient")
            diagnostic = {"solver": "weighted_ridge_linear_solve", "converged": True}
        coefficients.append(beta.tolist())
        diagnostics.append(
            {
                **diagnostic,
                "known_positive_weight_rows": int(used.sum()),
                "training_weight": float(weight.sum()),
                "maximum_label_known_ts": float(times[used, head].max()),
            }
        )
    artifact.update(
        fitted=True,
        prediction_kind="probability" if name == "logistic_offset" else "net_return",
        fit_cutoff_ts=float(fit_cutoff_ts),
        center=center.tolist(),
        scale=scale.tolist(),
        coefficients=coefficients,
        head_order=["YES"] if name == "logistic_offset" else ["YES", "NO"],
        head_diagnostics=diagnostics,
    )
    return artifact


def predict_candidate(artifact, X):
    """Predict from features alone; net returns already include target costs."""
    if artifact.get("schema_version") != ARTIFACT_VERSION or artifact.get("candidate") not in CANDIDATE_NAMES:
        raise ValueError("Unknown candidate artifact")
    name = artifact["candidate"]
    x = _features(X, artifact.get("feature_names", ()))
    if name in ("cash", "midpoint"):
        if artifact.get("fitted") is not False:
            raise ValueError("Stateless candidate unexpectedly contains a fit")
        values = np.zeros(len(x)) if name == "cash" else expit(x[:, 0])
        return {"kind": "cash" if name == "cash" else "probability", "values": values}
    if artifact.get("fitted") is not True:
        raise ValueError("Learned candidate is not fitted")
    expected_kind = "probability" if name == "logistic_offset" else "net_return"
    expected_heads = ["YES"] if name == "logistic_offset" else ["YES", "NO"]
    if artifact.get("prediction_kind") != expected_kind or artifact.get("head_order") != expected_heads:
        raise ValueError("Prediction units or side order differ from the candidate")
    center, scale = np.asarray(artifact["center"]), np.asarray(artifact["scale"])
    heads = 1 if name == "logistic_offset" else 2
    coefficients = np.asarray(artifact["coefficients"], dtype=np.float64)
    if (
        center.shape != (x.shape[1],)
        or scale.shape != center.shape
        or not np.isfinite(center).all()
        or not np.isfinite(scale).all()
        or (scale <= 0).any()
        or coefficients.shape != (heads, x.shape[1] + 1)
        or not np.isfinite(coefficients).all()
    ):
        raise ValueError("Invalid fitted transform or coefficients")
    with np.errstate(over="ignore", invalid="ignore"):
        values = _design(x, center, scale) @ coefficients.T
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite model linear prediction")
    if name == "logistic_offset":
        values = expit(x[:, 0] + values[:, 0])
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite model prediction")
    return {"kind": "probability" if name == "logistic_offset" else "net_return", "values": values}
