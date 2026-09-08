# Stock Timing App

KOSPI·KOSDAQ 종목 중 거래대금이 많은 종목을 선정하고, 관심종목에 대해 3일 이동평균선(MA3) 기반 매수·매도 신호를 알려주는 앱입니다.

주가 예측이 아니라, 정해진 규칙을 서버가 계산하고 조건이 맞으면 SMS로 알림을 보내는 구조입니다.

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| 모바일 | React Native + Expo |
| 백엔드 | Python + FastAPI |
| DB | Supabase (PostgreSQL) |
| 차트 | TradingView Lightweight Charts |
| 스케줄러 | GitHub Actions 또는 Cloud Scheduler |

## 저장소 구조

```
Stock-Timing-App/
├─ backend/     # FastAPI, 데이터 수집, MA3, 신호 엔진
├─ frontend/    # React Native (Expo)
├─ database/    # Supabase 스키마, RLS, 마이그레이션
├─ scripts/     # 배치·스케줄 작업
├─ docs/        # 개발계획서 및 문서
└─ README.md
```

## 개발 원칙

1. LLM에게 주가 계산을 시키지 않는다.
2. 이미 받은 주식 데이터를 다시 받지 않는다.
3. TOP20이 아닌 종목의 일별 주가는 기본적으로 수집하지 않는다.
4. 앱이 꺼져 있어도 서버에서 자동으로 처리한다.
5. 6개월 차트와 3개월 신호 분석을 분리한다.

## 운영 DB (STEP 18-1)

FastAPI·일일 잡의 운영 데이터는 `APP_ENV=production` 이고 `DATABASE_URL`(Supabase Postgres)이 있으면 PostgreSQL을 씁니다. 로컬 개발·`verify_*` 는 기본 SQLite(`backend/data/prices.db`)입니다. 백테스트는 계속 `backend/data/backtest_prices.db` 입니다.

1. Supabase SQL Editor에서 `database/001_schema.sql` 다음 `database/003_runtime_compat.sql` 을 실행합니다 (`002_rls.sql` 은 로그인 연동 전 선택).
2. `backend/.env` 에 `DATABASE_URL` 을 넣고 `APP_ENV=production` 으로 API를 띄웁니다.
3. 기존 SQLite 이관: `python backend/migrate_to_supabase.py` (INSERT ON CONFLICT DO NOTHING, 테스트 종목 코드 제외).

## 자동 실행 (STEP 18-2)

모바일 앱과 FastAPI가 꺼져 있어도 장 마감 파이프라인(TOP20 → 가격 → MA3 → 신호 → 관심종목 → SMS)이 돌아가야 합니다. 운영 기본값은 FastAPI 안의 APScheduler를 쓰지 않습니다.

1. GitHub repo Settings → Secrets 에 `DATABASE_URL` (필수), SMS용 `SMS_PROVIDER` / `SOLAPI_*` (실발송 시) 를 넣습니다.
2. `.github/workflows/daily-job.yml` 이 평일 **16:00 KST**에 `python scripts/run_daily_job.py` 를 실행합니다. Actions 탭에서 수동 실행도 됩니다.
3. 항상 켜 둔 PC/VPS를 쓰려면 `python scripts/job_worker.py` 만 띄웁니다 (uvicorn 불필요).

## SMS (STEP 18-3)

설정에 한국 휴대폰 번호를 저장하면 관심종목 BUY/SELL 신호만 SMS를 보냅니다. `SMS_PROVIDER=test` 는 로그(`backend/data/sms_log.jsonl`)만 남기고, `solapi` 는 실제 발송입니다.

- 테스트: `POST /sms/test` (기본은 발송 없이 로그). 실제 문자: `{"live": true}` + Solapi 키.
- 같은 신호는 `signal_history` 로 중복 발송하지 않습니다. 발송 실패(`sms_sent=0`)면 다음 잡에서 재시도합니다.
- Solapi 5xx/타임아웃은 `SMS_RETRY_MAX`(기본 3) 만큼 즉시 재시도하고, 실패는 `sms_log.jsonl` 과 잡 로그에 남깁니다.

## Android 설치 (STEP 18-4)

실제 폰에서는 `127.0.0.1` 이 PC가 아닙니다. PC와 폰을 같은 Wi-Fi에 두고:

1. `python backend/run_api_lan.py` 로 API를 `0.0.0.0:8000` 에 엽니다. 출력된 `http://192.168.x.x:8000` 을 복사합니다. (Windows 방화벽에서 8000 허용)
2. 앱 설정 → 서버 연결에 그 주소를 넣고 **서버 연결 확인**.
3. 홈 TOP20 → 관심종목 ☆ → 종목 차트 → 설정 저장 → 신호 이력.
4. 실제 SMS: `backend/.env` 의 `SMS_PROVIDER=solapi` 와 SOLAPI 키, 설정에서 번호 저장 후 **실제 문자로 테스트** 스위치를 켜고 테스트 SMS.

설치 파일(APK):

```text
cd frontend
npx eas-cli login
npx eas-cli build:configure
npx eas-cli build -p android --profile preview
```

`preview` 프로필은 내부 배포용 APK입니다. 빌드 시 `EXPO_PUBLIC_API_URL` 을 LAN 또는 공개 API 주소로 바꾸면 기본값이 그 주소가 됩니다. 설치 후에도 설정 화면에서 주소를 바꿀 수 있습니다.

자세한 내용은 [docs/Stock_Timing_App_개발계획서_V3.md](docs/Stock_Timing_App_개발계획서_V3.md)를 참고하세요.
