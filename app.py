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
st.set_page_config(page_title="AI 飆股診斷 v5.1", layout="wide", page_icon="🛡️")

if 'v51_results' not in st.session_state:
    st.session_state.v51_results = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心運算函式 ---

def get_taiwan_stock_tickers():
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if "權證" in info.type or "ETF" in info.type: continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return list(set(taiwan_tickers))

def get_historical_theme_ai(ticker, name):
    try:
        df = yf.Ticker(ticker).history(period="6mo")
        if df.empty: return "無數據"
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        date_str = max_day.strftime('%Y-%m-%d')
        prompt = f"分析台股 {name}({ticker})。該股在 {date_str} 前後大幅上漲。請簡述當時爆發利多，限 40 字。"
        return f"📅 {date_str} 考古：{model.generate_content(prompt).text}"
    except: return "考古失敗"

def check_breakout_v51(ticker, g_limit, v_limit, min_v, bias_range, use_bias):
    try:
        # 抓取稍長一點的區間，確保能涵蓋足夠交易日
        df = yf.Ticker(ticker).history(period="450d")
        if df.empty or len(df) < 250: return None
        
        # 【防呆關鍵】：過濾掉非交易日或無效數據，確保最後一筆是真實收盤
        df = df[df['Close'] > 0] 
        df = df.dropna(subset=['Close'])
        
        # 取最後一筆作為基準
        last = df.iloc[-1]
        
        # 1. 基本流動性
        if (last['Volume'] / 1000) < min_v: return None
        
        # 2. 計算均線 (全部基於真實交易日)
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma10 = df['Close'].rolling(10).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        ma240 = df['Close'].rolling(240).mean().iloc[-1]
        
        # 3. 季年位階過濾
        ma_bias = round(((ma60 / ma240) - 1) * 100, 2)
        if use_bias:
            if not (bias_range[0] <= ma_bias <= bias_range[1]): return None
            
        # 4. 短線糾結 (5/10/20MA)
        ma_list = [ma5, ma10, ma20]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        
        # 5. 量比 (與真實的前20個交易日均量比較)
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = round(last['Volume'] / vol_avg20, 2)
        
        # 判定日期標籤
        last_date = df.index[-1].strftime('%Y-%m-%d')
        
        if gap <= g_limit and v_ratio <= v_limit:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "類股": info.category if info else "其他", "現價": round(last['Close'], 2),
                "短線糾結(%)": gap, "季年位階(%)": ma_bias, "量比": v_ratio,
                "基準日期": last_date,
                "位階屬性": "📈 多頭" if ma_bias > 0 else "🩹 底部"
            }
    except: return None

def plot_v50(ticker):
    df = yf.Ticker(ticker).history(period="300d")
    for p in [5, 20, 60, 240]: df[f'MA{p}'] = df['Close'].rolling(p).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    for ma, col in zip(['MA5','MA20','MA60','MA240'], ['white','yellow','orange','purple']):
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=col, width=1.5)), row=1, col=1)
    v_cols = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=v_cols), row=2, col=1)
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=10, b=5))
    return fig

@st.dialog("🚀 AI 飆股診斷室", width="large")
def show_stock_v50(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 考古中..."):
        st.info(get_historical_theme_ai(ticker, name))
    chart = plot_v50(ticker)
    if chart: st.plotly_chart(chart, use_container_width=True)
    if st.button("關閉", use_container_width=True): st.rerun()

# --- 3. UI 分頁 ---
tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📅 歷史表現", "📚 雲端庫", "⚡ 飆股偵測器"])

try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

# 週報提取、歷史表現、雲端庫內容省略 (保持 v4.9 邏輯)

with tab4:
    st.subheader("⚡ 飆股 DNA 高階偵測 (穩定數據版)")
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        mode = st.radio("掃描範圍", ["資料庫標的", "全台股"], horizontal=True)
        st.write("")
        use_bias = st.checkbox("啟用季年線位階過濾", value=True)
        bias_range = st.slider("中長線位階 (季年線乖離%)", -30, 60, (-10, 25), disabled=not use_bias)
    with c2:
        g_limit = st.slider("短線糾結度 (5/10/20MA%)", 1.0, 7.0, 3.5)
        min_v = st.number_input("最低成交量 (張)", value=300)
    with c3:
        v_limit = st.slider("成交量比 (窒息量)", 0.1, 1.5, 0.75)

    if st.button("🏁 啟動深度掃描", use_container_width=True):
        # 題材對照邏輯...
        search_list = get_taiwan_stock_tickers() if mode == "全台股" else ... 
        
        if search_list:
            hits = []
            prog, status = st.progress(0), st.empty()
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
                futures = {ex.submit(check_breakout_v51, s, g_limit, v_limit, min_v, bias_range, use_bias): s for s in search_list}
                for i, f in enumerate(concurrent.futures.as_completed(futures)):
                    res = f.result()
                    if res: hits.append(res)
                    if i % 40 == 0: prog.progress((i+1)/len(search_list)); status.text(f"掃描中: {i+1}/{len(search_list)}")
            
            st.session_state.v51_results = pd.DataFrame(hits) if hits else pd.DataFrame()
            status.success(f"⚡ 完成！基準交易日為: {hits[0]['基準日期'] if hits else '無資料'}")

    if st.session_state.v51_results is not None and not st.session_state.v51_results.empty:
        st.write("### 🔍 偵測結果")
        event = st.dataframe(
            st.session_state.v51_results, width='stretch', on_select="rerun", selection_mode="single-row", hide_index=True,
            column_config={
                "季年位階(%)": st.column_config.NumberColumn("季年乖離", format="%.1f%%"),
                "短線糾結(%)": st.column_config.NumberColumn("短線壓縮", format="%.2f%%"),
                "位階屬性": st.column_config.BadgeColumn("屬性")
            }
        )
        if event.selection.rows:
            row = st.session_state.v51_results.iloc[event.selection.rows[0]]
            show_stock_v50(row['代號'], row['名稱'])
