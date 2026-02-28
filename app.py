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
st.set_page_config(page_title="AI 飆股偵測與週報資料庫", layout="wide", page_icon="📈")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    # 採用最新的 Gemini 模型
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"初始化失敗，請檢查 Secrets 設定: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def extract_stock_ids(text):
    """從文字中提取 4 位數台股代碼，並自動處理非字串資料"""
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    return re.findall(r'\b\d{4}\b', text)
def get_stock_perf(sid):
    """獲取台股即時行情與漲跌幅"""
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        t = yf.Ticker(f"{sid}{suffix}")
        h = t.history(period="1mo")
        if h.empty: return None
        cur = h['Close'].iloc[-1]
        chg = ((cur - h['Close'].iloc[0]) / h['Close'].iloc[0]) * 100
        return {"price": cur, "change": chg}
    except: return None

def check_breakout_dna(sid):
    """
    飆股起漲 DNA 偵測：
    1. 均線糾結度 < 3.5% (5/10/20 MA)
    2. 成交量萎縮 < 0.75 (相較於 20 日均量)
    3. 股價在 60MA (季線) 之上 (多頭架構)
    """
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        t = yf.Ticker(f"{sid}{suffix}")
        df = t.history(period="65d") 
        if len(df) < 60: return None
        
        # 計算指標
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        
        last = df.iloc[-1]
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        
        # 1. 均線糾結度
        ma_list = [last['MA5'], last['MA10'], last['MA20']]
        ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
        
        # 2. 量比
        v_ratio = last['Volume'] / vol_avg20 if vol_avg20 > 0 else 1
        
        # 3. 判定條件
        is_ready = (ma_gap < 3.5) and (v_ratio < 0.75) and (last['Close'] > last['MA60'])
        
        return {
            "sid": sid,
            "price": round(last['Close'], 2),
            "gap": round(ma_gap, 2),
            "v_ratio": round(v_ratio, 2),
            "is_ready": is_ready
        }
    except:
        return None

# --- 3. App 介面佈局 ---
st.title("📂 專業週報 JSON 結構化與飆股 DNA 偵測系統")
tab1, tab2, tab3, tab4 = st.tabs(["🚀 週報解析", "📅 歷史診斷", "📚 資料庫明細", "⚡ 飆股偵測器"])

# 預先讀取資料庫
try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame()

# --- Tab 1: 週報解析 ---
with tab1:
    up_col1, up_col2 = st.columns([3, 1])
    with up_col1:
        uploaded_file = st.file_uploader("上傳週報 PDF", type="pdf")
    with up_col2:
        re_analyze = st.button("🔄 重新分析")

    if uploaded_file:
        if 'json_analysis' not in st.session_state or re_analyze:
            with st.spinner('Gemini 正在進行深度結構化掃描...'):
                reader = PdfReader(uploaded_file)
                text = "".join([p.extract_text() for p in reader.pages if p.extract_text()])
                
                prompt = f"""
                你是一位專業股票分析員。請深入分析週報內容。
                請輸出一個 JSON 格式列表，不包含 Markdown 標籤或額外文字。
                格式：[{{"題材": "名稱", "原因": "10字內", "標的": "4位數代碼+名稱"}}]
                週報內容：{text[:15000]}
                """
                res = model.generate_content(prompt)
                st.session_state.json_analysis = res.text
                
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', uploaded_file.name)
                st.session_state.rep_date = date_match.group(0) if date_match else datetime.now().strftime("%Y-%m-%d")

        if 'json_analysis' in st.session_state:
            st.markdown(f"### 📋 {st.session_state.rep_date} 結構化結果")
            st.code(st.session_state.json_analysis, language='json')

            if st.button("📥 確認存入 Google Sheets"):
                try:
                    raw_str = st.session_state.json_analysis.replace('```json', '').replace('```', '').strip()
                    data_list = json.loads(raw_str)
                    new_df = pd.DataFrame(data_list)
                    new_df['日期'] = st.session_state.rep_date
                    final_df = pd.concat([db, new_df[['日期', '題材', '原因', '標的']]], ignore_index=True)
                    conn.update(worksheet="Sheet1", data=final_df)
                    st.success("✅ 數據已成功同步至雲端資料庫！")
                except Exception as e:
                    st.error(f"存入失敗: {e}")

