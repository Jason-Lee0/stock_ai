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
st.set_page_config(page_title="AI 飆股診斷 v4.5", layout="wide", page_icon="🛡️")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def get_taiwan_stock_tickers():
    """獲取精確台股清單"""
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if "權證" in info.type or "ETF" in info.type: continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return list(set(taiwan_tickers))

def check_breakout_dna_stable(ticker, g_limit, v_limit, min_vol_lots=500):
    """高穩定偵測引擎 (含流動性過濾)"""
    today = datetime.date.today()
    # 假日鎖定邏輯
    if today.weekday() >= 5:
        end_date = today - datetime.timedelta(days=today.weekday() - 4)
    else:
        end_date = today
    start_date = end_date - datetime.timedelta(days=400)

    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(start=start_date, end=end_date)
            if df.empty or len(df) < 245: return None
            
            last = df.iloc[-1]
            vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
            
            # 流動性過濾 (張數 = 股數/1000)
            current_vol_lots = last['Volume'] / 1000
            if current_vol_lots < min_vol_lots: return None

            # 指標計算
            df['MA5'] = df['Close'].rolling(5).mean()
            df['MA10'] = df['Close'].rolling(10).mean()
            df['MA20'] = df['Close'].rolling(20).mean()
            df['MA60'] = df['Close'].rolling(60).mean()
            df['MA240'] = df['Close'].rolling(240).mean()
            
            exp1 = df['Close'].ewm(span=12, adjust=False).mean()
            exp2 = df['Close'].ewm(span=26, adjust=False).mean()
            df['DIF'] = exp1 - exp2
            df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
            df['MACD_Hist'] = df['DIF'] - df['DEA']
            
            prev = df.iloc[-2]
            ma_list = [last['MA5'], last['MA10'], last['MA20']]
            gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
            v_ratio = round(last['Volume'] / vol_avg20, 2) if vol_avg20 > 0 else 1
            
            is_ma60_up = last['MA60'] > df['MA60'].iloc[-5]
            if gap <= g_limit and v_ratio <= v_limit and last['Close'] > last['MA60'] and is_ma60_up:
                return {
                    "代號": ticker,
                    "現價": round(last['Close'], 2),
                    "糾結(%)": gap,
                    "量比": v_ratio,
                    "長線屬性": "🚀 長線無壓" if last['Close'] > last['MA240'] else "🩹 補漲股",
                    "動能": "🔥 轉強" if last['MACD_Hist'] > prev['MACD_Hist'] else "⏳ 整理"
                }
            return None
        except:
            time.sleep(0.5)
    return None

def plot_interactive_chart(ticker):
    """繪製 K 線診斷圖"""
    try:
        df = yf.Ticker(ticker).history(period="300d")
        for ma, p in zip(['MA5','MA20','MA60','MA240'], [5,20,60,240]):
            df[ma] = df['Close'].rolling(p).mean()
        
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
        for ma, color in zip(['MA5', 'MA20', 'MA60', 'MA240'], ['white', 'yellow', 'orange', 'purple']):
            fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=color, width=1.5)), row=1, col=1)
        
        colors = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=colors), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=10, b=5))
        return fig
    except: return None

# --- 3. 彈出診斷視窗 ---
@st.dialog("📈 飆股 DNA 深度診斷", width="large")
def show_stock_dialog(ticker):
    st.write(f"### 正在診斷：{ticker}")
    fig = plot_interactive_chart(ticker)
    if fig:
        st.plotly_chart(fig, width='stretch')
    else:
        st.error("無法載入圖表")
    if st.button("關閉視窗", width='stretch'):
        st.rerun()

# --- 4. UI 介面 ---
tab1, tab2, tab3, tab4 = st.tabs(["📄 週報解析", "📅 歷史診斷", "📚 資料庫明細", "⚡ 飆股偵測器"])

try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame()

# (Tab 1, 2, 3 邏輯依此類推，此處重點在 Tab 4 的整合)

with tab4:
    st.subheader("⚡ 飆股 DNA 大數據掃描")
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        mode = st.radio("範圍", ["資料庫題材股", "全台股"], horizontal=True)
    with col2:
        g_limit = st.slider("均線糾結 (%)", 1.0, 5.0, 3.5)
    with col3:
        min_v = st.slider("最低成交張數", 100, 2000, 500, step=100)
    
    v_limit = st.slider("量比門檻 (窒息量)", 0.1, 1.2, 0.75)

    if st.button("🏁 開始執行高速掃描", width='stretch'):
        search_list = []
        if mode == "資料庫題材股":
            sids = []
            for s in db['標的'].astype(str): sids.extend(re.findall(r'\b\d{4}\b', s))
            search_list = [f"{s}.TW" if int(s)<9000 else f"{s}.TWO" for s in list(set(sids))]
        else:
            with st.spinner("同步台股代碼..."):
                search_list = get_taiwan_stock_tickers()

        if search_list:
            hits = []
            progress = st.progress(0)
            status = st.empty()
            start_time = time.time()

            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                future_to_sid = {executor.submit(check_breakout_dna_stable, sid, g_limit, v_limit, min_v): sid for sid in search_list}
                for i, future in enumerate(concurrent.futures.as_completed(future_to_sid)):
                    res = future.result()
                    if res: hits.append(res)
                    if i % 50 == 0:
                        progress.progress((i + 1) / len(search_list))
                        status.text(f"已掃描: {i+1} / {len(search_list)}")

            status.success(f"⚡ 掃描完成！耗時: {int(time.time()-start_time)} 秒")
            st.session_state.v45_hits = pd.DataFrame(hits) if hits else None

    # 顯示結果與觸發彈窗
    if 'v45_hits' in st.session_state and st.session_state.v45_hits is not None:
        st.write("### 🔍 偵測結果 (點選橫列彈出 K 線)")
        
        event = st.dataframe(
            st.session_state.v45_hits,
            width='stretch',
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
            column_config={
                "糾結(%)": st.column_config.ProgressColumn("糾結(%)", min_value=0, max_value=5, format="%.2f%%"),
                "量比": st.column_config.NumberColumn("量比", format="%.2f x"),
            }
        )

        if event.selection.rows:
            selected_row = event.selection.rows[0]
            selected_sid = st.session_state.v45_hits.iloc[selected_row]['代號']
            show_stock_dialog(selected_sid)

        csv = st.session_state.v45_hits.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載清單", csv, "hits.csv", "text/csv")
