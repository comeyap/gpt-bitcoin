#!/bin/bash
# 비트코인 트레이딩 봇 중지 스크립트

echo "🛑 비트코인 트레이딩 봇 중지 스크립트"
echo "=================================="

cd "/Users/kylemax/dev/pythonworkspace/gpt-bitcoin/gpt-bitcoin"

if [ -f "trading_bot.pid" ]; then
    PID=$(cat trading_bot.pid)
    echo "📊 프로세스 ID: $PID"
    
    if ps -p $PID > /dev/null 2>&1; then
        echo "🔄 봇을 중지하는 중..."
        kill $PID
        
        # 프로세스가 완전히 종료될 때까지 대기
        sleep 2
        
        if ps -p $PID > /dev/null 2>&1; then
            echo "⚠️  강제 종료합니다..."
            kill -9 $PID
        fi
        
        rm -f trading_bot.pid
        echo "✅ 트레이딩 봇이 중지되었습니다."
    else
        echo "❌ 해당 프로세스가 실행 중이 아닙니다."
        rm -f trading_bot.pid
    fi
else
    echo "❌ PID 파일을 찾을 수 없습니다. 봇이 실행 중이 아닐 수 있습니다."
fi

echo ""
echo "📋 실행 중인 관련 프로세스 확인:"
ps aux | grep -E "(autotrade_v3|python.*autotrade)" | grep -v grep
