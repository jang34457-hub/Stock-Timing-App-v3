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

## 로컬 개발 환경 실행

의존성 설치(백엔드 가상환경 + 프론트엔드 패키지):

```bash
bash scripts/cloud-agent-install.sh
```

백엔드(FastAPI, 기본 SQLite + 샘플 시드) 실행:

```bash
cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs 에서 API 확인, http://localhost:8000/top20 등
```

프론트엔드(Expo 웹) 실행:

```bash
cd frontend && npx expo start --web --port 8081
# http://localhost:8081
```

백엔드 테스트:

```bash
cd backend && . .venv/bin/activate && pytest
```

> 로컬에서는 외부 자격증명 없이 SQLite 와 테스트 모드 SMS 로 동작합니다.
> 운영 전환 시 `backend/.env.example` 을 참고해 Supabase(PostgreSQL)와 SMS API 를 주입하세요.
> Supabase 스키마는 [`database/schema.sql`](database/schema.sql) 에 있습니다.

## 개발 원칙

1. LLM에게 주가 계산을 시키지 않는다.
2. 이미 받은 주식 데이터를 다시 받지 않는다.
3. TOP20이 아닌 종목의 일별 주가는 기본적으로 수집하지 않는다.
4. 앱이 꺼져 있어도 서버에서 자동으로 처리한다.
5. 6개월 차트와 3개월 신호 분석을 분리한다.

자세한 내용은 [docs/Stock_Timing_App_개발계획서_V3.md](docs/Stock_Timing_App_개발계획서_V3.md)를 참고하세요.
