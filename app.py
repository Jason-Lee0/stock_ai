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
st.set_page_config(page_title="AI 飆股診斷 v7.3", layout="wide", page_icon="🛡️")

for key in ['v73_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("系統初始化失敗，請檢查 Secrets。")

# --- 2. 核心策略庫 (Strategy Library) ---

def check_strategy_logic(ticker, strategy_mode, params):
    """
    修正重點：確保每個 ticker 進來時，產生的結果字典是完全獨立的
    """
    try:
        # 下載數據 (強韌模式)
        df = yf.download(ticker, period="450d", interval="1d", progress=False)
        if df is None or df.empty or len(df) < 250: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        
        df = df.dropna(subset=['Close'])
        last = df.iloc[-1].copy() # 使用 copy 確保資料獨立
        close_series = df['Close']
        vol_ma20 = df['Volume'].rolling(20).mean().iloc[-1]
        
        current_shares = last['Volume'] / 1000
        if current_shares < params['min_v']: return None

        # --- 策略 A：量縮糾結 ---
        if strategy_mode == "💎 量縮糾結":
            v_ratio = last['Volume'] / vol_ma20
            if v_ratio > params['vol_ratio']: return None
            
            # 計算六線糾結
            ma_values = [close_series.rolling(p).mean().iloc[-1] for p in [5,10,20,60,120,240]]
            ma_gap = (max(ma_values) / min(ma_values) - 1) * 100
            
            if ma_gap <= params['gap']:
                # 抓取名稱
                sid = re.search(r'\d{4}', ticker).group(0)
                name = twstock.codes.get(sid).name if twstock.codes.get(sid) else "未知"
                
                # 回傳明確的資料結構
                return {
                    "代號": str(ticker),
                    "名稱": str(name),
                    "現價": round(float(last['Close']), 2),
                    "糾結度(%)": round(float(ma_gap), 2),
                    "量縮比": round(float(v_ratio), 2),
                    "今日張數": int(current_shares)
                }
        
        # --- 策略 B：帶量突破 (預留) ---
        elif strategy_mode == "🚀 帶量突破":
            v_ratio = last['Volume'] / vol_ma20
            if v_ratio >= 2.5 and last['Close'] > close_series.rolling(20).mean().iloc[-1]:
                sid = re.search(r'\d{4}', ticker).group(0)
                name = twstock.codes.get(sid).name if twstock.codes.get(sid) else "未知"
                return {
                    "代號": str(ticker), "名稱": str(name), "現價": round(float(last['Close']), 2),
                    "量比": round(float(v_ratio), 2), "今日張數": int(current_shares)
                }
    except:
        return None
    return None

# --- 3. 診斷視窗 ---
@st.dialog("📈 專業診斷報告", width="large")
def show_diagnosis(ticker, name):
    st.write(f"### {name} ({ticker})")
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
    
    ma_colors = {'MA5':'yellow','MA10':'#00BFFF','MA20':'#DA70D6','MA60':'#32CD32','MA120':'red','MA240':'#FF8C00'}
    for p, c in zip([5,10,20,60,120,240], ma_colors.values()):
        df[f'MA{p}'] = df['Close'].rolling(p).mean()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    for ma, color in ma_colors.items():
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=color, width=1.2)), row=1, col=1)
    
    vol_colors = ['#FF4136' if c >= o else '#3D9970' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=vol_colors), row=2, col=1)
    
    fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

# --- 4. UI 介面 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📈 績效回測", "📚 雲端資料庫", "⚡ 策略偵測器"])

with tab4:
    st.subheader("⚡ 策略偵測器")
    
    # A. 選擇策略模式
    st.write("#### 1. 選擇策略模式")
    strat_mode = st.segmented_control("目前執行策略", ["💎 量縮糾結", "🚀 帶量突破"], default="💎 量縮糾結")
    
    # B. 根據模式顯示對應參數
    st.write("#### 2. 調整策略參數")
    p_col1, p_col2, p_col3 = st.columns(3)
    
    params = {}
    params['min_v'] = p_col1.number_input("最低日成交量 (張)", value=300)

    if strat_mode == "💎 量縮糾結":
        params['gap'] = p_col2.slider("均線糾結度 (%)", 1.0, 10.0, 5.5)
        params['vol_ratio'] = p_col3.slider("量縮窒息比 (今日/均量)", 0.1, 1.0, 0.6)
    else:
        params['breakout_vol'] = p_col2.slider("突破量比 (倍數)", 2.0, 5.0, 3.0)
        st.info("💡 帶量突破模式將篩選股價站上關鍵均線且成交量爆發之標的。")

    # C. 執行掃描
    scope = st.radio("掃描範圍", ["全台股", "雲端庫"], horizontal=True)
    
    if st.button("🏁 執行策略掃描", width='stretch', type="primary"):
        # 準備代號列表 (省略重複邏輯...)
        all_codes = twstock.codes
        all_tickers = [f"{c}.TW" if i.market=="上市" else f"{c}.TWO" for c, i in all_codes.items() if c.isdigit() and len(c)==4]
        
        params = {'min_v': p_min_v, 'gap': p_gap, 'vol_ratio': p_vol}
        hits = []
        prog = st.progress(0)
        
        # 使用執行緒池
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # 關鍵：將參數打包進去，避免參考同一個記憶體位址
            future_to_ticker = {executor.submit(check_strategy_logic, t, strat_mode, params): t for t in all_tickers}
            for i, future in enumerate(concurrent.futures.as_completed(future_to_ticker)):
                result = future.result()
                if result:
                    hits.append(result)
                if i % 30 == 0:
                    prog.progress((i+1)/len(all_tickers))
        
        # 儲存結果並強制轉換為 DataFrame
        st.session_state.v73_results = pd.DataFrame(hits)
        st.success(f"掃描完成！發現 {len(hits)} 檔標的。")

    # 顯示結果
    if st.session_state.v73_results is not None and not st.session_state.v73_results.empty:
        # 修正：顯示前先根據糾結度排序，並重設索引避免選取錯誤
        df_final = st.session_state.v73_results.sort_values("糾結度(%)").reset_index(drop=True)
        
        st.info("💡 點擊下方列查看 K 線圖。")
        selection = st.dataframe(
            df_final, 
            on_select="rerun", 
            selection_mode="single-row", 
            hide_index=True, 
            use_container_width=True
        )
        
        # 修正彈窗對象抓取
        if selection.selection.rows:
            selected_row_index = selection.selection.rows[0]
            target_stock = df_final.iloc[selected_row_index]
            show_diagnosis(target_stock['代號'], target_stock['名稱'])

# --- 4. 主介面分頁區 ---

# 讀取雲端庫資料
try:
    db = conn.read(worksheet="Sheet1").dropna(subset=['標的'])
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])


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

