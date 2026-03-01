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
st.set_page_config(page_title="AI 飆股診斷 v4.8", layout="wide", page_icon="🛡️")

# 初始化 Session State (防止 Rerun 導致數據消失)
keys = ['v48_results', 'raw_json', 'rep_date', 'backtest_df']
for key in keys:
    if key not in st.session_state:
        st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗，請檢查 secrets 設定: {e}")
    st.stop()

# --- 2. 核心運算函式 ---

def get_taiwan_stock_tickers():
    """獲取精確台股清單 (過濾權證與 ETF)"""
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if "權證" in info.type or "ETF" in info.type: continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return list(set(taiwan_tickers))

def get_historical_theme_ai(ticker, name):
    """AI 考古學：回溯該股過去半年漲幅最大時的市場利多原因"""
    try:
        df = yf.Ticker(ticker).history(period="6mo")
        if df.empty: return "無足夠歷史數據"
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        date_str = max_day.strftime('%Y-%m-%d')
        
        prompt = f"分析台股 {name}({ticker})。該股在 {date_str} 前後曾大幅上漲。請簡短說明當時該股爆發的主因（如：營收、特定題材或產業背景），限 40 字內。"
        response = model.generate_content(prompt)
        return f"📅 {date_str} 爆發主因：{response.text}"
    except: return "暫時無法考古該股歷史。"

def check_breakout_v48(ticker, g_limit, v_limit, min_v, bias_limit):
    """深度篩選：整合糾結度、窒息量、年線乖離率"""
    today = datetime.date.today()
    # 週末自動鎖定週五數據
    end_date = today - datetime.timedelta(days=today.weekday() - 4) if today.weekday() >= 5 else today
    start_date = end_date - datetime.timedelta(days=400)
    
    try:
        df = yf.Ticker(ticker).history(start=start_date, end=end_date)
        if df.empty or len(df) < 245: return None
        last = df.iloc[-1]
        
        # 1. 流動性過濾 (張數)
        if (last['Volume'] / 1000) < min_v: return None
        
        # 2. 計算均線
        ma_s = df['Close'].rolling(5).mean().iloc[-1]
        ma_m = df['Close'].rolling(10).mean().iloc[-1]
        ma_l = df['Close'].rolling(20).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        ma240 = df['Close'].rolling(240).mean().iloc[-1]
        
        # 3. 乖離率過濾 (離年線太遠不買)
        bias_240 = round(((last['Close'] / ma240) - 1) * 100, 2)
        if bias_240 > bias_limit: return None 
        
        # 4. 均線糾結度 (5, 10, 20MA)
        ma_list = [ma_s, ma_m, ma_l]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        
        # 5. 成交量比 (窒息量偵測)
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = round(last['Volume'] / vol_avg20, 2)
        
        # 6. 動能判定 (MACD 柱狀體)
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        macd_h = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
        
        # 條件：糾結 + 量縮 + 價格在季線(60MA)之上且季線上揚
        if gap <= g_limit and v_ratio <= v_limit and last['Close'] > ma60:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "類股": info.category if info else "其他",
                "現價": round(last['Close'], 2), "糾結(%)": gap, "量比": v_ratio,
                "年線乖離(%)": bias_240,
                "動能": "🔥 轉強" if macd_h.iloc[-1] > macd_h.iloc[-2] else "⏳ 整理"
            }
    except: return None

# --- 3. 繪圖與互動組件 ---

def plot_v48(ticker):
    try:
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
    except: return None

