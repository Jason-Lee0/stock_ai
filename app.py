import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import twstock
import re
import json
import time
import datetime
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 1. 系統初始化與設定 ---
st.set_page_config(page_title="AI 飆股診斷系統 v4.1", layout="wide", page_icon="🛡️")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗，請檢查 Secrets 設定: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def get_taiwan_stock_tickers():
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if "權證" in info.type or "ETF" in info.type: continue
        if info.market == "上市":
            taiwan_tickers.append(f"{code}.TW")
        elif info.market == "上櫃":
            taiwan_tickers.append(f"{code}.TWO")
    return list(set(taiwan_tickers))

def check_breakout_dna_stable(ticker, g_limit, v_limit, min_volume=500):
    today = datetime.date.today()
    if today.weekday() >= 5:
        end_date = today - datetime.timedelta(days=today.weekday() - 4)
    else:
        end_date = today
    start_date = end_date - datetime.timedelta(days=400)

    for attempt in range(3):
        try:
            df = yf.Ticker(ticker).history(start=start_date, end=end_date)
            if df.empty or len(df) < 245: return None
            last = df.iloc[-1]
            vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
            
            # --- 新增：流動性過濾器 ---
            # Yahoo Finance 的 Volume 是「股數」，所以要除以 1000 變成「張數」
            current_vol_lots = last['Volume'] / 1000 
            avg_vol_lots = vol_avg20 / 1000
            
            # 如果今天不到 500 張，或是平均不到 300 張，直接跳過
            if current_vol_lots < min_volume or avg_vol_lots < 300:
                return None
            df['MA5'] = df['Close'].rolling(5).mean()
            df['MA10'] = df['Close'].rolling(10).mean()
            df['MA20'] = df['Close'].rolling(20).mean()
            df['MA60'] = df['Close'].rolling(60).mean()
            df['MA240'] = df['Close'].rolling(240).mean()
            
            exp1 = df['Close'].ewm(span=12, adjust=False).mean()
            exp2 = df['Close'].ewm(span=26, adjust=False).mean()
            df['DIF'] = exp1 - exp2
            df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
            df['MACD_Hist'] = df['DIF'] - df['DEA']
            
            last = df.iloc[-1]
            prev = df.iloc[-2]
            
            ma_list = [last['MA5'], last['MA10'], last['MA20']]
            gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
            vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
            v_ratio = round(last['Volume'] / vol_avg20, 2) if vol_avg20 > 0 else 1
            
            is_ma60_up = last['MA60'] > df['MA60'].iloc[-5]
            if gap <= g_limit and v_ratio <= v_limit and last['Close'] > last['MA60'] and is_ma60_up:
                return {
                    "sid": ticker,
                    "price": round(last['Close'], 2),
                    "gap": gap,
                    "v_ratio": v_ratio,
                    "type": "🚀 可能飆股 (長線無壓)" if last['Close'] > last['MA240'] else "🩹 補漲股 (年線壓力)",
                    "signal": "🔥 轉強" if last['MACD_Hist'] > prev['MACD_Hist'] else "⏳ 整理"
                }
            return None
        except:
            time.sleep(1)
    return None

def plot_interactive_chart(ticker):
    try:
        df = yf.Ticker(ticker).history(period="300d")
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        df['MA240'] = df['Close'].rolling(240).mean()
        
        colors = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        
        fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
        for ma, color in zip(['MA5', 'MA20', 'MA60', 'MA240'], ['white', 'yellow', 'orange', 'purple']):
            fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=color, width=1.5)), row=1, col=1)
        
        fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=colors, showlegend=False), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=50, b=5))
        return fig
    except: return None


# --- 1. 定義彈出視窗函式 ---
@st.dialog("📈 飆股 DNA 深度診斷", width="large")
def show_stock_dialog(ticker):
    st.write(f"### 正在診斷：{ticker}")
    with st.spinner("載入大數據 K 線圖..."):
        fig = plot_interactive_chart(ticker)
        if fig:
            st.plotly_chart(fig, width='stretch')
        else:
            st.error("無法載入此標的之圖表。")
    
    # 底部加上關閉按鈕，方便手機操作
    if st.button("關閉診斷", use_container_width=True):
        st.rerun()

# --- 3. UI 介面佈局 ---
tab1, tab2, tab3, tab4 = st.tabs(["📄 週報解析", "📅 歷史診斷", "📚 資料庫明細", "⚡ 飆股偵測器"])

try:
    db = conn.read(worksheet="Sheet1")
except:
    db = pd.DataFrame()

# --- Tab 1: 週報解析 ---
with tab1:
    st.subheader("📄 AI 投顧週報深度解析")
    uploaded_file = st.file_uploader("上傳投顧週報 PDF", type="pdf")
    if uploaded_file:
        if st.button("🚀 啟動 AI 標的提取"):
            with st.spinner('Gemini 正在讀取並解析題材...'):
                reader = PdfReader(uploaded_file)
                full_text = "".join([p.extract_text() for p in reader.pages])
                prompt = f"請將週報內容轉為 JSON 列表，格式：[{{\"題材\": \"\", \"原因\": \"\", \"標的\": \"代碼+名稱\"}}]。文字：{full_text[:8000]}"
                response = model.generate_content(prompt)
                st.session_state.raw_json = response.text
                st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")

        if 'raw_json' in st.session_state:
            st.code(st.session_state.raw_json, language='json')
            if st.button("📥 存入雲端資料庫"):
                try:
                    clean_json = st.session_state.raw_json.replace('```json', '').replace('```', '').strip()
                    new_df = pd.DataFrame(json.loads(clean_json))
                    new_df['日期'] = st.session_state.rep_date
                    updated_db = pd.concat([db, new_df], ignore_index=True)
                    conn.update(worksheet="Sheet1", data=updated_db)
                    st.success("成功存入 Google Sheets！")
                except Exception as e: st.error(f"存檔失敗: {e}")

