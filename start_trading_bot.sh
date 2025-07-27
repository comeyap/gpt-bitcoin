#!/bin/bash
# 비트코인 트레이딩 봇 실행 스크립트

echo "🚀 비트코인 트레이딩 봇 백그라운드 실행 스크립트"
echo "================================================"

# 작업 디렉토리로 이동
cd "/Users/kylemax/dev/pythonworkspace/gpt-bitcoin/gpt-bitcoin"

# 가상환경 활성화 및 백그라운드 실행
nohup ./.venv/bin/python autotrade_v3.py > trading_bot.log 2>&1 &

# 프로세스 ID 저장
echo $! > trading_bot.pid

echo "✅ 트레이딩 봇이 백그라운드에서 시작되었습니다."
echo "📊 프로세스 ID: $(cat trading_bot.pid)"
echo "📝 로그 파일: trading_bot.log"
echo ""
echo "📋 유용한 명령어:"
echo "   tail -f trading_bot.log          # 실시간 로그 보기"
echo "   kill \$(cat trading_bot.pid)      # 봇 중지"
echo "   ps aux | grep autotrade_v3       # 실행 상태 확인"
echo ""
echo "🌐 웹 대시보드 실행:"
echo "   streamlit run trading_dashboard.py"
