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
st.set_page_config(page_title="AI 飆股診斷 v4.6", layout="wide", page_icon="🛡️")

# 初始化所有記憶變數
for key in ['v45_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state:
        st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def get_taiwan_stock_tickers():
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if "權證" in info.type or "ETF" in info.type: continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return list(set(taiwan_tickers))

def check_breakout_dna_stable(ticker, g_limit, v_limit, min_vol_lots):
    today = datetime.date.today()
    end_date = today - datetime.timedelta(days=today.weekday() - 4) if today.weekday() >= 5 else today
    start_date = end_date - datetime.timedelta(days=400)
    try:
        df = yf.Ticker(ticker).history(start=start_date, end=end_date)
        if df.empty or len(df) < 245: return None
        last = df.iloc[-1]
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        if (last['Volume'] / 1000) < min_vol_lots: return None
        
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA240'] = df['Close'].rolling(240).mean()
        
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
        
        ma_list = [df['MA5'].iloc[-1], df['MA10'].iloc[-1], df['MA20'].iloc[-1]]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        v_ratio = round(last['Volume'] / vol_avg20, 2)
        
        if gap <= g_limit and v_ratio <= v_limit and last['Close'] > df['MA60'].iloc[-1]:
            return {
                "代號": ticker, "現價": round(last['Close'], 2), "糾結(%)": gap, "量比": v_ratio,
                "長線屬性": "🚀 長線無壓" if last['Close'] > df['MA240'].iloc[-1] else "🩹 補漲股",
                "動能": "🔥 轉強" if macd_hist.iloc[-1] > macd_hist.iloc[-2] else "⏳ 整理"
            }
    except: return None

def plot_interactive_chart(ticker):
    try:
        df = yf.Ticker(ticker).history(period="300d")
        for p in [5, 20, 60, 240]: df[f'MA{p}'] = df['Close'].rolling(p).mean()
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
        for ma, col in zip(['MA5','MA20','MA60','MA240'], ['white','yellow','orange','purple']):
            fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=col, width=1.2)), row=1, col=1)
        v_colors = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=v_colors), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10))
        return fig
    except: return None

@st.dialog("📈 飆股 DNA 深度診斷", width="large")
def show_stock_dialog(ticker):
    st.write(f"### 分析標的：{ticker}")
    chart = plot_interactive_chart(ticker)
    if chart: st.plotly_chart(chart, width='stretch')
    if st.button("關閉診斷", width='stretch'): st.rerun()

# --- 3. 讀取資料庫 ---
try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

# --- 4. 分頁 UI ---
tab1, tab2, tab3, tab4 = st.tabs(["📄 週報解析", "📅 歷史戰績", "📚 雲端清單", "⚡ 飆股偵測器"])

with tab1:
    st.subheader("📄 AI 投顧週報提取")
    uploaded_file = st.file_uploader("上傳投顧週報 PDF", type="pdf")
    if uploaded_file:
        if st.button("🚀 啟動 AI 標的提取"):
            with st.spinner('Gemini 正在閱讀週報...'):
                reader = PdfReader(uploaded_file)
                full_text = "".join([p.extract_text() for p in reader.pages])
                prompt = f"請將週報內容轉為 JSON 列表，包含：題材, 原因, 標的(4位代碼+名稱)。文字：{full_text[:8000]}"
                response = model.generate_content(prompt)
                st.session_state.raw_json = response.text
                st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")

    if st.session_state.raw_json:
        st.code(st.session_state.raw_json, language='json')
        if st.button("📥 存入 Google Sheets"):
            try:
                clean_json = st.session_state.raw_json.replace('```json', '').replace('```', '').strip()
                new_df = pd.DataFrame(json.loads(clean_json))
                new_df['日期'] = st.session_state.rep_date
                updated_db = pd.concat([db, new_df], ignore_index=True)
                conn.update(worksheet="Sheet1", data=updated_db)
                st.success("成功存入雲端！")
            except Exception as e: st.error(f"存檔失敗: {e}")

