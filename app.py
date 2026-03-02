import streamlit as st
import pandas as pd
import yfinance as yf
import twstock
import re
import json
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import time
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
from pypdf import PdfReader

# --- 1. 系統設定與初始化 ---
st.set_page_config(page_title="AI 飆股系統 v8.0", layout="wide", page_icon="🛡️")

# 初始化 Session State
for key in ['scan_results', 'raw_json', 'rep_date']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash')
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"連線設定錯誤: {e}")

# --- 2. 核心策略邏輯 (穩定循序掃描版) ---

def check_stock_v83(ticker, mode, p):
    try:
        df = yf.download(ticker, period="450d", interval="1d", progress=False)
        if df is None or len(df) < 250: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        df = df.dropna(subset=['Close']).copy()
        
        last = df.iloc[-1]
        close_p = float(last['Close'])
        vol_today = float(last['Volume'])
        avg_vol_20 = df['Volume'].tail(20).mean()
        shares = vol_today / 1000

        # 基礎過濾：張數
        if shares < p['min_v']: return None

        # 預計算均線 (5, 10, 20, 60, 120, 240)
        ma_vals = {str(m): df['Close'].rolling(m).mean().iloc[-1] for m in [5, 10, 20, 60, 120, 240]}
        ma_prev = {str(m): df['Close'].rolling(m).mean().iloc[-5] for m in [60, 120]} # 5天前
        
        # --- 策略 1：💎 量縮糾結 (長線大底) ---
        if mode == "💎 量縮糾結":
            v_ratio = vol_today / avg_vol_20
            if v_ratio > p['vol_ratio']: return None
            
            ma_list = list(ma_vals.values())
            ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
            if ma_gap > p['gap']: return None
            
            # 必須靠近 月/季/半年線 其中之一 (3.5% 誤差)
            supports = [ma_vals['20'], ma_vals['60'], ma_vals['120']]
            if not any(abs(close_p / s - 1) < 0.035 for s in supports): return None

            return {"代號": ticker, "名稱": twstock.codes.get(ticker[:4]).name, "現價": round(close_p, 2), "糾結%": round(ma_gap, 2), "量縮比": round(v_ratio, 2), "張數": int(shares), "型態": "大底糾結"}

        # --- 策略 2：🌀 量縮回測 (支撐找點) ---
        elif mode == "🌀 量縮回測":
            # 1. 季線/半年線上揚判定
            if ma_vals['60'] < ma_prev['60'] or ma_vals['120'] < ma_prev['120']: return None
            
            # 2. 季/半年/年線 乖離不可過大 (控制在 8% 內，避免追高)
            long_mas = [ma_vals['60'], ma_vals['120'], ma_vals['240']]
            long_gap = (max(long_mas) / min(long_mas) - 1) * 100
            if long_gap > 8.0: return None
            
            # 3. 收盤價靠近 季線或半年線 (3% 誤差)
            if not (abs(close_p / ma_vals['60'] - 1) < 0.03 or abs(close_p / ma_vals['120'] - 1) < 0.03): return None
            
            # 4. 短線 5/10/20 糾結 (等待噴發)
            short_mas = [ma_vals['5'], ma_vals['10'], ma_vals['20']]
            short_gap = (max(short_mas) / min(short_mas) - 1) * 100
            if short_gap > p['short_gap']: return None
            
            # 5. 成交量量縮
            v_ratio = vol_today / avg_vol_20
            if v_ratio > p['vol_ratio']: return None

            rank = "多頭排列" if ma_vals['60'] > ma_vals['120'] > ma_vals['240'] else "落後補漲"
            return {"代號": ticker, "名稱": twstock.codes.get(ticker[:4]).name, "現價": round(close_p, 2), "短糾%": round(short_gap, 2), "量縮比": round(v_ratio, 2), "張數": int(shares), "位階": rank}

        # --- 策略 3：🚀 帶量突破 ---
        elif mode == "🚀 帶量突破":
            v_ratio = vol_today / avg_vol_20
            if v_ratio >= p['breakout_vol'] and close_p > ma_vals['20']:
                return {"代號": ticker, "名稱": twstock.codes.get(ticker[:4]).name, "現價": round(close_p, 2), "量比": round(v_ratio, 2), "張數": int(shares), "狀態": "攻擊發動"}
                
        return None
    except: return None

# --- 3. K 線診斷與 AI 分析視窗 ---

@st.dialog("📈 專業診斷報告", width="large")
def show_diagnosis(ticker, name):
    st.write(f"### {name} ({ticker})")
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    # 繪製圖表 (六色均線 + 成交量)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    
    ma_conf = {'MA5':'yellow','MA10':'#00BFFF','MA20':'#DA70D6','MA60':'#32CD32','MA120':'red','MA240':'#FF8C00'}
    for m, color in ma_conf.items():
        ma_s = df['Close'].rolling(int(m[2:])).mean()
        fig.add_trace(go.Scatter(x=df.index, y=ma_s, name=m, line=dict(color=color, width=1.2)), row=1, col=1)
    
    vol_colors = ['#FF4136' if c >= o else '#3D9970' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=vol_colors), row=2, col=1)
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=5, b=5))
    st.plotly_chart(fig, use_container_width=True)

    # Gemini AI 分析 (針對過去大量漲幅原因)
    if st.button("🤖 使用 Gemini 分析漲幅原因", use_container_width=True):
        with st.spinner("AI 正在爬梳歷史新聞與題材..."):
            prompt = f"分析台灣股票 {name} ({ticker}) 過去一年中出現大幅漲幅的原因。請從產業地位、關鍵題材、財報表現三個面向簡潔回答。"
            res = model.generate_content(prompt)
            st.info(res.text)

