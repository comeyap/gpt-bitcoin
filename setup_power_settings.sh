#!/bin/bash
# macOS 절전 모드 방지 및 트레이딩 봇 실행

echo "🔋 macOS 절전 모드 방지 설정"
echo "=========================="

# 절전 모드 방지 (AC 전원 연결 시)
echo "⚡ AC 전원 연결 시 절전 모드 비활성화..."
sudo pmset -c displaysleep 0    # 디스플레이 절전 비활성화
sudo pmset -c sleep 0           # 시스템 절전 비활성화
sudo pmset -c disksleep 0       # 디스크 절전 비활성화

# 배터리 사용 시 (더 보수적 설정)
echo "🔋 배터리 사용 시 절전 모드 설정..."
sudo pmset -b displaysleep 10   # 디스플레이 10분 후 절전
sudo pmset -b sleep 30          # 시스템 30분 후 절전
sudo pmset -b disksleep 10      # 디스크 10분 후 절전

echo "✅ 절전 모드 설정 완료"
echo ""
echo "📊 현재 전원 관리 설정:"
pmset -g

echo ""
echo "💡 참고사항:"
echo "- AC 전원에 연결하여 사용하시는 것을 권장합니다."
echo "- 맥북 뚜껑을 닫아도 작업이 계속됩니다."
echo "- 원래 설정으로 되돌리려면 restore_power_settings.sh를 실행하세요."
