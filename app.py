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
st.set_page_config(page_title="AI 飆股診斷 v5.3", layout="wide", page_icon="🛡️")

# 初始化 Session State
for key in ['v53_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心運算函式 ---

def get_taiwan_stock_tickers():
    """獲取台股代碼"""
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if any(x in info.type for x in ["權證", "ETF", "受益證券"]): continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return sorted(list(set(taiwan_tickers)))

def check_breakout_v53(ticker, g_limit, v_limit, min_v, bias_range, use_bias):
    try:
        df = yf.Ticker(ticker).history(period="400d")
        if len(df) < 245: return None
        last = df.iloc[-1]
        
        # 1. 流動性過濾
        if (last['Volume'] / 1000) < min_v: return None
        
        # 2. 計算均線
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma10 = df['Close'].rolling(10).mean().iloc[-1]
        ma20 = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        ma240 = df['Close'].rolling(240).mean().iloc[-1]
        
        # 3. 季年線位階 (MA60 vs MA240)
        ma_bias = round(((ma60 / ma240) - 1) * 100, 2)
        if not (bias_range[0] <= ma_bias <= bias_range[1]): return None
        
        # 4. 短線糾結度 (5, 10, 20MA)
        ma_list = [ma5, ma10, ma20]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        
        # 5. 成交量比 (窒息量)
        v_ratio = round(last['Volume'] / df['Volume'].rolling(20).mean().iloc[-1], 2)
        
        if gap <= g_limit and v_ratio <= v_limit:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "類股": info.category if info else "其他", "現價": round(last['Close'], 2),
                "短線糾結(%)": gap, "季年位階(%)": ma_bias, "量比": v_ratio,
                "位階屬性": "📈 多頭起漲" if ma_bias > 0 else "🩹 底部補漲"
            }
    except: return None

def get_historical_theme_ai(ticker, name):
    try:
        df = yf.download(ticker, period="6mo", progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        prompt = f"分析台股 {name}({ticker})。該股在 {max_day.strftime('%Y-%m-%d')} 大幅上漲。請簡述利多原因(40字內)。"
        return f"📅 {max_day.strftime('%Y-%m-%d')} 考古：{model.generate_content(prompt).text}"
    except: return "考古暫時失敗"

def plot_v53(ticker):
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
def show_stock_v53(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 考古中..."):
        st.info(get_historical_theme_ai(ticker, name))
    chart = plot_v53(ticker)
    if chart: st.plotly_chart(chart, use_container_width=True)
    if st.button("關閉診斷", use_container_width=True): st.rerun()

# --- 3. UI 介面 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📅 歷史表現", "📚 雲端資料庫", "⚡ 飆股偵測器"])

try:
    db = conn.read(worksheet="Sheet1")
    db = db.dropna(subset=['標的'])
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
    if st.button("📈 計算回測 (僅顯示最近10筆)"):
        bt = []
        for _, r in db.tail(10).iterrows():
            m = re.search(r'\d{4}', str(r['標的']))
            if m:
                s = f"{m.group(0)}.TW" if int(m.group(0)) < 9000 else f"{m.group(0)}.TWO"
                h = yf.download(s, start=r['日期'], progress=False)
                if not h.empty:
                    p0, pn = h['Close'].iloc[0], h['Close'].iloc[-1]
                    bt.append({"標的": r['標的'], "當初日期": r['日期'], "漲跌%": round(((pn/p0)-1)*100, 2)})
        st.session_state.backtest_df = pd.DataFrame(bt)
    if st.session_state.backtest_df is not None: 
        st.dataframe(st.session_state.backtest_df.style.applymap(lambda x: 'color:red' if x > 0 else 'color:green', subset=['漲跌%']))

with tab3:
    st.subheader("📚 雲端監控庫")
    st.dataframe(db, width='stretch')

with tab4:
    st.subheader("⚡ 飆股 DNA 高階偵測")
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        mode = st.radio("範圍", ["全台股", "資料庫標的"], horizontal=True)
        use_bias = st.checkbox("啟用季年位階過濾", value=True)
        bias_range = st.slider("季年乖離 (%)", -30, 60, (-10, 25), disabled=not use_bias)
    with c2:
        g_limit = st.slider("短線糾結度 (%)", 1.0, 10.0, 4.0)
        min_v = st.number_input("最低成交量 (張)", value=300)
    with c3:
        v_limit = st.slider("成交量比 (窒息量)", 0.1, 2.5, 0.8)

    if st.button("🏁 啟動深度掃描", use_container_width=True):
        all_available = get_taiwan_stock_tickers()
        if mode == "全台股":
            search_list = all_available
        else:
            db_sids = [re.search(r'\d{4}', str(x)).group(0) for x in db['標的'] if re.search(r'\d{4}', str(x))]
            search_list = [t for t in all_available if any(sid in t for sid in db_sids)]
        
        if search_list:
            hits = []
            prog, status = st.progress(0), st.empty()
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(check_breakout_v53, s, g_limit, v_limit, min_v, bias_range, use_bias): s for s in search_list}
                for i, f in enumerate(concurrent.futures.as_completed(futures)):
                    res = f.result()
                    if res: hits.append(res)
                    if i % 10 == 0:
                        prog.progress((i+1)/len(search_list))
                        status.text(f"掃描中: {i+1}/{len(search_list)}")
            st.session_state.v53_results = pd.DataFrame(hits)
            status.success(f"⚡ 完成！發現 {len(hits)} / {len(search_list)}檔符合標的。")

    # --- 關鍵：點擊表格觸發 K線圖 ---
    if st.session_state.v53_results is not None and not st.session_state.v53_results.empty:
        st.write("---")
        st.write("💡 **點選下方橫列**：自動展開 AI 診斷與多空 K 線圖")
        event = st.dataframe(
            st.session_state.v53_results, 
            on_select="rerun", 
            selection_mode="single-row", 
            hide_index=True,
            use_container_width=True,
            column_config={
                "短線糾結(%)": st.column_config.NumberColumn(format="%.2f%%"),
                "季年位階(%)": st.column_config.NumberColumn(format="%.1f%%"),
                "量比": st.column_config.NumberColumn(format="%.2f")
            }
        )
        
        # 檢測是否有選中列
        if event.selection.rows:
            selected_row_index = event.selection.rows[0]
            row_data = st.session_state.v53_results.iloc[selected_row_index]
            show_stock_v53(row_data['代號'], row_data['名稱'])
