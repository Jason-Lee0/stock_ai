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
    st.subheader("📤 上傳與深度分析")
    
    up_col1, up_col2 = st.columns([3, 1])
    with up_col1:
        uploaded_file = st.file_uploader("請上傳 PDF 週報檔案", type="pdf")
    with up_col2:
        re_analyze = st.button("🔄 重新執行 AI 分析")

    if uploaded_file:
        if 'ai_analysis' not in st.session_state or re_analyze:
            with st.spinner('專業分析員正在從文字與圖表中提取標籤...'):
                reader = PdfReader(uploaded_file)
                current_text = "".join([p.extract_text() for p in reader.pages if p.extract_text()])
                
                # 優化後的 Prompt：針對主題、短原因、標的
                prompt = f"""
                你是一位股票專業週報分析員。請針對週報中的各個主題（含文字、表格及圖表）進行分析。
                請嚴格依照以下格式輸出，不准有前言或結語：

                【題材】：(產業名稱)
                - 原因：(限 10 字以內，例如：報價上漲、政策紅利)
                - 標的：(4位數代碼+名稱，多個請用逗號隔開)

                【題材】：(下一個主題...)
                ...

                週報原文內容：
                {current_text[:15000]}
                """
                
                response = model.generate_content(prompt)
                st.session_state.ai_analysis = response.text
                st.session_state.stock_ids = ", ".join(extract_stock_ids(response.text))
                st.session_state.report_date = re.search(r'\d{4}-\d{2}-\d{2}', uploaded_file.name).group(0) if re.search(r'\d{4}-\d{2}-\d{2}', uploaded_file.name) else datetime.now().strftime("%Y-%m-%d")

        # --- 顯示與存入 ---
        if 'ai_analysis' in st.session_state:
            st.markdown(f"### 💡 {st.session_state.report_date} 分析結果")
            st.code(st.session_state.ai_analysis) # 使用 code 區塊顯示更整齊，不易卡頓

            with st.form("save_to_sheets"):
                st.write("檢查摘要無誤後存入資料庫：")
                submit = st.form_submit_button("📥 確定寫入 Google Sheets")
                if submit:
                    try:
                        db = conn.read(worksheet="Sheet1")
                        new_data = pd.DataFrame([{
                            "日期": st.session_state.report_date,
                            "核心主題": "多題材掃描",
                            "產業族群": "自動標籤",
                            "重點個股": st.session_state.stock_ids,
                            "完整報告": st.session_state.ai_analysis[:5000] # 限制存入字數防止超時
                        }])
                        updated_db = pd.concat([db, new_data], ignore_index=True)
                        conn.update(worksheet="Sheet1", data=updated_db)
                        st.success("✅ 已同步至雲端！現在可以去『歷史回溯』診斷這些標的了。")
                    except Exception as e:
                        st.error(f"存入失敗：{e}")
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