with tab2:
    st.subheader("📅 歷史題材回測")
    if st.button("📈 計算標的漲跌幅 (最近 20 筆)"):
        with st.spinner('抓取歷史價格中...'):
            bt_list = []
            recent_db = db.drop_duplicates(subset=['標的']).tail(20)
            for _, row in recent_db.iterrows():
                sid = re.search(r'\b\d{4}\b', str(row['標的']))
                if sid:
                    sid = sid.group(0)
                    s = f"{sid}.TW" if int(sid)<9000 else f"{sid}.TWO"
                    h = yf.Ticker(s).history(start=row['日期'])
                    if not h.empty:
                        p_start, p_now = h.iloc[0]['Close'], h.iloc[-1]['Close']
                        bt_list.append({"日期": row['日期'], "標的": row['標的'], "當初價": round(p_start,2), "現價": round(p_now,2), "漲跌(%)": round(((p_now/p_start)-1)*100,2)})
            st.session_state.backtest_df = pd.DataFrame(bt_list)
    
    if st.session_state.backtest_df is not None:
        st.dataframe(st.session_state.backtest_df.style.applymap(lambda x: 'color:red' if x > 0 else 'color:green', subset=['漲跌(%)']), width='stretch')

with tab3:
    st.subheader("📚 雲端監控題材清單")
    search_q = st.text_input("🔍 搜尋關鍵字")
    if not db.empty:
        filtered_db = db[db.astype(str).apply(lambda x: x.str.contains(search_q)).any(axis=1)]
        st.dataframe(filtered_db, width='stretch')

with tab4:
    st.subheader("⚡ 飆股 DNA 大數據掃描")
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1: scan_mode = st.radio("範圍", ["資料庫標的", "全台股"], horizontal=True)
    with c2: g_limit = st.slider("糾結度 (%)", 1.0, 5.0, 3.5)
    with c3: min_v = st.slider("最低成交張數", 100, 2000, 500, step=100)
    v_limit = st.slider("成交量窒息門檻 (量比)", 0.1, 1.2, 0.75)

    if st.button("🏁 開始執行高速偵測", width='stretch'):
        search_list = []
        if scan_mode == "資料庫標的":
            sids = []
            for s in db['標的'].astype(str): sids.extend(re.findall(r'\b\d{4}\b', s))
            search_list = [f"{s}.TW" if int(s)<9000 else f"{s}.TWO" for s in list(set(sids))]
        else:
            with st.spinner("獲取台股清單..."): search_list = get_taiwan_stock_tickers()

        if search_list:
            hits = []
            prog, status = st.progress(0), st.empty()
            start_t = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
                futures = {ex.submit(check_breakout_dna_stable, s, g_limit, v_limit, min_v): s for s in search_list}
                for i, f in enumerate(concurrent.futures.as_completed(futures)):
                    res = f.result()
                    if res: hits.append(res)
                    if i % 50 == 0: prog.progress((i+1)/len(search_list)); status.text(f"掃描: {i+1}/{len(search_list)}")
            st.session_state.v45_results = pd.DataFrame(hits) if hits else pd.DataFrame()
            status.success(f"⚡ 完成！耗時: {int(time.time()-start_t)}秒")

    if st.session_state.v45_results is not None and not st.session_state.v45_results.empty:
        st.write("### 🔍 偵測清單 (點選橫列彈出 K 線)")
        event = st.dataframe(st.session_state.v45_results, width='stretch', on_select="rerun", selection_mode="single-row", hide_index=True)
        if event.selection.rows:
            show_stock_dialog(st.session_state.v45_results.iloc[event.selection.rows[0]]['代號'])
        st.download_button("📥 下載清單", st.session_state.v45_results.to_csv(index=False).encode('utf-8-sig'), "hits.csv", "text/csv")
