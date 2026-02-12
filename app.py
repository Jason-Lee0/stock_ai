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
    model = genai.GenerativeModel('gemini-pro')
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
            你是一位專業分析師。請針對以下週報內容，提取重點並以適合手機閱讀的格式輸出：
            1. 【大盤總結】：本週核心多空觀點、壓力與支撐。
            2. 【關鍵產業】：列出 2-3 個最具潛力的族群與原因。
            3. 【焦點個股】：列出週報提到的重點股（名稱代碼、看好理由）。
            
            內容如下：
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