# --- Tab 3: 資料庫明細 ---
with tab3:
    st.subheader("📚 雲端監控題材清單")
    if not db.empty:
        search_q = st.text_input("🔍 搜尋代碼或題材關鍵字")
        display_db = db[db.astype(str).apply(lambda x: x.str.contains(search_q)).any(axis=1)]
        st.dataframe(display_db, width='stretch') # 已修正
    else: st.info("資料庫目前沒有數據。")

# --- Tab 4: 飆股偵測器 ---
# --- Tab 4: 飆股偵測器 (修正後的穩定記憶版) ---
# --- 1. 定義彈出視窗函式 ---
@st.dialog("📈 飆股 DNA 深度診斷", width="large")
def show_stock_dialog(ticker):
    st.write(f"### 正在診斷：{ticker}")
    with st.spinner("載入大數據 K 線圖..."):
        fig = plot_interactive_chart(ticker)
        if fig:
            st.plotly_chart(fig, width='stretch')
        else:
            st.error("無法載入此標的之圖表。")
    
    # 底部加上關閉按鈕，方便手機操作
    if st.button("關閉診斷", use_container_width=True):
        st.rerun()
with tab4:
    st.subheader("⚡ 飆股 DNA 大數據掃描")
    col_l, col_r = st.columns(2)
    with col_l:
        mode = st.radio("範圍", ["資料庫題材股", "全台股 (1101~9960)"], horizontal=True)
    with col_r:
        g_limit = st.slider("糾結度 (%)", 1.0, 5.0, 3.5)
        v_limit = st.slider("量比門檻 (窒息量)", 0.1, 1.2, 0.75)

    # 按鈕觸發掃描
    if st.button("🏁 開始執行高速偵測"):
        search_list = []
        if mode == "資料庫題材股":
            sids = []
            for s in db['標的'].astype(str): sids.extend(re.findall(r'\b\d{4}\b', s))
            search_list = [f"{s}.TW" if int(s)<9000 else f"{s}.TWO" for s in list(set(sids))]
        else:
            with st.spinner("獲取全台股清單中..."):
                search_list = get_taiwan_stock_tickers()

        if search_list:
            hits = []
            start_time = time.time()
            progress = st.progress(0)
            status = st.empty()

            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                future_to_sid = {executor.submit(check_breakout_dna_stable, sid, g_limit, v_limit): sid for sid in search_list}
                for i, future in enumerate(concurrent.futures.as_completed(future_to_sid)):
                    res = future.result()
                    if res: hits.append(res)
                    if i % 30 == 0:
                        progress.progress((i + 1) / len(search_list))
                        status.text(f"已掃描: {i+1} / {len(search_list)} 檔...")

            status.success(f"⚡ 掃描完成！耗時: {int(time.time()-start_time)} 秒")
            
            # --- 關鍵修正：將結果存入 session_state ---
            if hits:
                st.session_state.final_hits_df = pd.DataFrame(hits).sort_values('gap')
                st.session_state.final_hits_df.columns = ['代號', '現價', '糾結(%)', '量比', '長線屬性', '動能']
            else:
                st.session_state.final_hits_df = None
                st.warning("查無符合 DNA 的標的。")

  if 'final_hits_df' in st.session_state and st.session_state.final_hits_df is not None:
        st.write("### 🔍 偵測結果 (點選任一橫列直接彈出診斷)")
        
        # 修正點：selection_mode 從 "single" 改為 "single-row"
        event = st.dataframe(
            st.session_state.final_hits_df,
            width='stretch',
            on_select="rerun",
            selection_mode="single-row",  # 這裡要改成 single-row
            hide_index=True,
            column_config={
                "代號": st.column_config.TextColumn("代號", disabled=True),
                "現價": st.column_config.NumberColumn("現價", disabled=True),
                "糾結(%)": st.column_config.NumberColumn("糾結(%)", disabled=True),
                "量比": st.column_config.NumberColumn("量比", disabled=True),
                "長線屬性": st.column_config.TextColumn("長線屬性", disabled=True),
                "動能": st.column_config.TextColumn("動能", disabled=True),
            }
        )

        # 偵測點擊列的事件邏輯也需要微調
        if event.selection.rows:
            selected_row_index = event.selection.rows[0]
            # 取得選中那一列的「代號」
            selected_sid = st.session_state.final_hits_df.iloc[selected_row_index]['代號']
            
            # 觸發我們之前定義好的彈出視窗
            show_stock_dialog(selected_sid)

        # 下載按鈕 (這部分保持不變)
        csv = st.session_state.final_hits_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載今日偵測清單", csv, "hits.csv", "text/csv")
