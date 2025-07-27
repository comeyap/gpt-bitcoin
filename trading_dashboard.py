import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import config
import os

st.set_page_config(
    page_title="비트코인 트레이딩 봇 대시보드",
    page_icon="🚀",
    layout="wide"
)

def load_trading_data():
    """데이터베이스에서 트레이딩 데이터 로드"""
    if not os.path.exists(config.DB_PATH):
        return pd.DataFrame()
    
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            query = """
            SELECT id, timestamp, decision, percentage, reason, 
                   btc_balance, krw_balance, btc_avg_buy_price, btc_krw_price
            FROM decisions 
            ORDER BY timestamp DESC
            """
            df = pd.read_sql_query(query, conn)
            
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df['total_value'] = df['krw_balance'] + (df['btc_balance'] * df['btc_krw_price'])
                
        return df
    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        return pd.DataFrame()

def main():
    st.title("🚀 비트코인 트레이딩 봇 대시보드")
    st.markdown("---")
    
    # 데이터 로드
    df = load_trading_data()
    
    if df.empty:
        st.warning("📊 저장된 트레이딩 결정이 없습니다.")
        st.info("💡 봇을 실행하면 결정 기록이 여기에 표시됩니다.")
        
        # 예시 정보 표시
        st.subheader("📋 기록될 정보")
        sample_data = {
            "항목": ["결정 시간", "매매 결정", "투자 비율", "결정 이유", "BTC 잔고", "KRW 잔고", "평균 매수가", "현재 BTC 가격"],
            "설명": [
                "결정이 내려진 정확한 시간",
                "buy, sell, hold 중 하나",
                "매매할 자산의 비율 (1-100%)",
                "GPT-4가 제공한 결정 근거",
                "현재 보유 중인 BTC 수량",
                "현재 보유 중인 KRW 잔고",
                "BTC 평균 매수 가격",
                "결정 시점의 BTC 시장 가격"
            ]
        }
        st.table(pd.DataFrame(sample_data))
        
        return
    
    # 상단 메트릭스
    col1, col2, col3, col4 = st.columns(4)
    
    latest_record = df.iloc[0]
    
    with col1:
        st.metric(
            "📊 총 결정 수",
            len(df),
            delta=None
        )
    
    with col2:
        st.metric(
            "💰 현재 총 자산",
            f"{latest_record['total_value']:,.0f} 원",
            delta=None
        )
    
    with col3:
        st.metric(
            "₿ BTC 잔고",
            f"{latest_record['btc_balance']:.6f} BTC",
            delta=None
        )
    
    with col4:
        st.metric(
            "💵 KRW 잔고",
            f"{latest_record['krw_balance']:,.0f} 원",
            delta=None
        )
    
    # 결정 통계
    st.subheader("📈 결정 통계")
    decision_counts = df['decision'].value_counts()
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 파이 차트
        fig_pie = px.pie(
            values=decision_counts.values,
            names=decision_counts.index,
            title="결정 유형 분포"
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    
    with col2:
        # 결정 통계 테이블
        stats_df = df.groupby('decision').agg({
            'percentage': ['count', 'mean'],
            'total_value': 'last'
        }).round(2)
        stats_df.columns = ['횟수', '평균 비율(%)', '최근 자산가치']
        st.dataframe(stats_df, use_container_width=True)
    
    # 시간별 자산 변화
    if len(df) > 1:
        st.subheader("💹 자산 가치 변화")
        
        fig_line = go.Figure()
        
        fig_line.add_trace(go.Scatter(
            x=df['timestamp'][::-1],  # 시간순 정렬
            y=df['total_value'][::-1],
            mode='lines+markers',
            name='총 자산 가치',
            line=dict(color='#1f77b4', width=2),
            marker=dict(size=6)
        ))
        
        fig_line.update_layout(
            title="시간별 총 자산 가치 변화",
            xaxis_title="시간",
            yaxis_title="자산 가치 (KRW)",
            hovermode='x unified'
        )
        
        st.plotly_chart(fig_line, use_container_width=True)
    
    # 최근 결정 기록
    st.subheader("📋 최근 트레이딩 결정")
    
    # 표시할 컬럼 선택
    display_df = df.copy()
    display_df['timestamp'] = display_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    display_df['btc_balance'] = display_df['btc_balance'].round(8)
    display_df['krw_balance'] = display_df['krw_balance'].round(0)
    display_df['btc_krw_price'] = display_df['btc_krw_price'].round(0)
    display_df['total_value'] = display_df['total_value'].round(0)
    
    # 컬럼명 한글화
    display_columns = {
        'timestamp': '시간',
        'decision': '결정',
        'percentage': '비율(%)',
        'btc_balance': 'BTC 잔고',
        'krw_balance': 'KRW 잔고',
        'btc_krw_price': 'BTC 가격',
        'total_value': '총 자산',
        'reason': '이유'
    }
    
    display_df = display_df.rename(columns=display_columns)
    
    # 최근 20개 기록만 표시
    st.dataframe(
        display_df[list(display_columns.values())].head(20),
        use_container_width=True,
        hide_index=True
    )
    
    # 새로고침 버튼
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col2:
        if st.button("🔄 새로고침", use_container_width=True):
            st.rerun()
    
    # 자동 새로고침 설정
    st.markdown("""
    <script>
    setTimeout(function(){
        window.location.reload(1);
    }, 60000); // 60초마다 자동 새로고침
    </script>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
