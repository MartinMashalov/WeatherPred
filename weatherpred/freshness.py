"""A separately registered freshness ablation; the original forecast stays pinned."""

from collections import Counter

from weatherpred.forecasts import IndexSeries, ResidualDistribution, fit_residuals
from weatherpred.shadow import validate_lineage
from weatherpred.timeutil import parse_time


def millis(value):
    return int(parse_time(value).timestamp() * 1000)


def train_fresh_model(points, protocol):
    start, cutoff = (millis(protocol[k]) for k in ("training_start", "training_end_exclusive"))
    # Date-gate individual points before building a series or examining labels.
    series = IndexSeries([p for p in points if start - 3_600_000 <= p["t"] < cutoff])
    rows, exclusions = [], Counter()
    for settlement in range(start, cutoff, 3_600_000):
        if settlement + 300_000 >= cutoff:
            exclusions["label_deadline_after_fit_cutoff"] += 1
            continue
        label = series.label(settlement)
        if label is None:
            exclusions["missing_label"] += 1
            continue
        for horizon in protocol["horizons_minutes"]:
            decision = settlement - horizon * 60_000
            feature = series.features(
                decision,
                settlement,
                protocol["historical_publication_lag_minutes"] * 60_000,
                protocol["max_last_point_age_minutes"] * 60_000,
                protocol["trend_lookback_minutes"] * 60_000,
            )
            if feature is None:
                exclusions["missing_features"] += 1
                continue
            rows.append(
                {
                    "settlement_ms": settlement,
                    "decision_ms": decision,
                    "horizon_minutes": horizon,
                    "features": feature,
                    "observed": label["v"],
                    "label_point_ms": label["t"],
                }
            )
    models = {}
    for horizon in protocol["horizons_minutes"]:
        for name in protocol["models"]:
            base, kind = name.split("_")
            residuals = fit_residuals(rows, base, horizon, cutoff)
            distribution = ResidualDistribution(residuals, kind)
            models[f"{name}:{horizon}"] = {
                "base": base,
                "kind": kind,
                "residuals": residuals,
                "n": len(residuals),
                "bias": distribution.bias,
                "sigma": distribution.sigma,
            }
    return models, rows, dict(exclusions)


def paired_forecast(archive, parent_record, model_record, published):
    parent, artifact = archive.json(parent_record), archive.json(model_record)
    protocol = artifact["protocol"]
    scheduled, settlement = (parse_time(parent[k]) for k in ("scheduled_at", "settlement_at"))
    if (
        parent["protocol"] != "E004-forward-v1"
        or parent["model_record_id"] != protocol["parent_model_record_id"]
    ):
        raise ValueError("Parent forecast differs from the preregistered comparator")
    slots = artifact["slots"]
    if not any(
        s["decision_at"] == parent["scheduled_at"]
        and s["settlement_at"] == parent["settlement_at"]
        and s["horizon_minutes"] == parent["horizon_minutes"]
        for s in slots
    ):
        raise ValueError("Parent slot was not registered before outcomes")
    if parse_time(model_record["available_at"]) >= scheduled:
        raise ValueError("New model was not frozen before the scheduled decision")
    if not 0 <= (published - scheduled).total_seconds() <= protocol["maximum_publication_lateness_seconds"]:
        raise ValueError("Freshness publication missed its window")
    if parent_record["available_at"] != parent["published_at"]:
        raise ValueError("Parent archive timestamp differs from publication")
    if archive.db.execute(
        "SELECT 1 FROM records WHERE kind='shadow_invalidated' AND key=?", (str(parent_record["id"]),)
    ).fetchone():
        raise ValueError("Parent forecast was invalidated")
    evidence = list(
        dict.fromkeys([*parent["evidence_record_ids"], parent_record["id"], parent["model_record_id"]])
    )
    validate_lineage(archive, evidence, published, settlement, model_record["id"])
    validate_lineage(
        archive,
        parent["evidence_record_ids"],
        parse_time(parent["published_at"]),
        settlement,
        parent["model_record_id"],
    )
    source_rows = [
        archive.db.execute("SELECT * FROM records WHERE id=? AND kind='shadow_index'", (r,)).fetchone()
        for r in parent["evidence_record_ids"]
    ]
    source_rows = [r for r in source_rows if r is not None]
    if len(source_rows) != 1:
        raise ValueError("Ambiguous parent index snapshot")
    # Both variants use the parent's actual snapshot time, not this consumer's later time.
    feature = IndexSeries(archive.json(source_rows[0])["timeseries"]).features(
        millis(parent["snapshot_at"]),
        millis(parent["settlement_at"]),
        protocol["historical_publication_lag_minutes"] * 60_000,
        protocol["max_last_point_age_minutes"] * 60_000,
        protocol["trend_lookback_minutes"] * 60_000,
    )
    if feature is None:
        raise ValueError("Fresh canonical features missing or stale")
    markets = []
    for market in parent["markets"]:
        probabilities = {"original_10m_" + k: p for k, p in market["probabilities"].items()}
        for name in protocol["models"]:
            fit = artifact["models"][f"{name}:{parent['horizon_minutes']}"]
            dist = ResidualDistribution(tuple(fit["residuals"]), fit["kind"])
            probabilities["fresh_5m_" + name] = dist.probability(
                feature[fit["base"]], {**market, "strike_type": "greater"}
            )
        markets.append(
            {
                "ticker": market["ticker"],
                "floor_strike": market["floor_strike"],
                "probabilities": probabilities,
                "intended_quantity": 0,
                "intended_trade": "abstain",
            }
        )
    return {
        "protocol": protocol["experiment"],
        "parent_forecast_record_id": parent_record["id"],
        "model_record_id": model_record["id"],
        "scheduled_at": parent["scheduled_at"],
        "snapshot_at": parent["snapshot_at"],
        "published_at": published.isoformat(timespec="microseconds"),
        "settlement_at": parent["settlement_at"],
        "horizon_minutes": parent["horizon_minutes"],
        "event": parent["event"],
        "evidence_record_ids": evidence,
        "features": feature,
        "original_features": parent["features"],
        "markets": markets,
        "real_money_recommended_size": 0,
        "fills": 0,
        "limitations": [
            "Same archived quote snapshot may be up to 20 seconds old at consumer publication",
            "Paired probability comparison only; no execution or profit inference",
            "Historical fitting assumes five-minute availability without initial publication proof",
        ],
    }
