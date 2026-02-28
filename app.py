import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import re
import json
from datetime import datetime
import time
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import concurrent.futures  # 導入多線程庫

# --- 1. 基礎設定與連線 ---
st.set_page_config(page_title="AI 飆股診斷系統 v3.5", layout="wide", page_icon="⚡")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def check_breakout_dna(sid, g_limit, v_limit):
    """
    核心偵測引擎 (加入門檻參數以便並行計算)
    """
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        # 抓取 300 天數據
        df = yf.Ticker(f"{sid}{suffix}").history(period="300d")
        if len(df) < 245: return None
        
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
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 判定條件
        ma_list = [last['MA5'], last['MA10'], last['MA20']]
        ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = last['Volume'] / vol_avg20 if vol_avg20 > 0 else 1
        is_ma60_up = last['MA60'] > df['MA60'].iloc[-5]
        
        # 綜合過濾
        if ma_gap <= g_limit and v_ratio <= v_limit and last['Close'] > last['MA60'] and is_ma60_up:
            attr = "🚀 可能飆股 (長線無壓)" if last['Close'] > last['MA240'] else "🩹 補漲股 (年線壓力)"
            return {
                "sid": sid,
                "price": round(last['Close'], 2),
                "gap": round(ma_gap, 2),
                "v_ratio": round(v_ratio, 2),
                "type": attr,
                "signal": "🔥 轉強" if last['MACD_Hist'] > prev['MACD_Hist'] else "⏳ 整理"
            }
    except:
        pass
    return None

def plot_stock_chart(sid):
    """手機互動版 K 線診斷圖"""
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        df = yf.Ticker(f"{sid}{suffix}").history(period="300d")
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA240'] = df['Close'].rolling(240).mean()
        colors = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
        for ma, color in zip(['MA5', 'MA20', 'MA60', 'MA240'], ['white', 'yellow', 'orange', 'purple']):
            fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=color, width=1.5)), row=1, col=1)
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=colors, showlegend=False), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=50, b=5))
        return fig
    except: return None

# --- 3. UI 介面 ---
st.title("🚀 AI 飆股偵測系統 Pro")

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報解析", "📅 歷史診斷", "📚 資料庫明細", "⚡ 飆股偵測器"])

# 預載資料庫
try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame()

# --- Tab 1: 週報解析 ---
with tab1:
    uploaded_file = st.file_uploader("上傳投顧週報 PDF", type="pdf")
    if uploaded_file:
        if st.button("🚀 開始 AI 深度解析"):
            with st.spinner('Gemini 正在提取標的與題材...'):
                reader = PdfReader(uploaded_file)
                text = "".join([p.extract_text() for p in reader.pages])
                prompt = f"請將週報內容轉為 JSON 列表，格式：[{{\"題材\": \"\", \"原因\": \"\", \"標的\": \"代碼+名稱\"}}]。內容：{text[:10000]}"
                res = model.generate_content(prompt)
                st.session_state.analysis_res = res.text
                st.session_state.rep_date = datetime.now().strftime("%Y-%m-%d")
        
        if 'analysis_res' in st.session_state:
            st.code(st.session_state.analysis_res, language='json')
            if st.button("📥 存入雲端 Sheet"):
                raw_json = st.session_state.analysis_res.replace('```json', '').replace('```', '').strip()
                new_df = pd.DataFrame(json.loads(raw_json))
                new_df['日期'] = st.session_state.rep_date
                updated_db = pd.concat([db, new_df], ignore_index=True)
                conn.update(worksheet="Sheet1", data=updated_db)
                st.success("成功存入資料庫！")

# --- Tab 3: 資料庫明細 ---
with tab3:
    st.subheader("📚 雲端監控清單")
    st.dataframe(db, width="stretch")
    
try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame()

with tab4:
    st.subheader("🏁 快速篩選起漲點")
    col_l, col_r = st.columns(2)
    with col_l:
        mode = st.radio("範圍", ["資料庫題材", "全台股 (1101~9960)"], horizontal=True)
    with col_r:
        g_limit = st.slider("糾結度 (%)", 1.0, 5.0, 3.5)
        v_limit = st.slider("量比門檻", 0.1, 1.2, 0.75)

    if st.button("🚀 開始高速掃描"):
        search_list = []
        if mode == "資料庫題材":
            sids = []
            for s in db['標的'].astype(str): sids.extend(re.findall(r'\b\d{4}\b', s))
            search_list = list(set(sids))
        else:
            search_list = [str(i) for i in range(1101, 9961)]

        if search_list:
            hits = []
            start_time = time.time()
            progress = st.progress(0)
            status = st.empty()

            # --- 多線程執行核心 ---
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                # 建立任務映射
                future_to_sid = {executor.submit(check_breakout_dna, sid, g_limit, v_limit): sid for sid in search_list}
                
                for i, future in enumerate(concurrent.futures.as_completed(future_to_sid)):
                    res = future.result()
                    if res: hits.append(res)
                    
                    if i % 30 == 0:
                        progress.progress((i + 1) / len(search_list))
                        status.text(f"已掃描: {i+1} / {len(search_list)} 檔...")

            status.success(f"⚡ 掃描完成！耗時: {int(time.time()-start_time)} 秒")
            
            if hits:
                st.session_state.scan_results = hits
                res_df = pd.DataFrame(hits)
                res_df.columns = ['代號', '現價', '糾結(%)', '量比', '長線屬性', '動能']
                st.dataframe(res_df.sort_values('糾結(%)'), width="stretch")
            else:
                st.info("未發現符合 DNA 的標的。")

    if 'scan_results' in st.session_state:
        st.divider()
        selected = st.selectbox("🎯 點選標的查看手機版診斷圖", [h['sid'] for h in st.session_state.scan_results])
        if selected:
            fig = plot_stock_chart(selected)
            if fig: st.plotly_chart(fig, use_container_width=True)
