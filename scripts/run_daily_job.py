"""장 마감 파이프라인을 한 번 실행한다.

서버(uvicorn)나 모바일 앱이 없어도 된다. GitHub Actions·OS cron·수동 실행용.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    _load_dotenv()
    if not os.getenv("SMS_PROVIDER", "").strip():
        os.environ["SMS_PROVIDER"] = "test"

    parser = argparse.ArgumentParser(description="Stock Timing daily close job")
    parser.add_argument("--force", action="store_true", help="휴장·주말도 강제 실행")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="기준일 YYYY-MM-DD")
    args = parser.parse_args()

    from jobs import run_daily_job  # noqa: E402

    result = run_daily_job(args.as_of, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
