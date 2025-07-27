#!/usr/bin/env python3
"""
트레이딩 봇 결정 기록 조회 스크립트
Usage: python view_trading_history.py [limit]
"""

import sqlite3
import sys
import json
from datetime import datetime
import config

def format_timestamp(ts_str):
    """타임스탬프를 읽기 쉬운 형태로 변환"""
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%m/%d %H:%M")
    except:
        return ts_str

def format_krw(amount):
    """KRW 금액을 천단위 콤마로 포맷"""
    if amount is None:
        return "N/A"
    return f"{amount:,.0f}원"

def format_btc(amount):
    """BTC 금액을 적절한 자릿수로 포맷"""
    if amount is None or amount == 0:
        return "0 BTC"
    if amount < 0.001:
        return f"{amount:.8f} BTC"
    elif amount < 1:
        return f"{amount:.6f} BTC"
    else:
        return f"{amount:.4f} BTC"

def view_trading_history(limit=20):
    """트레이딩 결정 기록 조회"""
    print("=" * 80)
    print("🚀 비트코인 트레이딩 봇 결정 기록")
    print("=" * 80)
    
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            
            # 테이블 존재 확인
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='decisions'
            """)
            if not cursor.fetchone():
                print("❌ 데이터베이스 테이블이 존재하지 않습니다.")
                return
            
            # 전체 레코드 수 확인
            cursor.execute('SELECT COUNT(*) FROM decisions')
            total_count = cursor.fetchone()[0]
            print(f"📊 총 기록 수: {total_count}개")
            
            if total_count == 0:
                print("❌ 저장된 트레이딩 결정이 없습니다.")
                print("💡 봇을 실행하면 결정 기록이 여기에 표시됩니다.")
                return
            
            # 최근 결정들 조회
            cursor.execute('''
                SELECT timestamp, decision, percentage, reason, 
                       btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price
                FROM decisions 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (limit,))
            
            records = cursor.fetchall()
            
            if records:
                print(f"\n📈 최근 {len(records)}개 트레이딩 결정:")
                print("-" * 80)
                
                for i, record in enumerate(records, 1):
                    ts, decision, percentage, reason, btc_bal, krw_bal, avg_price, btc_price = record
                    
                    print(f"\n{i:2d}. [{format_timestamp(ts)}] {decision.upper()} ({percentage:.1f}%)")
                    print(f"    💰 잔고: {format_btc(btc_bal)} / {format_krw(krw_bal)}")
                    print(f"    📊 BTC가격: {format_krw(btc_price)} (평균매수가: {format_krw(avg_price)})")
                    print(f"    💭 이유: {reason or 'N/A'}")
                
                print("-" * 80)
                
                # 통계 정보
                cursor.execute('''
                    SELECT decision, COUNT(*) as count, AVG(percentage) as avg_pct
                    FROM decisions 
                    GROUP BY decision
                ''')
                stats = cursor.fetchall()
                
                print("\n📈 결정 통계:")
                for stat in stats:
                    decision, count, avg_pct = stat
                    print(f"   {decision.upper()}: {count}회 (평균 {avg_pct:.1f}%)")
                
                # 최근 수익률 계산 (간단한 버전)
                if len(records) >= 2:
                    latest_krw = records[0][5] or 0
                    latest_btc = records[0][4] or 0
                    latest_price = records[0][7] or 0
                    
                    oldest_krw = records[-1][5] or 0
                    oldest_btc = records[-1][4] or 0
                    oldest_price = records[-1][7] or 0
                    
                    if oldest_krw > 0 and oldest_price > 0:
                        current_total = latest_krw + (latest_btc * latest_price)
                        old_total = oldest_krw + (oldest_btc * oldest_price)
                        change_rate = ((current_total - old_total) / old_total) * 100
                        
                        print(f"\n💹 포트폴리오 변화:")
                        print(f"   {format_timestamp(records[-1][0])} → {format_timestamp(records[0][0])}")
                        print(f"   {format_krw(old_total)} → {format_krw(current_total)}")
                        print(f"   변화율: {change_rate:+.2f}%")
            
    except sqlite3.Error as e:
        print(f"❌ 데이터베이스 오류: {e}")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")

def view_detailed_record(record_id):
    """특정 기록의 상세 정보 조회"""
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM decisions WHERE id = ?
            ''', (record_id,))
            
            record = cursor.fetchone()
            if record:
                print("=" * 60)
                print(f"📋 트레이딩 결정 상세 정보 (ID: {record_id})")
                print("=" * 60)
                print(f"시간: {record[1]}")
                print(f"결정: {record[2].upper()}")
                print(f"비율: {record[3]}%")
                print(f"이유: {record[4] or 'N/A'}")
                print(f"BTC 잔고: {format_btc(record[5])}")
                print(f"KRW 잔고: {format_krw(record[6])}")
                print(f"BTC 평균매수가: {format_krw(record[7])}")
                print(f"현재 BTC 가격: {format_krw(record[8])}")
            else:
                print(f"❌ ID {record_id}에 해당하는 기록을 찾을 수 없습니다.")
                
    except Exception as e:
        print(f"❌ 오류 발생: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            if sys.argv[1] == "detail" and len(sys.argv) > 2:
                view_detailed_record(int(sys.argv[2]))
            else:
                limit = int(sys.argv[1])
                view_trading_history(limit)
        except ValueError:
            print("❌ 올바른 숫자를 입력해주세요.")
    else:
        view_trading_history()
    
    print("\n💡 사용법:")
    print("   python view_trading_history.py        # 최근 20개 기록")
    print("   python view_trading_history.py 50     # 최근 50개 기록")
    print("   python view_trading_history.py detail 5  # ID 5번 상세 정보")
