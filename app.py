import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import re
from datetime import datetime

# --- 1. 核心初始化與 Secrets ---
st.set_page_config(page_title="專業週報分析基地", layout="wide")

try:
    # Gemini 設定
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    # 設定低溫度值確保分析穩定
    generation_config = {"temperature": 0.1, "top_p": 0.95, "max_output_tokens": 4096}
    model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)
    
    # Google Sheets 連線
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"❌ 初始化失敗，請檢查 Secrets 設定: {e}")
    st.stop()

# --- 2. 輔助函式庫 ---
def extract_stock_ids(text):
    """自動從文字中提取 4 位數台股代碼"""
    return re.findall(r'\b\d{4}\b', text)

def get_stock_performance(stock_id):
    """抓取台股近一個月表現"""
    try:
        ticker = yf.Ticker(f"{stock_id}.TW")
        hist = ticker.history(period="1mo")
        if hist.empty: return None
        current = hist['Close'].iloc[-1]
        start = hist['Close'].iloc[0]
        change = ((current - start) / start) * 100
        return {"current": current, "change": change}
    except:
        return None

# --- 3. App 介面導覽 ---
st.title("📂 股票專業週報分析系統")
tab1, tab2, tab3 = st.tabs(["🚀 上傳更新", "📅 歷史回溯診斷", "📚 雲端資料庫"])
with tab1:
    st.subheader("📤 上傳與當週分析")
    
    # 使用列配置讓按鈕橫向排列
    up_col1, up_col2 = st.columns([3, 1])
    with up_col1:
        uploaded_file = st.file_uploader("請上傳 PDF 週報檔案", type="pdf")
    with up_col2:
        st.write("") # 調整間距
        st.write("") 
        re_analyze = st.button("🔄 重新執行 AI 分析")

    # 邏輯控制：當檔案存在，且 (尚未分析過 OR 使用者點擊重新分析)
    if uploaded_file:
        if 'ai_analysis' not in st.session_state or re_analyze:
            with st.spinner('專業分析員正在深度閱卷中...'):
                try:
                    # 1. 提取 PDF 文字
                    reader = PdfReader(uploaded_file)
                    current_text = "".join([p.extract_text() for p in reader.pages if p.extract_text()])
                    
                    # 2. 讀取歷史紀錄 (給 AI 參考趨勢)
                    try:
                        history_df = conn.read(worksheet="Sheet1")
                        history_context = history_df.tail(5).to_string() if not history_df.empty else "尚無歷史紀錄"
                    except:
                        history_df = pd.DataFrame()
                        history_context = "尚未建立資料表"

                    # 3. 呼叫 Gemini
                    # 獲取檔案日期 (嘗試從檔名抓取，若無則用今天)
                    file_date = re.search(r'\d{4}-\d{2}-\d{2}', uploaded_file.name)
                    st.session_state.report_date = file_date.group(0) if file_date else datetime.now().strftime("%Y-%m-%d")

                    prompt = f"""
                    你是一位專業股票分析員。請針對「當週週報」進行分類，並參考「歷史紀錄」來對比趨勢變化。

                    ### 歷史紀錄參考：
                    {history_context}

                    ### 當週週報內容：
                    {current_text[:12000]}

                    ### 任務要求：
                    1. 分類資訊：提取「核心主題、產業族群、提及原因時間、個股亮點」。
                    2. 趨勢比對：若族群或個股已在歷史中出現，標註【動能延續】；若新出現標註【新啟動】。
                    3. 偵測數據：若有「外銷訂單」數據請整理，無則跳過。
                    """
                    
                    response = model.generate_content(prompt)
                    # 將結果存入暫存，避免頁面重新整理時消失
                    st.session_state.ai_analysis = response.text
                    st.session_state.stock_ids = ", ".join(extract_stock_ids(response.text))
                
                except Exception as e:
                    st.error(f"分析過程發生錯誤: {e}")

        # --- 顯示分析結果 ---
        if 'ai_analysis' in st.session_state:
            st.markdown("---")
            st.markdown(f"### 💡 {st.session_state.report_date} 分析報告")
            st.info(st.session_state.ai_analysis)

            # --- 存入資料庫區塊 ---
            st.write("確認無誤後，將分析結果存入雲端備份：")
            if st.button("📥 寫入 Google Sheets 資料庫"):
                try:
                    # 再次讀取最新資料以免覆蓋
                    current_db = conn.read(worksheet="Sheet1")
                    new_entry = pd.DataFrame([{
                        "日期": st.session_state.report_date,
                        "核心主題": "本週趨勢分析", # 可進階解析摘要
                        "產業族群": "自動偵測中",
                        "重點個股": st.session_state.stock_ids,
                        "完整報告": st.session_state.ai_analysis
                    }])
                    
                    updated_db = pd.concat([current_db, new_entry], ignore_index=True)
                    conn.update(worksheet="Sheet1", data=updated_db)
                    st.success(f"✅ 已成功將 {st.session_state.report_date} 紀錄存入！")
                    # 清除暫存，防止重複存入
                    # del st.session_state.ai_analysis 
                except Exception as e:
                    st.error(f"存入失敗：{e}\n請確認您已將 Google Sheets 分享給 JSON 裡的 client_email 並設為編輯者。")
    else:
        st.write("👋 歡迎回來！請上傳週報 PDF 開始進行專業分析。")
# --- Tab 2: 歷史回溯與即時診斷 ---
with tab2:
    st.subheader("📅 歷史標的回顧與 AI 診斷")
    try:
        df = conn.read(worksheet="Sheet1")
        if not df.empty:
            dates = df['日期'].unique().tolist()
            selected_date = st.selectbox("選擇要回溯的週報日期", dates[::-1])
            
            # 抓取該週資料
            record = df[df['日期'] == selected_date].iloc[0]
            st.markdown(f"**當週分析回顧：**")
            st.caption(record['完整報告'])
            
            # 提取個股進行診斷
            sids = extract_stock_ids(record['重點個股'])
            if sids:
                st.markdown("---")
                st.write(f"🔍 偵測到 {len(sids)} 檔標的，進行現況追蹤：")
                
                for sid in sids:
                    perf = get_stock_performance(sid)
                    if perf:
                        with st.expander(f"📈 股票代碼：{sid}"):
                            c1, c2 = st.columns(2)
                            c1.metric("目前價格", f"{perf['current']:.2f}")
                            c2.metric("近一月漲跌", f"{perf['change']:.2f}%")
                            
                            if st.button(f"執行 AI 現況診斷 ({sid})", key=f"diag_{sid}"):
                                diag_prompt = f"該股 {sid} 在 {selected_date} 被看好，原因為：{record['完整報告'][:500]}。目前股價 {perf['current']}，漲跌幅 {perf['change']}%。請分析當時看好的邏輯現在是否還成立？"
                                diag_res = model.generate_content(diag_prompt)
                                st.write(diag_res.text)
            else:
                st.warning("該週紀錄中未發現標準股票代碼。")
        else:
            st.info("資料庫目前是空的。")
    except:
        st.error("無法讀取資料庫，請確認 Google Sheets 設定。")

# --- Tab 3: 原始資料庫 ---
with tab3:
    st.subheader("📚 雲端資料庫全紀錄 (Google Sheets)")
    try:
        raw_df = conn.read(worksheet="Sheet1")
        st.dataframe(raw_df, use_container_width=True)
    except:
        st.write("尚未有資料存入。")

# 底部導覽
st.markdown("---")
st.caption("AI 股票分析員 v1.0 | 數據源：Gemini 1.5 Flash & Yahoo Finance")
