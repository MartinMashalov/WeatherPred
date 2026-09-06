import argparse
import json
import logging

from weatherpred.archive import Archive
from weatherpred.http import PublicClient
from weatherpred.universe import archive_rules, collect_markets, discover


def main():
    parser = argparse.ArgumentParser(description="Kalshi weather empirical research (public GETs only)")
    parser.add_argument("--data", default="data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    markets = sub.add_parser("markets")
    markets.add_argument("--status", choices=["open", "settled", "closed", "unopened"], default="open")
    markets.add_argument("--series", nargs="*")
    sub.add_parser("rules")
    sub.add_parser("verify-archive")
    sub.add_parser("scan-baskets")
    sub.add_parser("dashboard")
    sub.add_parser("replay-baskets")
    sub.add_parser("score-shadow")
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--cycles", type=int, default=120)
    capture_parser.add_argument("--interval", type=int, default=30)
    shadow_parser = sub.add_parser("shadow")
    shadow_parser.add_argument("--hours", type=int, default=4)
    shadow_parser.add_argument("--model-record", type=int)
    get = sub.add_parser("get")
    get.add_argument("url")
    get.add_argument("--kind", default="http")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archive = Archive(args.data)
    client = PublicClient(archive)
    try:
        if args.command == "discover":
            result = discover(client)
            print(
                json.dumps(
                    {k: v for k, v in result.items() if k not in ("series", "outside_category_candidates")},
                    indent=2,
                )
            )
        elif args.command == "verify-archive":
            print(json.dumps(archive.verify(), indent=2))
        elif args.command == "dashboard":
            from weatherpred.report import dashboard

            print(json.dumps(dashboard(archive), indent=2))
        elif args.command == "replay-baskets":
            from weatherpred.audit import replay_baskets

            print(json.dumps(replay_baskets(archive), indent=2))
        elif args.command == "scan-baskets":
            from weatherpred.basket import scan_baskets

            print(json.dumps(scan_baskets(client), indent=2))
        elif args.command == "capture":
            from weatherpred.capture import capture

            print(json.dumps(capture(client, args.cycles, args.interval), indent=2))
        elif args.command == "shadow":
            from weatherpred.shadow import run_shadow

            print(json.dumps(run_shadow(client, args.hours, args.model_record), indent=2))
        elif args.command == "score-shadow":
            from weatherpred.shadow_outcomes import score_shadow

            print(json.dumps(score_shadow(client), indent=2))
        elif args.command == "get":
            response, record_id = client.get(args.url, kind=args.kind)
            print(
                json.dumps(
                    {"record_id": record_id, "status": response.status_code, "bytes": len(response.content)},
                    indent=2,
                )
            )
        else:
            with open("reports/universe.json") as f:
                series = json.load(f)["series"]
            if args.command == "markets":
                if args.series:
                    names = set(args.series)
                    series = [s for s in series if s["ticker"] in names]
                    if names - {s["ticker"] for s in series}:
                        raise ValueError("Unknown series requested")
                result = collect_markets(client, series, status=args.status)
                print(json.dumps({"markets": len(result["markets"]), "failures": result["failures"]}))
            elif args.command == "rules":
                result = archive_rules(client, series)
                print(json.dumps({"templates": len(result), "failures": [r for r in result if "error" in r]}))
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
