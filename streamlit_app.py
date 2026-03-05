import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

try:
    import pyupbit
except ImportError:
    pyupbit = None

try:
    import config
except ImportError:
    class config:
        DB_PATH = 'trading_decisions.sqlite'


def load_data():
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            df = pd.read_sql_query(
                "SELECT timestamp, decision, percentage, reason, "
                "btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price "
                "FROM decisions ORDER BY timestamp",
                conn,
            )
            return df
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return pd.DataFrame()


def get_current_btc_price():
    try:
        if pyupbit:
            orderbook = pyupbit.get_orderbook(ticker="KRW-BTC")
            return orderbook['orderbook_units'][0]["ask_price"]
    except Exception:
        pass
    return None


def main():
    st.set_page_config(layout="wide", page_title="BTC 자동매매 대시보드")
    st.title("실시간 비트코인 GPT 자동매매 기록")
    st.write("by 유튜버 [조코딩](https://youtu.be/MgatVqXXoeA) - "
             "[Github](https://github.com/youtube-jocoding/gpt-bitcoin)")
    st.write("---")

    df = load_data()
    if df.empty:
        st.warning("아직 매매 기록이 없습니다.")
        return

    current_price = get_current_btc_price()
    if current_price is None:
        st.warning("현재 비트코인 가격을 가져올 수 없습니다. DB의 마지막 기록 가격을 사용합니다.")
        current_price = df.iloc[-1]['btc_krw_price'] if 'btc_krw_price' in df.columns else 0

    start_value = 2_000_000
    latest_row = df.iloc[-1]
    btc_balance = latest_row['btc_balance']
    krw_balance = latest_row['krw_balance']
    current_value = int(btc_balance * current_price + krw_balance)

    time_diff = datetime.now() - pd.to_datetime(df.iloc[0]['timestamp'])
    days = time_diff.days
    hours = time_diff.seconds // 3600
    minutes = (time_diff.seconds % 3600) // 60

    # Metrics row
    col1, col2, col3 = st.columns(3)
    pnl_pct = round((current_value - start_value) / start_value * 100, 2)
    with col1:
        st.metric("수익률", f"{pnl_pct}%", delta=f"{pnl_pct}%")
    with col2:
        st.metric("현재 자산", f"{current_value:,}원")
    with col3:
        st.metric("BTC 가격", f"{current_price:,.0f}원")

    st.write(f"투자기간: {days}일 {hours}시간 {minutes}분 | 시작 원금: {start_value:,}원")
    st.write(f"보유 현금: {krw_balance:,.0f}원 | 보유 BTC: {btc_balance:.8f} BTC")

    # Portfolio value chart
    st.subheader("포트폴리오 가치 변화")
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['portfolio_value'] = df['btc_balance'] * df['btc_krw_price'] + df['krw_balance']
    st.line_chart(df.set_index('timestamp')['portfolio_value'])

    # Decision distribution
    st.subheader("매매 결정 분포")
    col1, col2 = st.columns(2)
    with col1:
        decision_counts = df['decision'].value_counts()
        st.bar_chart(decision_counts)
    with col2:
        st.dataframe(
            df[['timestamp', 'decision', 'percentage', 'reason']].tail(10),
            use_container_width=True,
        )

    # Full data
    st.subheader("전체 매매 기록")
    st.dataframe(df, use_container_width=True)


if __name__ == '__main__':
    main()
