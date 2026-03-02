import streamlit as st
import google.generativeai as genai
import pandas as pd
import yfinance as yf
import twstock
import re
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime

# --- 1. 系統初始化 ---
st.set_page_config(page_title="AI 飆股診斷 v7.0", layout="wide", page_icon="🛡️")

if 'v70_results' not in st.session_state: 
    st.session_state.v70_results = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
except:
    st.error("⚠️ 請在 Secrets 中設定 GEMINI_KEY 才能使用 AI 功能。")

# --- 2. 核心篩選引擎 (成交量雙重過濾) ---

def check_squeeze_v70(ticker, gap_threshold, vol_threshold, min_v):
    try:
        # 抓取 450 天數據確保長線均線 (240MA) 穩定
        df = yf.download(ticker, period="450d", interval="1d", progress=False)
        if df is None or df.empty or len(df) < 250: return None
        
        # 處理 yfinance 可能的 MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        df = df.dropna(subset=['Close'])
        df = df[df['Volume'] > 0]
        
        last = df.iloc[-1]
        vol_ma20 = df['Volume'].rolling(20).mean().iloc[-1]
        
        # --- [成交量限制] ---
        current_shares = last['Volume'] / 1000
        # 門檻 1: 基礎日張數 (例如 > 300張)
        if current_shares < min_v: return None
        # 門檻 2: 窒息量比例 (例如 < 0.6 倍均量)
        current_vol_ratio = last['Volume'] / vol_ma20
        if current_vol_ratio > vol_threshold: return None
        
        # --- [均線糾結限制] ---
        close = df['Close']
        ma_list = [
            close.rolling(5).mean().iloc[-1],
            close.rolling(10).mean().iloc[-1],
            close.rolling(20).mean().iloc[-1],
            close.rolling(60).mean().iloc[-1],
            close.rolling(120).mean().iloc[-1],
            close.rolling(240).mean().iloc[-1]
        ]
        
        ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
        if ma_gap > gap_threshold: return None
        
        return {
            "代號": ticker,
            "名稱": twstock.codes.get(re.search(r'\d{4}', ticker).group(0)).name if twstock.codes.get(re.search(r'\d{4}', ticker).group(0)) else "未知",
            "現價": round(float(last['Close']), 2),
            "糾結度(%)": round(ma_gap, 2),
            "量縮比": round(current_vol_ratio, 2),
            "今日張數": int(current_shares)
        }
    except: return None

# --- 3. K 線診斷視窗 (含成交量) ---

@st.dialog("📈 標的診斷報告", width="large")
def show_details_v70(ticker, name):
    st.write(f"### {name} ({ticker})")
    
    with st.spinner("正在生成 K 線圖..."):
        df = yf.download(ticker, period="300d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
            
        # 計算均線
        df['MA5'] = df['Close'].rolling(5).mean()     # 黃
        df['MA10'] = df['Close'].rolling(10).mean()   # 藍
        df['MA20'] = df['Close'].rolling(20).mean()   # 紫
        df['MA60'] = df['Close'].rolling(60).mean()   # 綠
        df['MA120'] = df['Close'].rolling(120).mean() # 紅
        df['MA240'] = df['Close'].rolling(240).mean() # 橘

        # 建立雙軸圖表 (Row 1: K線, Row 2: 成交量)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                           vertical_spacing=0.1, 
                           row_heights=[0.7, 0.3])
        
        # 1. 繪製 K 線
        fig.add_trace(go.Candlestick(
            x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], 
            name="K線", increasing_line_color='#FF4136', decreasing_line_color='#3D9970'
        ), row=1, col=1)
        
        # 2. 繪製六色均線
        ma_colors = {
            'MA5': 'yellow', 'MA10': '#00BFFF', 'MA20': '#DA70D6', 
            'MA60': '#32CD32', 'MA120': '#FF0000', 'MA240': '#FF8C00'
        }
        for ma, color in ma_colors.items():
            fig.add_trace(go.Scatter(
                x=df.index, y=df[ma], name=ma, line=dict(color=color, width=1.2)
            ), row=1, col=1)
        
        # 3. 繪製成交量 (根據漲跌變色)
        colors = ['#FF4136' if close >= open else '#3D9970' for close, open in zip(df['Close'], df['Open'])]
        fig.add_trace(go.Bar(
            x=df.index, y=df['Volume'], name="成交量", marker_color=colors, opacity=0.8
        ), row=2, col=1)
        
        fig.update_layout(
            template="plotly_dark", height=600, 
            xaxis_rangeslider_visible=False,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, width='stretch')
        
        if st.button("✅ 關閉診斷", width='stretch'):
            st.rerun()

# --- 4. 主介面設計 ---

st.title("🛡️ 潛伏飆股偵測器 v7.0")
st.caption("策略邏輯：量縮窒息 + 均線六線合一 (潛伏吸籌模式)")

with st.sidebar:
    st.header("⚙️ 篩選參數")
    gap_p = st.slider("均線糾結門檻 (%)", 1.0, 10.0, 5.0, help="數值越小，均線越黏合")
    vol_p = st.slider("量縮比 (今日/20日均量)", 0.1, 1.0, 0.6, help="建議 0.6 以下為極致量縮")
    min_v_p = st.number_input("最低日成交量 (張)", value=300)
    
    st.divider()
    scan_btn = st.button("🚀 開始掃描全台股", width='stretch', type="primary")

# 執行掃描邏輯
if scan_btn:
    all_codes = twstock.codes
    # 僅選取上市櫃股票，排除 ETF 與權證
    all_tickers = [f"{c}.TW" if i.market=="上市" else f"{c}.TWO" 
                   for c, i in all_codes.items() 
                   if c.isdigit() and len(c)==4 and "ETF" not in i.type]
    
    hits = []
    prog = st.progress(0)
    status = st.empty()
    
    # 使用多執行緒加速掃描
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_squeeze_v70, t, gap_p, vol_p, min_v_p): t for t in all_tickers}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            res = future.result()
            if res: hits.append(res)
            if i % 25 == 0:
                prog.progress((i+1)/len(all_tickers))
                status.text(f"🔍 正在掃描台股: {i+1}/{len(all_tickers)}")
    
    st.session_state.v70_results = pd.DataFrame(hits)
    status.success(f"✅ 掃描完成！發現 {len(hits)} 檔符合條件標的。")

# 顯示結果列表
if st.session_state.v70_results is not None and not st.session_state.v70_results.empty:
    st.write("---")
    st.info("💡 點擊下方表格標的，即可查看 **六色均線** 與 **成交量圖**。")
    
    # 表格顯示與點擊監聽
    event = st.dataframe(
        st.session_state.v70_results.sort_values("糾結度(%)"), 
        on_select="rerun", 
        selection_mode="single-row", 
        hide_index=True, 
        width='stretch'
    )
    
    if event.selection.rows:
        selected_idx = event.selection.rows[0]
        # 注意：Dataframe 排序後 index 會亂，需用 iloc 抓取正確位置
        target = st.session_state.v70_results.sort_values("糾結度(%)").iloc[selected_idx]
        show_details_v70(target['代號'], target['名稱'])
