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
    st.subheader("📤 上傳本週週報")
    uploaded_file = st.file_uploader("請上傳 PDF 週報檔案", type="pdf", key="pdf_uploader")
    
    if uploaded_file:
        # 建立一個容器，方便重新分析時刷新內容
        analysis_container = st.empty()
        
        # 提取文字 (移到外面避免重複讀取檔案)
        reader = PdfReader(uploaded_file)
        current_text = "".join([p.extract_text() for p in reader.pages if p.extract_text()])

        # 定義分析函式
        def run_analysis():
            with st.spinner('專業分析員閱卷中...'):
                try:
                    history_df = conn.read(worksheet="Sheet1")
                    history_context = history_df.tail(5).to_string() if not history_df.empty else "尚無歷史紀錄"
                except:
                    history_df = pd.DataFrame()
                    history_context = "尚未建立資料表"

                prompt = f"""
                你是一位股票專業週報分析員。請針對「當週週報」進行分類，並參考「歷史紀錄」來對比趨勢變化。
                ### 歷史紀錄參考：
                {history_context}
                ### 當週週報內容：
                {current_text[:12000]}
                ### 任務要求：
                1. 分類資訊：提取「核心主題、產業族群、提及原因時間、個股亮點」。
                2. 趨勢比對：若族群或個股已在歷史中出現，標註【動能延續】；若新出現標註【新啟動】。
                """
                return model.generate_content(prompt), history_df

        # 初始執行分析
        if "analysis_result" not in st.session_state or st.button("🔄 重新分析"):
            response, history_df = run_analysis()
            st.session_state.analysis_result = response.text
            st.session_state.history_df = history_df

        # 顯示結果
        st.markdown("### 💡 本週分析報告")
        st.info(st.session_state.analysis_result)

        # 儲存功能
        if st.button("📥 確認存入雲端資料庫"):
            # 使用 session_state 中的結果存檔
            sids = extract_stock_ids(st.session_state.analysis_result)
            new_row = pd.DataFrame([{
                "日期": datetime.now().strftime("%Y-%m-%d"),
                "核心主題": "已分析內容", 
                "產業族群": "偵測族群中",
                "重點個股": ", ".join(sids),
                "完整報告": st.session_state.analysis_result
            }])
            updated_df = pd.concat([st.session_state.history_df, new_row], ignore_index=True)
            conn.update(worksheet="Sheet1", data=updated_df)
            st.success("✅ 資料已同步至 Google Sheets！")
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
