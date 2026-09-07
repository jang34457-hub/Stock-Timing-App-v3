#!/usr/bin/env bash
# Cloud Agent / 로컬 개발 환경 부트스트랩 스크립트.
# 멱등(idempotent)하게 백엔드(Python/FastAPI)와 프론트엔드(Expo) 의존성을 설치한다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Backend: Python 가상환경 + 의존성"
cd "$REPO_ROOT/backend"
rm -rf .venv
if ! python3 -m venv .venv 2>/dev/null; then
  # 기본 이미지에 python3-venv(ensurepip) 가 없으면 설치한다.
  echo "    python3-venv 미설치 -> apt 로 설치"
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv python3-pip
  rm -rf .venv
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
deactivate

echo "==> Frontend: npm 의존성"
cd "$REPO_ROOT/frontend"
npm install --no-audit --no-fund

echo "==> 설치 완료"
