"""Map contract-source history from archived rule text, without outcome scores."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from research.experiments.e002_coverage import event_date, source_name
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import iso, utcnow


def main():
    archive = Archive()
    coverage = json.loads(Path("reports/E002_coverage.json").read_text())
    series_rows, mixed = [], []
    for series in coverage["series"]:
        by_event = defaultdict(list)
        for rec in series["record_ids"]:
            row = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
            for m in archive.json(row)["markets"]:
                day = event_date(m["event_ticker"])
                if day:
                    by_event[(day, m["event_ticker"])].append((source_name(m), rec))
        runs = []
        for (day, event), entries in sorted(by_event.items()):
            sources = sorted({source for source, _ in entries})
            source = sources[0] if len(sources) == 1 else "mixed"
            if len(sources) != 1:
                mixed.append({"event": event, "day": day, "sources": sources})
            if runs and runs[-1]["source"] == source:
                runs[-1]["last_event_date"] = day
                runs[-1]["events"] += 1
                runs[-1]["last_source_record_id"] = entries[-1][1]
            else:
                runs.append(
                    {
                        "source": source,
                        "first_event_date": day,
                        "last_event_date": day,
                        "events": 1,
                        "first_source_record_id": entries[0][1],
                        "last_source_record_id": entries[-1][1],
                    }
                )
        series_rows.append({"series": series["ticker"], "source_runs": runs, "events": len(by_event)})
    transitions = []
    for s in series_rows:
        for old, new in zip(s["source_runs"], s["source_runs"][1:], strict=False):
            transitions.append(
                {
                    "series": s["series"],
                    "old_source": old["source"],
                    "new_source": new["source"],
                    "last_old_event_date": old["last_event_date"],
                    "first_new_event_date": new["first_event_date"],
                }
            )
    nws_twc = [t for t in transitions if t["old_source"] == "NWS" and t["new_source"] == "TWC"]
    result = {
        "experiment": "E000-source-transitions",
        "generated_at": iso(utcnow()),
        "series": series_rows,
        "transitions": transitions,
        "mixed_source_events": mixed,
        "summary": {
            "series": len(series_rows),
            "events": sum(s["events"] for s in series_rows),
            "nws_to_twc_transitions": len(nws_twc),
            "first_twc_event_dates": dict(Counter(t["first_new_event_date"] for t in nws_twc)),
            "mixed_source_events": len(mixed),
            "outcomes_scored": 0,
        },
        "limitations": [
            "Describes rule text in today's retrieved historical metadata",
            "Does not prove original contract publication or exclude retrospective edits",
            "Current series discovery may miss delisted historical series",
            "Generic PDF/source conflicts and TWC observation-window precision remain unresolved",
        ],
    }
    archive.append("experiment_report", "E000_source_transitions", utcnow(), {}, canonical(result).encode())
    Path("reports/E000_source_transitions.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"summary": result["summary"], "nws_to_twc": nws_twc}, indent=2))
    archive.close()


if __name__ == "__main__":
    main()
