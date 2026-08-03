#!/bin/bash
# 강의 자동 녹음기 실행 스크립트
# 사용법:
#   ./run.sh            → 자동 녹음(소리 감지)
#   ./run.sh continuous → 연속 녹음(Ctrl+C까지)
#   ./run.sh --list     → 오디오 장치 목록 확인

cd "$(dirname "$0")" || exit 1
source .venv/bin/activate
python recorder.py "$@"
