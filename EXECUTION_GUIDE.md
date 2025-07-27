# 🚀 비트코인 트레이딩 봇 실행 가이드

## 📋 준비사항

1. **전원 연결**: 맥북을 AC 전원에 연결하세요
2. **WiFi 연결**: 안정적인 인터넷 연결 확인
3. **API 키**: 모든 환경변수(.env) 설정 확인

## 🚀 실행 방법

### 방법 1: 자동 백그라운드 실행 (추천)

```bash
# 1. 절전 모드 방지 설정 (관리자 권한 필요)
./setup_power_settings.sh

# 2. 트레이딩 봇 백그라운드 실행
./start_trading_bot.sh

# 3. 실시간 로그 확인
tail -f trading_bot.log

# 4. 웹 대시보드 실행 (선택사항)
streamlit run trading_dashboard.py
```

### 방법 2: 직접 실행

```bash
# 가상환경에서 직접 실행
./.venv/bin/python autotrade_v3.py
```

## 📊 모니터링 방법

### 1. 로그 파일 확인
```bash
# 실시간 로그 보기
tail -f trading_bot.log

# 로그 검색
grep "decision" trading_bot.log
```

### 2. 웹 대시보드
```bash
# 대시보드 실행
streamlit run trading_dashboard.py

# 브라우저에서 http://localhost:8501 접속
```

### 3. 명령어로 기록 확인
```bash
# 최근 20개 트레이딩 결정 확인
python view_trading_history.py

# 최근 50개 확인
python view_trading_history.py 50
```

## 🛑 중지 방법

```bash
# 트레이딩 봇 중지
./stop_trading_bot.sh

# 또는 프로세스 직접 종료
kill $(cat trading_bot.pid)

# 절전 설정 복원
./restore_power_settings.sh
```

## 🔧 문제 해결

### 1. 봇이 실행되지 않는 경우
```bash
# 프로세스 확인
ps aux | grep autotrade_v3

# 로그 파일 확인
cat trading_bot.log
```

### 2. 화면이 꺼지는 경우
```bash
# 절전 설정 확인
pmset -g

# 절전 모드 재설정
./setup_power_settings.sh
```

### 3. ChromeDriver 오류
```bash
# ChromeDriver 경로 확인
echo $CHROMEDRIVER_PATH

# Chrome 버전 확인
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --version
```

## 📅 스케줄

- **00:01**: 자정 실행
- **08:01**: 오전 8시 실행  
- **16:01**: 오후 4시 실행

## ⚠️ 주의사항

1. **AC 전원 연결**: 배터리로만 실행 시 절전 모드로 인해 중단될 수 있음
2. **네트워크 안정성**: WiFi 연결 끊김 시 재연결 필요
3. **API 제한**: Upbit API 호출 제한 확인
4. **디스크 용량**: 로그 파일 및 데이터베이스 용량 주기적 확인

## 📱 알림 설정 (선택사항)

웹 대시보드를 외부에서 접근하려면:
1. 포트 포워딩 설정
2. ngrok 같은 터널링 서비스 사용
3. 클라우드 서버 배포 고려
