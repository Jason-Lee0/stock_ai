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
st.set_page_config(page_title="AI 飆股診斷 v5.2", layout="wide", page_icon="🛡️")

# 初始化記憶體
for key in ['v52_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 穩定版核心函式 ---

def get_taiwan_stock_tickers():
    """獲取台股代碼 (不含 ETF 與權證)"""
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if any(x in info.type for x in ["權證", "ETF", "受益證券"]): continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return sorted(list(set(taiwan_tickers)))

def check_breakout_v52(ticker, g_limit, v_limit, min_v, bias_range, use_bias):
    """深度偵測：短線糾結 + 季年位階 + 數據防抖"""
    try:
        # 下載數據 (鎖定較長區間以計算 MA240)
        df = yf.download(ticker, period="400d", progress=False, show_errors=False)
        if df.empty or len(df) < 240: return None
        
        # 修正 yfinance MultiIndex 問題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # 數據清洗：確保有成交量且非空值
        df = df[df['Volume'] > 0].dropna()
        if len(df) < 240: return None
        
        last = df.iloc[-1]
        
        # 1. 基礎張數門檻
        if (last['Volume'] / 1000) < min_v: return None
        
        # 2. 計算均線
        close = df['Close']
        ma5, ma10, ma20 = close.rolling(5).mean().iloc[-1], close.rolling(10).mean().iloc[-1], close.rolling(20).mean().iloc[-1]
        ma60, ma240 = close.rolling(60).mean().iloc[-1], close.rolling(240).mean().iloc[-1]
        
        # 3. 季年線位階過濾 (MA60 vs MA240)
        ma_bias = round(((ma60 / ma240) - 1) * 100, 2)
        if use_bias:
            if not (bias_range[0] <= ma_bias <= bias_range[1]): return None
        
        # 4. 短線糾結度 (5, 10, 20MA)
        ma_list = [float(ma5), float(ma10), float(ma20)]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        
        # 5. 量比 (窒息量判斷)
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = round(last['Volume'] / vol_avg20, 2)
        
        if gap <= g_limit and v_ratio <= v_limit:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "類股": info.category if info else "其他", "現價": round(float(last['Close']), 2),
                "短線糾結(%)": gap, "季年位階(%)": ma_bias, "量比": v_ratio,
                "屬性": "📈 多頭" if ma_bias > 0 else "🩹 底部",
                "最後交易日": df.index[-1].strftime('%Y-%m-%d')
            }
    except: return None
    return None

def get_historical_theme_ai(ticker, name):
    try:
        df = yf.download(ticker, period="6mo", progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        date_str = max_day.strftime('%Y-%m-%d')
        prompt = f"分析台股 {name}({ticker})。該股在 {date_str} 前後大幅上漲。請簡述當時爆發原因(40字內)。"
        return f"📅 {date_str} 考古：{model.generate_content(prompt).text}"
    except: return "考古失敗"

def plot_v52(ticker):
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
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
def show_stock_v52(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 考古中..."):
        st.info(get_historical_theme_ai(ticker, name))
    chart = plot_v52(ticker)
    if chart: st.plotly_chart(chart, use_container_width=True)
    if st.button("關閉", use_container_width=True): st.rerun()

# --- 3. UI 介面 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📅 歷史表現", "📚 雲端資料庫", "⚡ 飆股偵測器"])

try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

with tab1:
    st.subheader("📄 AI 投顧週報標的提取")
    pdf = st.file_uploader("上傳 PDF", type="pdf")
    if pdf and st.button("🚀 啟動解析"):
        reader = PdfReader(pdf)
        text = "".join([p.extract_text() for p in reader.pages])
        res = model.generate_content(f"轉為 JSON (題材, 原因, 標的): {text[:8000]}").text
        st.session_state.raw_json = res
        st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")
    if st.session_state.raw_json:
        st.code(st.session_state.raw_json)
        if st.button("📥 存入雲端"):
            clean = st.session_state.raw_json.replace('```json', '').replace('```', '').strip()
            new = pd.DataFrame(json.loads(clean))
            new['日期'] = st.session_state.rep_date
            conn.update(worksheet="Sheet1", data=pd.concat([db, new], ignore_index=True))
            st.success("存檔成功")

with tab2:
    if st.button("📈 計算回測"):
        bt = []
        for _, r in db.tail(10).iterrows():
            m = re.search(r'\d{4}', str(r['標的']))
            if m:
                s = f"{m.group(0)}.TW"
                h = yf.download(s, start=r['日期'], progress=False)
                if not h.empty:
                    p0, pn = h['Close'].iloc[0], h['Close'].iloc[-1]
                    bt.append({"標的": r['標的'], "漲跌%": round(((pn/p0)-1)*100, 2)})
        st.session_state.backtest_df = pd.DataFrame(bt)
    if st.session_state.backtest_df is not None: st.table(st.session_state.backtest_df)

with tab3:
    st.subheader("📚 監控庫")
    st.dataframe(db, width='stretch')

with tab4:
    st.subheader("⚡ 飆股 DNA 高階偵測 (穩定版)")
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        mode = st.radio("範圍", ["全台股", "資料庫標的"], horizontal=True)
        use_bias = st.checkbox("啟用季年位階過濾", value=True)
        bias_range = st.slider("季年乖離 (%)", -30, 60, (-10, 25), disabled=not use_bias)
    with c2:
        g_limit = st.slider("短線糾結度 (%)", 1.0, 7.0, 3.5)
        min_v = st.number_input("最低成交量 (張)", value=500)
    with c3:
        v_limit = st.slider("成交量比 (窒息量)", 0.1, 2.0, 0.75)

    if st.button("🏁 啟動深度掃描", use_container_width=True):
        all_tickers = get_taiwan_stock_tickers()
        if mode == "資料庫標的":
            search_list = [t for t in all_tickers if any(sid in t for sid in db['標的'].astype(str))]
        else:
            search_list = all_tickers
        
        hits = []
        prog, status = st.progress(0), st.empty()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(check_breakout_v52, s, g_limit, v_limit, min_v, bias_range, use_bias): s for s in search_list}
            for i, f in enumerate(concurrent.futures.as_completed(futures)):
                res = f.result()
                if res: hits.append(res)
                if i % 20 == 0:
                    prog.progress((i+1)/len(search_list))
                    status.text(f"掃描中... 已發現 {len(hits)} 檔符合標的")
        
        st.session_state.v52_results = pd.DataFrame(hits)
        status.success(f"⚡ 完成！發現 {len(hits)} 檔。")

    if st.session_state.v52_results is not None and not st.session_state.v52_results.empty:
        event = st.dataframe(
            st.session_state.v52_results, on_select="rerun", selection_mode="single-row", hide_index=True,
            column_config={"短線糾結(%)": st.column_config.NumberColumn(format="%.2f%%")}
        )
        if event.selection.rows:
            row = st.session_state.v52_results.iloc[event.selection.rows[0]]
            show_stock_v52(row['代號'], row['名稱'])
