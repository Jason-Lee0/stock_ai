import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
import io

# ==========================================
# 1. 直接永存您的 API Key (在此替換)
# ==========================================
MY_GEMINI_API_KEY = "AIzaSyC73yQvhiVh0b4JtmpiU0GrPnYIYBXQURI" 
# ==========================================

# 網頁基礎設定
st.set_page_config(page_title="股籌 AI 分析助手", layout="centered")

# 初始化 Gemini
try:
    genai.configure(api_key=MY_GEMINI_API_KEY)
    # 使用包含 -latest 的名稱來避免 404 錯誤
    model = genai.GenerativeModel('gemini-pro')
except Exception as e:
    st.error(f"API 設定失敗: {e}")

st.title("📱 股籌週報 AI 分析助手")
st.markdown("---")

# 檔案上傳區
uploaded_file = st.file_uploader("📤 上傳週報 PDF", type="pdf")

if uploaded_file is not None:
    with st.spinner('Gemini 正在深入閱讀週報中...'):
        try:
            # 讀取 PDF 文字
            reader = PdfReader(uploaded_file)
            full_text = ""
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    full_text += text

            # 專業分析指令
            prompt = f"""
            你是一位專業的台灣股市分析師。請針對以下週報內容進行結構化整理，輸出格式必須非常適合手機閱讀。
            
            請分為以下三個區塊整理：
            1. **【大盤風向】**：用一句話總結本週情緒，並列出支撐、壓力位。
            2. **【產業雷達】**：提取本週最重要的 3 個產業趨勢。
            3. **【個股動能】**：針對週報中提到最關鍵的個股，列出名稱代碼及一兩句核心亮點。
            
            週報內容：
            {full_text[:12000]} 
            """

            # 呼叫 Gemini
            response = model.generate_content(prompt)
            
            # 顯示結果
            st.subheader("💡 AI 整理精華")
            st.markdown(response.text)
            st.success("✅ 分析完成")
            
        except Exception as e:
            st.error(f"分析發生錯誤: {e}")

# 底部簡單選單
st.markdown("---")
st.caption("目前功能：週報整理 | 開發中：股票篩選、籌碼分析")