@st.dialog("🚀 AI 飆股深度診斷室", width="large")
def show_stock_v48(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 正在考古該股基因..."):
        story = get_historical_theme_ai(ticker, name)
        st.info(story)
    chart = plot_v48(ticker)
    if chart: st.plotly_chart(chart, width='stretch', use_container_width=True)
    if st.button("關閉診斷", use_container_width=True): st.rerun()

# --- 4. 分頁邏輯 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報解析", "📅 歷史戰績", "📚 雲端庫", "⚡ 飆股偵測器"])

try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

with tab1:
    st.subheader("📄 AI 投顧週報標的自動提取")
    pdf_file = st.file_uploader("上傳 PDF", type="pdf")
    if pdf_file and st.button("🚀 啟動 AI 解析"):
        with st.spinner('正在分析標的...'):
            reader = PdfReader(pdf_file)
            full_text = "".join([p.extract_text() for p in reader.pages])
            prompt = f"請提取週報中的標的並轉為 JSON (題材, 原因, 標的)。內容：{full_text[:8000]}"
            st.session_state.raw_json = model.generate_content(prompt).text
            st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")
    
    if st.session_state.raw_json:
        st.code(st.session_state.raw_json, language='json')
        if st.button("📥 寫入 Google Sheets"):
            try:
                clean = st.session_state.raw_json.replace('```json', '').replace('```', '').strip()
                new_df = pd.DataFrame(json.loads(clean))
                new_df['日期'] = st.session_state.rep_date
                conn.update(worksheet="Sheet1", data=pd.concat([db, new_df], ignore_index=True))
                st.success("寫入雲端成功！")
            except Exception as e: st.error(f"錯誤: {e}")

with tab2:
    st.subheader("📅 歷史題材表現追蹤")
    if st.button("📈 執行戰績核算"):
        with st.spinner('計算漲跌幅中...'):
            bt = []
            recent = db.drop_duplicates(subset=['標的']).tail(15)
            for _, r in recent.iterrows():
                sid_match = re.search(r'\d{4}', str(r['標的']))
                if sid_match:
                    sid = sid_match.group(0)
                    sym = f"{sid}.TW" if int(sid)<9000 else f"{sid}.TWO"
                    h = yf.Ticker(sym).history(start=r['日期'])
                    if not h.empty:
                        p_0, p_n = h.iloc[0]['Close'], h.iloc[-1]['Close']
                        bt.append({"日期": r['日期'], "標的": r['標的'], "當初價": round(p_0,2), "現價": round(p_n,2), "漲跌(%)": round(((p_n/p_0)-1)*100,2)})
            st.session_state.backtest_df = pd.DataFrame(bt)
    if st.session_state.backtest_df is not None:
        st.dataframe(st.session_state.backtest_df.style.applymap(lambda x: 'color:red' if x > 0 else 'color:green', subset=['漲跌(%)']), width='stretch')

with tab3:
    st.subheader("📚 雲端監控資料庫")
    q = st.text_input("🔍 搜尋標的或題材")
    if not db.empty:
        st.dataframe(db[db.astype(str).apply(lambda x: x.str.contains(q)).any(axis=1)], width='stretch')

with tab4:
    st.subheader("⚡ 飆股 DNA 高階偵測 (AI 考古整合版)")
    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_a: 
        mode = st.radio("範圍", ["資料庫題材股", "全台股 (1101~9960)"], horizontal=True)
        bias = st.slider("年線乖離上限 (%)", 10, 100, 40)
    with col_b:
        g_val = st.slider("均線糾結度 (%)", 1.0, 5.0, 3.5)
        min_vol = st.slider("最低成交張數", 100, 2000, 500, step=100)
    with col_c:
        v_val = st.slider("量比門檻 (窒息量)", 0.1, 1.2, 0.75)

    if st.button("🏁 啟動高速深度掃描", use_container_width=True):
        topic_map = {}
        if not db.empty:
            for _, r in db.iterrows():
                sid = re.search(r'\d{4}', str(r['標的']))
                if sid: topic_map[sid.group(0)] = r['題材']
        
        search_list = []
        if mode == "資料庫題材股":
            search_list = [f"{k}.TW" if int(k)<9000 else f"{k}.TWO" for k in topic_map.keys()]
        else:
            with st.spinner("獲取全台股代碼..."): search_list = get_taiwan_stock_tickers()

        if search_list:
            hits = []
            prog, status = st.progress(0), st.empty()
            start_t = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as ex:
                futures = {ex.submit(check_breakout_v48, s, g_val, v_val, min_vol, bias): s for s in search_list}
                for i, f in enumerate(concurrent.futures.as_completed(futures)):
                    res = f.result()
                    if res:
                        pure_sid = re.search(r'\d{4}', res['代號']).group(0)
                        res['💡關聯題材'] = topic_map.get(pure_sid, "新發現標的")
                        hits.append(res)
                    if i % 40 == 0:
                        prog.progress((i+1)/len(search_list))
                        status.text(f"掃描中: {i+1}/{len(search_list)}")
            st.session_state.v48_results = pd.DataFrame(hits) if hits else pd.DataFrame()
            status.success(f"⚡ 完成！發現 {len(hits)} 檔符合 DNA 標的 (耗時 {int(time.time()-start_t)} 秒)")

    # 顯示結果
    if st.session_state.v48_results is not None and not st.session_state.v48_results.empty:
        st.write("### 🔍 深度偵測結果 (點選橫列彈出 AI 考古診斷)")
        event = st.dataframe(
            st.session_state.v48_results, width='stretch', 
            on_select="rerun", selection_mode="single-row", hide_index=True,
            column_config={
                "年線乖離(%)": st.column_config.ProgressColumn("年線乖離", min_value=0, max_value=bias, format="%.1f%%"),
                "類股": st.column_config.BadgeColumn("產業類股"),
                "💡關聯題材": st.column_config.TextColumn("週報原題材")
            }
        )
        if event.selection.rows:
            row = st.session_state.v48_results.iloc[event.selection.rows[0]]
            show_stock_v48(row['代號'], row['名稱'])
