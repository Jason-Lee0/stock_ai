import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
import io

# 1. 網頁基礎設定 (優化手機顯示)
st.set_page_config(page_title="股籌 AI 分析助手", layout="centered")

# 2. 安全地讀取 API Key (建議在部署平台設定為 Secret)
# 如果在本地測試，請直接替換為 "你的API_KEY"
gemini_key = st.sidebar.text_input("請輸入 Gemini API Key", type="password")

if gemini_key:
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel('gemini-1.5-flash')

    st.title("📱 股籌週報 AI 分析助手")
    st.markdown("---")

    # 3. 檔案上傳區
    uploaded_file = st.file_uploader("📤 上傳週報 PDF", type="pdf")

    if uploaded_file is not None:
        with st.spinner('Gemini 正在深入閱讀週報中，請稍候...'):
            # 讀取 PDF 文字
            reader = PdfReader(uploaded_file)
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text()

            # 4. 設計專業的 AI 提示詞 (Prompt)
            prompt = f"""
            你是一位專業的台灣股市分析師。請針對以下週報內容進行結構化整理，輸出格式必須非常適合手機閱讀。
            
            請分為以下三個區塊整理：
            1. **【大盤風向】**：用一句話總結本週情緒，並列出支撐、壓力位。
            2. **【產業雷達】**：提取本週最重要的 3 個產業趨勢。
            3. **【個股動能】**：針對週報中提到最關鍵的個股，列出名稱代碼及一兩句核心亮點。
            
            週報內容：
            {full_text[:10000]}  # 限制字數避免超過 token
            """

            # 5. 呼叫 Gemini 並顯示結果
            try:
                response = model.generate_content(prompt)
                
                # 顯示 AI 分析結果
                st.subheader("💡 AI 整理精華")
                st.markdown(response.text)
                
                st.success("✅ 分析完成")
                
            except Exception as e:
                st.error(f"分析發生錯誤: {e}")

else:
    st.warning("👈 請在左側選單輸入您的 Gemini API Key 以啟動服務。")

# 6. 底部導覽列模擬 (CSS 優化)
st.markdown("""
    <style>
    .report-footer {
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        background-color: #0e1117;
        padding: 10px;
        text-align: center;
        border-top: 1px solid #334155;
    }
    </style>
    <div class="report-footer">
        <span style="color: #38bdf8; font-size: 12px;">週報整理 | 股票篩選(開發中) | 籌碼分析(開發中)</span>
    </div>
    """, unsafe_allow_html=True)
