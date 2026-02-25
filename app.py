import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import re
import json
from datetime import datetime

# --- 1. 初始化設定 ---
st.set_page_config(page_title="AI 股票週報資料庫系統", layout="wide")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    # 升級至最新的 gemini-2.5-flash
    model = genai.GenerativeModel('gemini-2.5-flash')
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"初始化失敗: {e}")
    st.stop()

# --- 2. 輔助函式 ---
def extract_stock_ids(text):
    """提取 4 位數代碼"""
    return re.findall(r'\b\d{4}\b', text)

def get_stock_perf(sid):
    """獲取台股即時行情"""
    try:
        t = yf.Ticker(f"{sid}.TW")
        h = t.history(period="1mo")
        if h.empty: return None
        cur = h['Close'].iloc[-1]
        chg = ((cur - h['Close'].iloc[0]) / h['Close'].iloc[0]) * 100
        return {"price": cur, "change": chg}
    except: return None

# --- 3. App 介面 ---
st.title("📂 專業週報 JSON 結構化基地 (Gemini 2.5)")
tab1, tab2, tab3 = st.tabs(["🚀 週報解析", "📅 歷史診斷", "📚 資料庫明細"])

# --- Tab 1: 解析與 JSON 儲存 ---
with tab1:
    up_col1, up_col2 = st.columns([3, 1])
    with up_col1:
        uploaded_file = st.file_uploader("上傳週報 PDF", type="pdf")
    with up_col2:
        st.write("")
        st.write("")
        re_analyze = st.button("🔄 重新分析")

    if uploaded_file:
        if 'json_analysis' not in st.session_state or re_analyze:
            with st.spinner('Gemini 2.5 正在進行深度 JSON 結構化掃描...'):
                reader = PdfReader(uploaded_file)
                text = "".join([p.extract_text() for p in reader.pages if p.extract_text()])
                
                # 嚴格的 JSON Prompt
                prompt = f"""
                你是一位專業股票分析員。請深入分析週報中的文字、表格與圖表。
                請「只」輸出一個 JSON 格式的列表，不要有任何前言、結語或 Markdown 標記。
                
                JSON 格式要求：
                [
                  {{"題材": "產業名稱", "原因": "10字內原因", "標的": "4位數代碼+名稱"}}
                ]
                
                週報內容：
                {text[:18000]}
                """
                res = model.generate_content(prompt)
                st.session_state.json_analysis = res.text
                
                # 自動偵測日期
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', uploaded_file.name)
                st.session_state.rep_date = date_match.group(0) if date_match else datetime.now().strftime("%Y-%m-%d")

        if 'json_analysis' in st.session_state:
            st.markdown(f"### 📋 {st.session_state.rep_date} 結構化結果")
            st.code(st.session_state.json_analysis, language='json')

            if st.button("📥 確認存入 Google Sheets (一題材一行)"):
                try:
                    # 清理 JSON 字串
                    raw_str = st.session_state.json_analysis.replace('```json', '').replace('```', '').strip()
                    data_list = json.loads(raw_str)
                    
                    # 轉為 DataFrame 並補上日期
                    new_df = pd.DataFrame(data_list)
                    new_df['日期'] = st.session_state.rep_date
                    
                    # 讀取現有資料
                    old_df = conn.read(worksheet="Sheet1")
                    
                    # 確保欄位對齊並合併
                    final_df = pd.concat([old_df, new_df[['日期', '題材', '原因', '標的']]], ignore_index=True)
                    
                    # 更新至雲端
                    conn.update(worksheet="Sheet1", data=final_df)
                    st.success(f"✅ 成功！已存入 {len(new_df)} 筆結構化數據。")
                    del st.session_state.json_analysis # 清除暫存
                except Exception as e:
                    st.error(f"存入錯誤 (請檢查 JSON 格式或 Sheet 權限): {e}")

# --- Tab 2: 歷史回溯 ---
with tab2:
    st.subheader("📊 歷史標題動能比對")
    try:
        db = conn.read(worksheet="Sheet1")
        if not db.empty:
            # 讓使用者選日期，會列出該日所有題材
            dates = db['日期'].unique()[::-1]
            sel_date = st.selectbox("選擇日期", dates)
            sub_df = db[db['日期'] == sel_date]
            
            for _, row in sub_df.iterrows():
                with st.expander(f"📌 {row['題材']} (原因: {row['原因']})"):
                    sids = extract_stock_ids(row['標的'])
                    for sid in sids:
                        perf = get_stock_perf(sid)
                        if perf:
                            c1, c2 = st.columns(2)
                            c1.metric(f"{sid} 現價", f"{perf['price']:.2f}")
                            c2.metric("近一月幅度", f"{perf['change']:.2f}%")
        else:
            st.info("尚無歷史資料。")
    except:
        st.write("等待資料庫連線中...")

# --- Tab 3: 資料庫明細 ---
with tab3:
    st.subheader("📚 雲端資料庫原始清單")
    if 'db' in locals() and not db.empty:
        st.dataframe(db, use_container_width=True)
