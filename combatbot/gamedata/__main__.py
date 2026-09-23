"""Diagnostic CLI offline : python -m combatbot.gamedata --client DOSSIER."""
import argparse
import json
from pathlib import Path

from .provider import LocalGameDataProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--map-id", type=int)
    parser.add_argument("--output", type=Path, help="Dossier de rapport/cache hors du client")
    args = parser.parse_args()
    provider = LocalGameDataProvider(args.client, cache_dir=args.output / "cache" if args.output else None)
    report = provider.scan_client()
    if args.map_id is not None:
        try:
            game_map = provider.get_map(args.map_id)
            if args.output:
                provider.export_map(args.map_id, args.output / f"map_{args.map_id}.json")
            print(json.dumps(game_map.summary(), ensure_ascii=False))
        except (ValueError, OSError) as exc:
            report.errors.append(str(exc))
    if args.output:
        try:
            provider.export_report(args.output / "probe-report.json")
        except (ValueError, OSError) as exc:
            report.errors.append(str(exc))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.status == "SCANNED" and not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