# --- Tab 2: 歷史回溯 ---
with tab2:
    st.subheader("📊 歷史標題動能比對")
    if not db.empty:
        dates = db['日期'].unique()[::-1]
        sel_date = st.selectbox("選擇回溯日期", dates)
        sub_df = db[db['日期'] == sel_date]
        
        for _, row in sub_df.iterrows():
            with st.expander(f"📌 {row['題材']} - {row['標的']}"):
                sids = extract_stock_ids(row['標的'])
                for sid in sids:
                    perf = get_stock_perf(sid)
                    if perf:
                        c1, c2 = st.columns(2)
                        c1.metric(f"{sid} 現價", f"{perf['price']:.2f}")
                        c2.metric("近一月幅度", f"{perf['change']:.2f}%")
    else:
        st.info("資料庫尚無資料。")

# --- Tab 3: 資料庫明細 ---
with tab3:
    st.subheader("📚 雲端資料庫清單")
    # 修正：2026 Streamlit 規範，使用 width="stretch" 填滿寬度
    if not db.empty:
        st.dataframe(db, width="stretch")
    else:
        st.info("目前資料庫內沒有數據。")

# --- Tab 4: ⚡ 飆股偵測器 (雙模式：資料庫 vs 全台股) ---
with tab4:
    st.subheader("⚡ 尋找起漲點：均線糾結 + 窒息量偵測")
    
    # 模式選擇
    scan_mode = st.radio("選擇掃描範圍", ["資料庫內的題材股", "掃描全台股 (1101~9960)"], horizontal=True)
    
    # 門檻設定 (讓使用者微調，增加靈活性)
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        gap_threshold = st.slider("均線糾結門檻 (%)", 1.0, 5.0, 3.5, 0.5, help="數值越小代表籌碼越集中")
    with col_p2:
        vol_threshold = st.slider("窒息量門檻 (倍)", 0.1, 1.0, 0.75, 0.05, help="數值越小代表賣壓越乾淨")

    # 準備掃描清單
    search_list = []
    if scan_mode == "資料庫內的題材股":
        if not db.empty:
            all_sids = []
            for s in db['標的']: 
                clean_s = str(s) if pd.notna(s) else ""
                all_sids.extend(extract_stock_ids(clean_s))
            search_list = list(set(all_sids))
            st.info(f"🔍 模式：資料庫精準監控 (共 {len(search_list)} 檔)")
        else:
            st.warning("資料庫目前是空的，請先解析週報 PDF。")
    else:
        # 建立全台股代碼池 (1101~9960)
        # 註：這會包含一些不存在的號碼，但 check_breakout_dna 會自動跳過
        search_list = [str(i) for i in range(1101, 9961)]
        st.info("🚀 模式：全台股大數據掃描 (預計需時 2-5 分鐘)")

    # 執行掃描按鈕
    if st.button("🏁 開始 DNA 篩選"):
        if not search_list:
            st.error("掃描清單為空，請確認資料庫數據。")
        else:
            results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            start_time = time.time()
            total_count = len(search_list)
            
            # 遍歷清單
            for i, sid in enumerate(search_list):
                # 每 10 檔更新一次文字，避免畫面閃爍過快
                if i % 10 == 0 or i == total_count - 1:
                    status_text.text(f"正在分析: {sid} ({i}/{total_count})")
                
                # 呼叫技術面檢查函式
                res = check_breakout_dna(sid)
                
                # 符合使用者設定的門檻才納入
                if res and res['gap'] <= gap_threshold and res['v_ratio'] <= vol_threshold:
                    # check_breakout_dna 內部已包含 price > MA60 判斷
                    results.append(res)
                
                # 更新進度條
                progress_bar.progress((i + 1) / total_count)
            
            end_time = time.time()
            status_text.success(f"掃描完成！總耗時: {int(end_time - start_time)} 秒")
            
            if results:
                st.success(f"🎊 發現 {len(results)} 檔符合起漲特徵的標的！")
                
                # 整理成 DataFrame
                res_df = pd.DataFrame(results).drop(columns=['is_ready'])
                res_df.columns = ['股票代號', '目前價格', '均線糾結度(%)', '成交量比']
                
                # 依據『均線糾結度』排序 (越小越好)
                res_df = res_df.sort_values(by='均線糾結度(%)', ascending=True)
                
                # 顯示表格 (使用 2026 最新規範 width="stretch")
                st.dataframe(res_df, width="stretch")
                
                st.caption("💡 專業建議：優先觀察均線糾結度 < 2% 且成交量比 < 0.5 的標的，代表爆發力最強。")
            else:
                st.info("目前選定範圍內，尚無標的同時滿足設定的起漲條件。")
