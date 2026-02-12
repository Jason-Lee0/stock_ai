import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader

# ==========================================
# 🔐 從 Streamlit Secrets 自動讀取密鑰
# ==========================================
try:
    # 這裡的 "GEMINI_KEY" 要跟你在後台設定的名稱一樣
    MY_GEMINI_API_KEY = st.secrets["GEMINI_KEY"]
    genai.configure(api_key=MY_GEMINI_API_KEY)
    generation_config = {
    "temperature": 0.1,  # 接近 0 確保每次輸出高度一致
    "top_p": 0.95,
    "max_output_tokens": 2048,
    }
    model = genai.GenerativeModel(model_name='gemini-2.5-flash',
                                 generation_config=generation_config)
except Exception as e:
    st.error("❌ 找不到 API Key 或設定錯誤，請檢查 Streamlit Secrets 設定。")
    st.stop() # 停止執行後續程式
# ==========================================

# 網頁基礎設定 (手機優化)
st.set_page_config(page_title="股籌 AI 分析助手", layout="centered")

st.title("📱 股籌週報 AI 分析助手")
st.caption("2026 智慧投資版 | 上傳即分析")
st.markdown("---")

# 檔案上傳區
uploaded_file = st.file_uploader("📤 請選擇週報 PDF 文件", type="pdf")

if uploaded_file is not None:
    with st.spinner('Gemini 正在拆解週報內容...'):
        try:
            # 讀取 PDF
            reader = PdfReader(uploaded_file)
            full_text = "".join([page.extract_text() for page in reader.pages if page.extract_text()])

            # AI 分析指令
           prompt = f"""
            你是一位嚴謹的台股量化分析師。你的任務是從「週報原文」中提取核心資訊，並將其標準化以供資料庫儲存。
            
            ### 執行規則：
            1. **客觀優先**：只提取原文提到的事實與數據，不加入個人推測。
            2. **標籤歸一化**：從以下【標準族群清單】中選擇最符合的標籤，不要自創。
               【標準族群清單】：AI伺服器、半導體、設備、機器人、電力電纜、重電、散熱、PCB、車用、原物料。
            3. **穩定格式**：請嚴格依照下方的結構輸出，不要有任何多餘的解釋文字。
            
            ### 輸出結構：
            
            【大盤情緒】：(請從：極度樂觀、偏多、震盪、偏空、極度悲觀 中選一個)
            【核心觀點】：(用一句話總結本週最重要的大盤結論，限 30 字內)
            【族群標籤】：(從標準清單選取，用逗號隔開)
            【重點個股】：(格式為：代碼 名稱 - 核心動能摘要，例如：3035 智原 - 先進製程案量提升)
            【詳細分析】：(300字以內的核心細節整理)
            
            ### 週報原文：
            {full_text[:12000]}
            """

            response = model.generate_content(prompt)
            
            # 顯示結果卡片
            st.markdown("### 💡 AI 整理精華")
            st.info(response.text)
            st.success("✅ 分析完成")
            
        except Exception as e:
            st.error(f"分析失敗：{e}")

# 底部簡單版導覽
st.markdown("---")
cols = st.columns(3)
cols[0].button("📄 週報整理", use_container_width=True)
cols[1].button("🔍 股票篩選", use_container_width=True, disabled=True)
cols[2].button("📊 籌碼分析", use_container_width=True, disabled=True)
