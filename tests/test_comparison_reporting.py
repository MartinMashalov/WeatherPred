from research.experiments.e003_daily_baselines import summaries_and_intervals


def test_calendar_gaps_withhold_inference_and_do_not_shrink_holm_family():
    rows = []
    for horizon, days in ((24, range(1, 15)), (6, (1, 3, 5, 7))):
        for day in days:
            for model, score in (("raw_market", 0.2), ("candidate", 0.19)):
                rows.append(
                    {
                        "day": f"2025-01-{day:02}",
                        "event": str(day),
                        "model": model,
                        "horizon_hours": horizon,
                        "probability": 0.4,
                        "outcome": 0,
                        "weight": 1.0,
                        "brier": score,
                        "log_loss": score,
                    }
                )
    summaries, intervals, _ = summaries_and_intervals(rows, ["candidate"])
    assert next(r for r in summaries if r["model"] == "candidate" and r["horizon_hours"] == 6)["days"] == 4
    assert all("confidence_unavailable" in r for r in intervals if r["horizon_hours"] == 6)
    assert all("lower" in r for r in intervals if r["horizon_hours"] == 24)
    assert all("holm_adjusted_pvalue_weather_models_and_horizons" not in r for r in intervals)
