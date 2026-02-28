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

# --- 1. 初始化設定 ---
st.set_page_config(page_title="AI 飆股偵測系統 Pro", layout="wide", page_icon="🚀")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"初始化失敗: {e}")
    st.stop()

# --- 2. 核心技術分析函式 ---

def extract_stock_ids(text):
    """提取 4 位數代碼並確保為字串"""
    if not isinstance(text, str):
        text = str(text) if pd.notna(text) else ""
    return re.findall(r'\b\d{4}\b', text)

def check_breakout_dna(sid):
    """
    核心偵測引擎：MA糾結 + 量縮 + 季線上揚 + MACD動能
    """
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        df = yf.Ticker(f"{sid}{suffix}").history(period="80d")
        if len(df) < 65: return None
        
        # A. 均線計算
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        
        # B. MACD 計算 (用來判定動能是否轉強，避開盤整失真)
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp1 - exp2
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['DIF'] - df['DEA']
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # C. 判定條件
        # 1. 均線糾結度 (5/10/20 MA)
        ma_list = [last['MA5'], last['MA10'], last['MA20']]
        ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
        
        # 2. 窒息量比
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = last['Volume'] / vol_avg20 if vol_avg20 > 0 else 1
        
        # 3. 季線趨勢 (今日 MA60 > 5日前 MA60)
        is_ma60_up = last['MA60'] > df['MA60'].iloc[-5]
        
        # 4. MACD 動能 (柱狀體往上走 = 轉強訊號)
        momentum_score = 1 if last['MACD_Hist'] > prev['MACD_Hist'] else 0
        
        # 綜合評分 (基礎門檻：站在季線上 & 季線上揚)
        is_ready = (last['Close'] > last['MA60']) and is_ma60_up
        
        return {
            "sid": sid,
            "price": round(last['Close'], 2),
            "gap": round(ma_gap, 2),
            "v_ratio": round(v_ratio, 2),
            "trend": "📈 上揚" if is_ma60_up else "📉 下彎",
            "signal": "🔥 轉強" if momentum_score else "⏳ 整理",
            "is_ready": is_ready
        }
    except: return None

# --- 3. App 介面 ---
st.title("📈 台股起漲點 DNA 自動化掃描儀")
tab1, tab2, tab3, tab4 = st.tabs(["🚀 週報解析", "📅 歷史診斷", "📚 資料庫明細", "⚡ 飆股偵測器"])

# 預取資料庫
try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame()

# --- Tab 1 & 2 & 3 (保持原有功能) ---
with tab1:
    up_col1, up_col2 = st.columns([3, 1])
    with up_col1: uploaded_file = st.file_uploader("上傳週報 PDF", type="pdf")
    with up_col2: re_analyze = st.button("🔄 重新分析")
    if uploaded_file and ('json_analysis' not in st.session_state or re_analyze):
        with st.spinner('Gemini 分析中...'):
            reader = PdfReader(uploaded_file)
            text = "".join([p.extract_text() for p in reader.pages if p.extract_text()])
            prompt = f"請將週報內容轉為 JSON 列表，格式：[{{\"題材\": \"\", \"原因\": \"\", \"標的\": \"代碼+名稱\"}}]。內容：{text[:15000]}"
            res = model.generate_content(prompt)
            st.session_state.json_analysis = res.text
            st.session_state.rep_date = datetime.now().strftime("%Y-%m-%d")
    if 'json_analysis' in st.session_state:
        st.code(st.session_state.json_analysis, language='json')
        if st.button("📥 存入雲端資料庫"):
            raw = st.session_state.json_analysis.replace('```json', '').replace('```', '').strip()
            new_data = pd.DataFrame(json.loads(raw))
            new_data['日期'] = st.session_state.rep_date
            conn.update(worksheet="Sheet1", data=pd.concat([db, new_data], ignore_index=True))
            st.success("存儲成功！")

with tab3:
    st.subheader("📚 雲端資料庫明細")
    st.dataframe(db, width="stretch")

# --- Tab 4: ⚡ 飆股偵測器 (核心合併版) ---
with tab4:
    st.subheader("⚡ 篩選條件設定")
    
    col_a, col_b = st.columns(2)
    with col_a:
        scan_mode = st.radio("掃描範圍", ["資料庫題材股", "全台股 (1101~9960)"], horizontal=True)
    with col_b:
        gap_limit = st.slider("均線糾結門檻 (%)", 1.0, 5.0, 3.5)
        vol_limit = st.slider("窒息量門檻 (倍)", 0.1, 1.0, 0.75)

    if st.button("🚀 開始執行起漲點偵測"):
        # 準備清單
        if scan_mode == "資料庫題材股":
            sids = []
            if not db.empty:
                for s in db['標的']: sids.extend(extract_stock_ids(s))
                search_list = list(set(sids))
            else: search_list = []
        else:
            # 排除 ETF (00xx)，從 1101 普通股開始
            search_list = [str(i) for i in range(1101, 9961)]

        if not search_list:
            st.warning("請先確保資料庫有數據或選擇全台股模式。")
        else:
            hits = []
            progress = st.progress(0)
            status = st.empty()
            start = time.time()

            for i, sid in enumerate(search_list):
                if i % 10 == 0: status.text(f"掃描中: {sid} ({i}/{len(search_list)})")
                res = check_breakout_dna(sid)
                
                # 最終過濾邏輯：糾結度、量縮、且符合 is_ready (季線上揚+股價在線上)
                if res and res['gap'] <= gap_limit and res['v_ratio'] <= vol_limit and res['is_ready']:
                    hits.append(res)
                progress.progress((i + 1) / len(search_list))
            
            status.success(f"掃描完成！耗時 {int(time.time()-start)} 秒")
            
            if hits:
                st.success(f"🎊 發現 {len(hits)} 檔潛力標的！")
                df_res = pd.DataFrame(hits).drop(columns=['is_ready'])
                df_res.columns = ['代號', '現價', '糾結(%)', '量比', '季線趨勢', '動能訊號']
                st.dataframe(df_res.sort_values('糾結(%)'), width="stretch")
                
                
            else:
                st.info("目前尚未發現完全符合 DNA 條件的標的。")
