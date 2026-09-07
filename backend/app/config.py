"""환경 설정.

로컬 개발(Cloud Agent 포함)에서는 외부 Supabase/SMS 자격증명이 없어도
SQLite 로 완전히 동작하도록 기본값을 둔다. 운영에서는 환경변수로
Supabase(PostgreSQL)와 SMS API 를 주입한다.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = BASE_DIR / "stock_timing.db"


class Settings:
    # 로컬 기본은 SQLite. 운영은 SUPABASE_DB_URL(PostgreSQL) 주입.
    sqlite_path: str = os.getenv("SQLITE_PATH", str(DEFAULT_SQLITE_PATH))
    supabase_db_url: str | None = os.getenv("SUPABASE_DB_URL")

    # SMS 는 기본 비활성(테스트 모드). 실제 발송은 자격증명 주입 시.
    sms_enabled: bool = os.getenv("SMS_ENABLED", "false").lower() == "true"
    sms_api_key: str | None = os.getenv("SMS_API_KEY")

    # 신호 기본 임계값 (%). 개발계획서 §13~§14.
    default_x1: float = float(os.getenv("DEFAULT_X1", "10"))
    default_x2: float = float(os.getenv("DEFAULT_X2", "20"))
    default_y1: float = float(os.getenv("DEFAULT_Y1", "10"))
    default_y2: float = float(os.getenv("DEFAULT_Y2", "20"))

    cors_origins: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")


settings = Settings()
