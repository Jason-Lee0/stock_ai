import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import twstock
import re
import json
import time
import datetime
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 1. 系統初始化 ---
st.set_page_config(page_title="AI 飆股診斷 v6.2", layout="wide", page_icon="🛡️")

for key in ['v62_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心運算引擎 (強韌格式化版本) ---

def get_taiwan_stock_tickers():
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if any(x in info.type for x in ["權證", "ETF", "受益"]): continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return sorted(list(set(taiwan_tickers)))

def check_breakout_v62(ticker, g_limit, v_limit, min_v, bias_range, use_bias):
    """強韌版偵測引擎"""
    try:
        # 採用純淨版 download 抓取
        df = yf.download(ticker, period="450d", interval="1d", progress=False)
        
        if df is None or df.empty or len(df) < 245: return None
        
        # 萬用索引清洗 (處理 MultiIndex)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        # 清除 NaN 與無交易量日子
        df = df.dropna(subset=['Close'])
        df = df[df['Volume'] > 0]
        
        if len(df) < 240: return None
        
        last = df.iloc[-1]
        if (last['Volume'] / 1000) < min_v: return None
        
        close = df['Close']
        ma5, ma10, ma20 = close.rolling(5).mean().iloc[-1], close.rolling(10).mean().iloc[-1], close.rolling(20).mean().iloc[-1]
        ma60, ma240 = close.rolling(60).mean().iloc[-1], close.rolling(240).mean().iloc[-1]
        
        ma_bias = round(((ma60 / ma240) - 1) * 100, 2)
        if use_bias and not (bias_range[0] <= ma_bias <= bias_range[1]): return None
        
        ma_list = [float(ma5), float(ma10), float(ma20)]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        v_ratio = round(last['Volume'] / df['Volume'].rolling(20).mean().iloc[-1], 2)
        
        if gap <= g_limit and v_ratio <= v_limit:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "現價": round(float(last['Close']), 2),
                "糾結(%)": gap, "位階(%)": ma_bias, "量比": v_ratio,
                "屬性": "📈 多頭" if ma_bias > 0 else "🩹 底部"
            }
    except: return None

# --- 3. 診斷功能 ---

def get_historical_theme_ai(ticker, name):
    try:
        df = yf.download(ticker, period="6mo", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        prompt = f"分析台股 {name}({ticker})。在 {max_day.strftime('%Y-%m-%d')} 大漲原因，限40字。"
        return f"📅 {max_day.strftime('%Y-%m-%d')} 考古：{model.generate_content(prompt).text}"
    except: return "AI 考古暫時不可用"

@st.dialog("🚀 AI 飆股診斷室", width="large")
def show_diagnosis(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 考古中..."):
        st.info(get_historical_theme_ai(ticker, name))
    
    # 繪圖也採用強韌讀取
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
    for p in [5, 20, 60, 240]: df[f'MA{p}'] = df['Close'].rolling(p).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    for ma, col in zip(['MA5','MA20','MA60','MA240'], ['white','yellow','orange','purple']):
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=col, width=1.5)), row=1, col=1)
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=10, b=5))
    st.plotly_chart(fig, width='stretch')
    if st.button("關閉", width='stretch'): st.rerun()

# --- 4. 介面 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報", "📈 回測", "📚 庫", "⚡ 偵測器"])

with tab4:
    st.subheader("⚡ 數據健康檢查")
    with st.container(border=True):
        c_status, c_info, c_btn = st.columns([1, 2, 1])
        try:
            # 採用測試成功的強韌格式
            t_df = yf.download("2330.TW", period="3d", progress=False)
            if t_df is not None and not t_df.empty:
                if isinstance(t_df.columns, pd.MultiIndex):
                    t_df.columns = [col[0] if isinstance(col, tuple) else col for col in t_df.columns]
                v_df = t_df[t_df['Volume'] > 0].dropna()
                c_status.metric("數據狀態", "✅ 正常")
                c_info.write(f"📅 **基準日**：`{v_df.index[-1].strftime('%Y-%m-%d')}`")
            else: c_status.metric("數據狀態", "❌ 異常")
        except: c_status.metric("數據狀態", "🚫 錯誤")
        if c_btn.button("🔄 重新測試", width='stretch'): st.rerun()

    st.write("---")
    mode = st.segmented_control("範圍", ["全台股", "資料庫"], default="全台股")
    with st.expander("🛠️ 調整參數"):
        use_bias = st.toggle("位階過濾", value=True)
        bias_range = st.slider("位階 (%)", -30, 60, (-10, 25), disabled=not use_bias)
        g_limit = st.slider("糾結度 (%)", 1.0, 10.0, 4.0)
        v_limit = st.slider("量比", 0.1, 2.5, 0.8)
        min_v = st.number_input("最低成交量 (張)", value=300)

    if st.button("🏁 執行深度掃描", width='stretch', type="primary"):
        all_tickers = get_taiwan_stock_tickers()
        search_list = all_tickers if mode == "全台股" else [] # 這裡可依需求加入資料庫邏輯
        
        hits = []
        prog, status = st.progress(0), st.empty()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(check_breakout_v62, s, g_limit, v_limit, min_v, bias_range, use_bias): s for s in search_list}
            for i, f in enumerate(concurrent.futures.as_completed(futures)):
                res = f.result()
                if res: hits.append(res)
                if i % 15 == 0:
                    prog.progress((i+1)/len(search_list))
                    status.text(f"掃描中: {i+1}/{len(search_list)}")
        st.session_state.v62_results = pd.DataFrame(hits)
        status.success(f"⚡ 完成！發現 {len(hits)} 檔。")

    if st.session_state.v62_results is not None and not st.session_state.v62_results.empty:
        event = st.dataframe(st.session_state.v62_results, on_select="rerun", selection_mode="single-row", hide_index=True, width='stretch')
        if event.selection.rows:
            target = st.session_state.v62_results.iloc[event.selection.rows[0]]
            show_diagnosis(target['代號'], target['名稱'])
