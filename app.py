import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import twstock
import re
import json
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime

# --- 1. 系統初始化 ---
st.set_page_config(page_title="AI 飆股診斷 v7.1", layout="wide", page_icon="🛡️")

# 初始化所有記憶體狀態
for key in ['v71_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心篩選引擎 (量縮糾結邏輯) ---

def check_squeeze_v71(ticker, gap_threshold, vol_threshold, min_v):
    try:
        df = yf.download(ticker, period="450d", interval="1d", progress=False)
        if df is None or df.empty or len(df) < 250: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        df = df.dropna(subset=['Close'])
        df = df[df['Volume'] > 0]
        
        last = df.iloc[-1]
        vol_ma20 = df['Volume'].rolling(20).mean().iloc[-1]
        
        # 成交量限制
        current_shares = last['Volume'] / 1000
        if current_shares < min_v: return None
        current_vol_ratio = last['Volume'] / vol_ma20
        if current_vol_ratio > vol_threshold: return None
        
        # 均線糾結 (5,10,20,60,120,240)
        close = df['Close']
        ma_list = [close.rolling(p).mean().iloc[-1] for p in [5,10,20,60,120,240]]
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

# --- 3. 診斷視窗 (K線 + 成交量) ---

@st.dialog("📈 專業量價診斷", width="large")
def show_details_v71(ticker, name):
    st.write(f"### {name} ({ticker})")
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
    
    # 計算均線
    ma_conf = {'MA5':'yellow','MA10':'#00BFFF','MA20':'#DA70D6','MA60':'#32CD32','MA120':'red','MA240':'#FF8C00'}
    for p, c in zip([5,10,20,60,120,240], ma_conf.values()):
        df[f'MA{p}'] = df['Close'].rolling(p).mean()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1, row_heights=[0.7, 0.3])
    # K線
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    # 均線
    for ma, color in ma_conf.items():
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=color, width=1.2)), row=1, col=1)
    # 成交量 (紅漲綠跌)
    vol_colors = ['#FF4136' if c >= o else '#3D9970' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=vol_colors), row=2, col=1)
    
    fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width='stretch')
    if st.button("關閉", width='stretch'): st.rerun()

# --- 4. 主介面分頁區 ---

# 讀取雲端庫資料
try:
    db = conn.read(worksheet="Sheet1").dropna(subset=['標的'])
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📈 績效回測", "📚 雲端資料庫", "⚡ 策略偵測器"])

# --- TAB 1: 週報提取 ---
with tab1:
    pdf = st.file_uploader("上傳券商週報 (PDF)", type="pdf")
    if pdf and st.button("🚀 開始解析內容", width='stretch'):
        reader = PdfReader(pdf)
        text = "".join([p.extract_text() for p in reader.pages])
        prompt = "請從以下文本提取股票標的。回傳 JSON 格式列表，包含: 標的(代號+名稱), 題材, 原因。內容須精簡。"
        res = model.generate_content(f"{prompt}\n\n{text[:8000]}").text
        st.session_state.raw_json = res.replace('```json', '').replace('```', '').strip()
        st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")
    
    if st.session_state.raw_json:
        st.write("### AI 解析結果")
        st.code(st.session_state.raw_json, language='json')
        if st.button("📥 確認並存入 Google Sheets", width='stretch'):
            new_df = pd.DataFrame(json.loads(st.session_state.raw_json))
            new_df['日期'] = st.session_state.rep_date
            updated_db = pd.concat([db, new_df], ignore_index=True)
            conn.update(worksheet="Sheet1", data=updated_db)
            st.success("資料已成功同步至雲端庫！")

# --- TAB 2: 績效回測 ---
with tab2:
    if st.button("🏁 執行庫存標的回測", width='stretch'):
        bt_list = []
        for _, row in db.tail(15).iterrows():
            sid = re.search(r'\d{4}', str(row['標的'])).group(0)
            ticker = f"{sid}.TW" if int(sid) < 9000 else f"{sid}.TWO"
            hist = yf.download(ticker, start=row['日期'], progress=False)
            if not hist.empty:
                start_p = hist['Close'].iloc[0]
                end_p = hist['Close'].iloc[-1]
                change = round(((end_p / start_p) - 1) * 100, 2)
                bt_list.append({"標的": row['標的'], "推薦日期": row['日期'], "起始價": round(float(start_p),1), "目前價": round(float(end_p),1), "漲跌%": change})
        st.session_state.backtest_df = pd.DataFrame(bt_list)
    
    if st.session_state.backtest_df is not None:
        st.dataframe(st.session_state.backtest_df, width='stretch', hide_index=True)

# --- TAB 3: 雲端資料庫 ---
with tab3:
    st.write(f"目前雲端庫共有 {len(db)} 筆標的")
    st.dataframe(db, width='stretch', hide_index=True)

# --- TAB 4: 策略偵測器 (量縮糾結) ---
with tab4:
    st.subheader("⚡ 潛伏飆股偵測 (量縮+糾結)")
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 2, 1])
        try:
            t_df = yf.download("2330.TW", period="3d", progress=False)
            if not t_df.empty: c1.metric("數據連線", "✅ 正常")
        except: c1.metric("數據連線", "❌ 錯誤")
        if c3.button("🔄 重新整理", width='stretch'): st.rerun()

    st.write("### ⚙️ 策略參數調整")
    p1, p2, p3 = st.columns(3)
    gap_p = p1.slider("均線糾結度 (%)", 1.0, 10.0, 5.5)
    vol_p = p2.slider("量縮比 (窒息量)", 0.1, 1.0, 0.6)
    min_v_p = p3.number_input("最低日張數", value=300)

    scope = st.segmented_control("掃描範圍", ["全台股", "雲端庫"], default="全台股")
    
    if st.button("🏁 開始執行掃描", width='stretch', type="primary"):
        if scope == "全台股":
            all_tickers = [f"{c}.TW" if i.market=="上市" else f"{c}.TWO" for c, i in twstock.codes.items() if c.isdigit() and len(c)==4 and "ETF" not in i.type]
        else:
            all_tickers = [f"{re.search(r'\d{4}', str(x)).group(0)}.TW" for x in db['標的'] if re.search(r'\d{4}', str(x))]
            
        hits = []
        prog = st.progress(0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(check_squeeze_v71, t, gap_p, vol_p, min_v_p): t for t in all_tickers}
            for i, f in enumerate(concurrent.futures.as_completed(futures)):
                res = f.result()
                if res: hits.append(res)
                if i % 25 == 0: prog.progress((i+1)/len(all_tickers))
        
        st.session_state.v71_results = pd.DataFrame(hits)
        st.success(f"掃描完成！發現 {len(hits)} 檔符合標的。")

    if st.session_state.v71_results is not None and not st.session_state.v71_results.empty:
        st.info("💡 點擊下方表格查看『六色均線 K 線圖』與『成交量』")
        df_display = st.session_state.v71_results.sort_values("糾結度(%)")
        event = st.dataframe(df_display, on_select="rerun", selection_mode="single-row", hide_index=True, width='stretch')
        
        if event.selection.rows:
            target = df_display.iloc[event.selection.rows[0]]
            show_details_v71(target['代號'], target['名稱'])
