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

# --- 1. 基礎設定與連線 ---
st.set_page_config(page_title="AI 飆股診斷系統 v3.0", layout="wide", page_icon="📈")

try:
    # 請確保 st.secrets 中有 GEMINI_KEY
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗，請檢查 Secrets 設定: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def extract_stock_ids(text):
    """從文字中提取 4 位數台股代碼"""
    if not isinstance(text, str):
        text = str(text) if pd.notna(text) else ""
    return re.findall(r'\b\d{4}\b', text)

def check_breakout_dna(sid):
    """
    飆股 DNA 偵測引擎：
    1. 均線糾結 2. 窒息量 3. 季線上揚 4. 年線位階判斷 5. MACD 動能過濾
    """
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        # 抓取 300 天數據確保年線 (MA240) 準確
        df = yf.Ticker(f"{sid}{suffix}").history(period="300d")
        if len(df) < 245: return None
        
        # A. 技術指標計算
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA240'] = df['Close'].rolling(240).mean()
        
        # MACD (12, 26, 9)
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp1 - exp2
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['DIF'] - df['DEA']
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # B. 判定條件
        # 1. 均線糾結 (5/10/20 MA 差距 < 3.5%)
        ma_list = [last['MA5'], last['MA10'], last['MA20']]
        ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
        
        # 2. 窒息量 (成交量 < 20日均量 0.75倍)
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = last['Volume'] / vol_avg20 if vol_avg20 > 0 else 1
        
        # 3. 季線趨勢與股價位置
        is_ma60_up = last['MA60'] > df['MA60'].iloc[-5]
        is_above_ma60 = last['Close'] > last['MA60']
        
        # 4. 長線位階標註 (年線 MA240)
        if last['Close'] > last['MA240']:
            attr = "🚀 可能飆股 (長線無壓)"
        else:
            attr = "🩹 補漲股 (年線壓力)"
            
        # 5. MACD 動能 (柱狀體翻正或增長)
        momentum = "🔥 轉強" if last['MACD_Hist'] > prev['MACD_Hist'] else "⏳ 整理"
        
        return {
            "sid": sid,
            "price": round(last['Close'], 2),
            "gap": round(ma_gap, 2),
            "v_ratio": round(v_ratio, 2),
            "type": attr,
            "signal": momentum,
            "is_ready": is_above_ma60 and is_ma60_up
        }
    except:
        return None

def plot_stock_chart(sid):
    """手機優化版：量價連動 K 線診斷圖"""
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        df = yf.Ticker(f"{sid}{suffix}").history(period="300d")
        
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA240'] = df['Close'].rolling(240).mean()
        
        # 成交量顏色連動
        colors = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                           vertical_spacing=0.05, row_heights=[0.7, 0.3])

        # 上圖：K線與均線
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['MA5'], name="MA5", line=dict(color='white', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], name="MA20", line=dict(color='yellow', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], name="MA60", line=dict(color='orange', width=2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['MA240'], name="MA240", line=dict(color='purple', width=2)), row=1, col=1)

        # 下圖：成交量
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=colors, showlegend=False), row=2, col=1)

        fig.update_layout(template="plotly_dark", height=650, xaxis_rangeslider_visible=False,
                          margin=dict(l=5, r=5, t=50, b=5),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        return fig
    except:
        return None

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

# --- Tab 4: 飆股偵測器 (核心功能) ---
with tab4:
    st.subheader("⚡ 飆股 DNA 大數據掃描")
    
    col1, col2 = st.columns(2)
    with col1:
        mode = st.radio("掃描模式", ["資料庫內的題材股", "掃描全台股 (1101~9960)"], horizontal=True)
    with col2:
        g_limit = st.slider("糾結門檻 (%)", 1.0, 5.0, 3.5, help="5,10,20MA差距")
        v_limit = st.slider("窒息量門檻 (倍)", 0.1, 1.0, 0.75, help="今日量/20日均量")

    if st.button("🏁 開始執行 DNA 篩選"):
        # 準備掃描清單
        search_list = []
        if mode == "資料庫內的題材股":
            if not db.empty:
                for s in db['標的']: search_list.extend(extract_stock_ids(s))
                search_list = list(set(search_list))
        else:
            # 排除 00xx ETF 區段
            search_list = [str(i) for i in range(1101, 9961)]

        if not search_list:
            st.warning("目前清單為空，請先執行週報解析或選擇全台股模式。")
        else:
            hits = []
            progress_bar = st.progress(0)
            status_txt = st.empty()
            start_time = time.time()

            for i, sid in enumerate(search_list):
                if i % 10 == 0: status_txt.text(f"分析中: {sid} ({i}/{len(search_list)})")
                res = check_breakout_dna(sid)
                
                # 過濾條件
                if res and res['gap'] <= g_limit and res['v_ratio'] <= v_limit and res['is_ready']:
                    hits.append(res)
                progress_bar.progress((i + 1) / len(search_list))
            
            status_txt.success(f"掃描完成！耗時: {int(time.time()-start_time)} 秒")
            
            if hits:
                st.session_state.scan_hits = hits
                df_hits = pd.DataFrame(hits).drop(columns=['is_ready'])
                df_hits.columns = ['代號', '現價', '糾結(%)', '量比', '長線屬性', '動能訊號']
                st.dataframe(df_hits.sort_values('糾結(%)'), width="stretch")
            else:
                st.info("目前無符合條件之標的。")

    # 點選看圖區
    if 'scan_hits' in st.session_state and st.session_state.scan_hits:
        st.divider()
        target = st.selectbox("🎯 選擇標的查看診斷圖", [h['sid'] for h in st.session_state.scan_hits])
        if target:
            fig = plot_stock_chart(target)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