# --- 4. 主介面分頁規劃 ---

# 讀取雲端庫
try: db = conn.read(worksheet="Sheet1").dropna(subset=['標的'])
except: db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📈 週報回顧", "📚 雲端資料庫", "⚡ 策略偵測器"])

with tab1:
    st.subheader("📄 Gemini 週報分析")
    pdf = st.file_uploader("上傳 PDF 週報", type="pdf")
    if pdf and st.button("🚀 解析週報", use_container_width=True):
        text = "".join([p.extract_text() for p in PdfReader(pdf).pages])
        res = model.generate_content(f"請提取股票標的、題材與推薦原因，並以 JSON 格式回傳: [{{\"標的\":\"\", \"題材\":\"\", \"原因\":\"\"}}]\n\n{text[:8000]}").text
        st.session_state.raw_json = res.replace('```json', '').replace('```', '').strip()
    
    if st.session_state.raw_json:
        st.code(st.session_state.raw_json, language='json')
        if st.button("📥 儲存至資料庫", use_container_width=True):
            new_data = pd.DataFrame(json.loads(st.session_state.raw_json))
            new_data['日期'] = datetime.date.today().strftime("%Y-%m-%d")
            conn.update(worksheet="Sheet1", data=pd.concat([db, new_data], ignore_index=True))
            st.success("存檔成功！")

with tab2:
    st.subheader("📈 週報推薦績效回顧")
    if st.button("執行績效回測 (最近 10 筆)", use_container_width=True):
        results = []
        for _, row in db.tail(10).iterrows():
            sid = re.search(r'\d{4}', str(row['標的'])).group(0)
            h = yf.download(f"{sid}.TW", start=row['日期'], progress=False)
            if not h.empty:
                chg = ((h['Close'].iloc[-1] / h['Close'].iloc[0]) - 1) * 100
                results.append({"標的": row['標的'], "推薦日": row['日期'], "目前漲跌%": round(float(chg), 2)})
        st.table(results)

with tab3:
    st.subheader("📚 雲端庫存分析")
    st.dataframe(db, use_container_width=True, hide_index=True)

with tab4:
    st.subheader("⚡ 策略偵測器")
    # 策略選擇切換
    mode = st.segmented_control("策略模式", ["💎 量縮糾結", "🌀 量縮回測", "🚀 帶量突破"], default="💎 量縮回測")
    
    # 動態參數設定
    with st.expander("🛠️ 參數與篩選設定", expanded=True):
        c1, c2, c3 = st.columns(3)
        p_min_v = c1.number_input("最低張數", value=300)
        p_dict = {'min_v': p_min_v}
        
        if mode == "💎 量縮糾結":
            p_dict['gap'] = c2.slider("全線糾結度%", 1.0, 10.0, 5.0)
            p_dict['vol_ratio'] = c3.slider("量縮比", 0.1, 1.0, 0.6)
            st.caption("🔍 條件：六線糾結 + 價格貼近 月/季/半年線 支撐區")
            
        elif mode == "🌀 量縮回測":
            p_dict['short_gap'] = c2.slider("短線糾結% (5/10/20)", 1.0, 5.0, 3.0)
            p_dict['vol_ratio'] = c3.slider("量縮比門檻", 0.1, 1.0, 0.5)
            st.caption("🔍 條件：季/半年線上揚 + 均線乖離控制 + 縮量回測支撐線")
            
        else:
            p_dict['breakout_vol'] = c2.slider("量比倍數", 2.0, 5.0, 3.5)
            st.caption("🔍 條件：今日成交量爆發 + 股價站上 20MA 月線")

    # 執行與結果顯示 (邏輯維持穩定循序掃描)
    if st.button("🏁 開始執行掃描", type="primary", use_container_width=True):
        all_tickers = [f"{c}.TW" if i.market=="上市" else f"{c}.TWO" for c, i in twstock.codes.items() if c.isdigit() and len(c)==4]
        hits = []
        bar = st.progress(0)
        status = st.empty()
        
        # 為了手機操作流暢，建議限制掃描總量或增加進度提示
        total = len(all_tickers)
        for i, t in enumerate(all_tickers):
            res = check_stock_v83(t, mode, p_dict)
            if res: hits.append(res)
            if i % 25 == 0: 
                bar.progress((i+1)/total)
                status.text(f"掃描中: {t} ({i+1}/{total})")
        
        st.session_state.scan_results = pd.DataFrame(hits)
        status.success(f"掃描完成！發現 {len(hits)} 檔符合標的。")

    # 表格點選看圖邏輯 (同前版)
    if st.session_state.scan_results is not None and not st.session_state.scan_results.empty:
        df_final = st.session_state.scan_results.reset_index(drop=True)
        sel = st.dataframe(df_final, on_select="rerun", selection_mode="single-row", use_container_width=True, hide_index=True)
        if sel.selection.rows:
            target = df_final.iloc[sel.selection.rows[0]]
            show_diagnosis(target['代號'], target['名稱'])
