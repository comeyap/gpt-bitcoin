#!/bin/bash
# macOS 절전 모드 설정 복원

echo "🔄 macOS 절전 모드 설정 복원"
echo "=========================="

# 기본 절전 설정으로 복원
echo "⚡ AC 전원 연결 시 기본 설정 복원..."
sudo pmset -c displaysleep 10   # 디스플레이 10분 후 절전
sudo pmset -c sleep 1           # 시스템 1분 후 절전 (Wake for network access)
sudo pmset -c disksleep 10      # 디스크 10분 후 절전

echo "🔋 배터리 사용 시 기본 설정 복원..."
sudo pmset -b displaysleep 2    # 디스플레이 2분 후 절전
sudo pmset -b sleep 1           # 시스템 1분 후 절전
sudo pmset -b disksleep 10      # 디스크 10분 후 절전

echo "✅ 절전 모드 설정이 기본값으로 복원되었습니다."
echo ""
echo "📊 현재 전원 관리 설정:"
pmset -g
