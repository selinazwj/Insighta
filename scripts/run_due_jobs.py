from __future__ import annotations

import argparse
import json

from api.main import run_due_background_jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run due Insighta background jobs.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum jobs to claim in this run.")
    args = parser.parse_args()
    result = run_due_background_jobs(limit=max(1, args.limit))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
