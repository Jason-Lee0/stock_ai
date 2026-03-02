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

# --- 1. 系統初始化 (行動端寬度自適應) ---
st.set_page_config(page_title="AI 飆股診斷 v5.5", layout="wide", page_icon="🛡️")

# 初始化記憶體，確保手機翻轉或刷新時數據不遺失
for key in ['v55_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state:
        st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗，請檢查 Secrets 設定: {e}")
    st.stop()

# --- 2. 核心運算引擎 (採用 Ticker 穩定模式) ---

def get_taiwan_stock_tickers():
    """獲取台股清單並排除非個股標的"""
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if any(x in info.type for x in ["權證", "ETF", "受益證券"]): continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return sorted(list(set(taiwan_tickers)))

def check_breakout_v55(ticker, g_limit, v_limit, min_v, bias_range, use_bias):
    """均線糾結 + 位階過濾偵測核心"""
    try:
        stock_obj = yf.Ticker(ticker)
        df = stock_obj.history(period="400d")
        
        if df.empty or len(df) < 245: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        
        # 數據清洗：移除無效交易日與 NaN
        df = df[df['Volume'] > 0].dropna()
        if len(df) < 240: return None
        
        last = df.iloc[-1]
        # 成交張數過濾 (Volume/1000)
        if (last['Volume'] / 1000) < min_v: return None
        
        close = df['Close']
        ma5, ma10, ma20 = close.rolling(5).mean().iloc[-1], close.rolling(10).mean().iloc[-1], close.rolling(20).mean().iloc[-1]
        ma60, ma240 = close.rolling(60).mean().iloc[-1], close.rolling(240).mean().iloc[-1]
        
        # 季年位階篩選 (可選功能)
        ma_bias = round(((ma60 / ma240) - 1) * 100, 2)
        if use_bias and not (bias_range[0] <= ma_bias <= bias_range[1]): return None
        
        # 短線 5/10/20MA 糾結度計算
        ma_list = [float(ma5), float(ma10), float(ma20)]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        
        # 成交量比 (與20日均量相比)
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = round(last['Volume'] / vol_avg20, 2)
        
        if gap <= g_limit and v_ratio <= v_limit:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "類股": info.category if info else "其他", "現價": round(float(last['Close']), 2),
                "糾結(%)": gap, "位階(%)": ma_bias, "量比": v_ratio,
                "屬性": "📈 多頭" if ma_bias > 0 else "🩹 底部"
            }
    except: return None

def get_historical_theme_ai(ticker, name):
    """AI 考古：分析該股半年內最強利多"""
    try:
        df = yf.download(ticker, period="6mo", progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        date_str = max_day.strftime('%Y-%m-%d')
        prompt = f"分析台股 {name}({ticker})。該股在 {date_str} 前後大幅上漲。請簡述利多原因（如營收、題材、產品），限 40 字。"
        return f"📅 {date_str} 考古：{model.generate_content(prompt).text}"
    except: return "AI 考古分析暫時無法載入。"

def plot_stock_chart(ticker):
    """繪製專業 K 線圖"""
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    for p in [5, 20, 60, 240]: df[f'MA{p}'] = df['Close'].rolling(p).mean()
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    for ma, col in zip(['MA5','MA20','MA60','MA240'], ['white','yellow','orange','purple']):
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=col, width=1.5)), row=1, col=1)
    
    v_colors = ['red' if df['Close'].iloc[i] >= df['Open'].iloc[i] else 'green' for i in range(len(df))]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="量", marker_color=v_colors), row=2, col=1)
    
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=10, b=5))
    return fig

@st.dialog("🚀 AI 飆股診斷室", width="large")
def show_diagnosis(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 正在翻閱歷史檔案..."):
        st.info(get_historical_theme_ai(ticker, name))
    st.plotly_chart(plot_stock_chart(ticker), use_container_width=True)
    if st.button("關閉診斷", use_container_width=True): st.rerun()

# --- 3. UI 介面配置 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📈 績優回測", "📚 雲端庫", "⚡ 飆股偵測器"])

# 載入雲端資料庫
try:
    db = conn.read(worksheet="Sheet1").dropna(subset=['標的'])
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

with tab1:
    st.subheader("📄 PDF 週報解析")
    pdf = st.file_uploader("上傳投顧週報 PDF", type="pdf")
    if pdf and st.button("🚀 啟動解析", use_container_width=True):
        reader = PdfReader(pdf)
        text = "".join([p.extract_text() for p in reader.pages])
        prompt = "將以下文字轉為 JSON 列表，包含(題材, 原因, 標的)。標的請保留數字代號。"
        res = model.generate_content(f"{prompt}: {text[:8000]}").text
        st.session_state.raw_json = res
        st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")
    
    if st.session_state.raw_json:
        st.code(st.session_state.raw_json)
        if st.button("📥 存入 Google Sheets", use_container_width=True):
            clean = st.session_state.raw_json.replace('```json', '').replace('```', '').strip()
            new_data = pd.DataFrame(json.loads(clean))
            new_data['日期'] = st.session_state.rep_date
            conn.update(worksheet="Sheet1", data=pd.concat([db, new_data], ignore_index=True))
            st.success("數據已同步至雲端資料庫！")

with tab2:
    st.subheader("📈 歷史表現回測")
    if st.button("🏁 開始回測最近 10 筆標的", use_container_width=True):
        bt_results = []
        for _, r in db.tail(10).iterrows():
            m = re.search(r'\d{4}', str(r['標的']))
            if m:
                s = f"{m.group(0)}.TW" if int(m.group(0)) < 9000 else f"{m.group(0)}.TWO"
                h = yf.download(s, start=r['日期'], progress=False)
                if not h.empty:
                    p0, pn = h['Close'].iloc[0], h['Close'].iloc[-1]
                    bt_results.append({"標的": r['標的'], "推薦日": r['日期'], "漲跌(%)": round(((pn/p0)-1)*100, 2)})
        st.session_state.backtest_df = pd.DataFrame(bt_results)
    
    if st.session_state.backtest_df is not None:
        st.table(st.session_state.backtest_df.style.applymap(lambda x: 'color:red' if x > 0 else 'color:green', subset=['漲跌(%)']))

with tab3:
    st.subheader("📚 雲端監控庫內容")
    st.dataframe(db, use_container_width=True, hide_index=True)
with tab4:
    st.subheader("⚡ 飆股 DNA 高階偵測")

    # --- 🔍 數據健康檢查儀表板 ---
    with st.container(border=True):
        c_status, c_info, c_btn = st.columns([1, 2, 1])
        
        test_sid = "2330.TW"
        try:
            # 使用 download 抓取最近 3 天資料，這比 history(period="1d") 更穩定
            check_df = yf.download(test_sid, period="3d", progress=False, show_errors=False)
            
            if not check_df.empty:
                # 強制處理 MultiIndex 索引問題
                if isinstance(check_df.columns, pd.MultiIndex):
                    check_df.columns = check_df.columns.get_level_values(0)
                
                # 過濾掉無交易量的日子（如週末或連假）
                valid_df = check_df[check_df['Volume'] > 0]
                
                if not valid_df.empty:
                    last_row = valid_df.iloc[-1]
                    last_price = float(last_row['Close'])
                    last_date = valid_df.index[-1].strftime('%Y-%m-%d')
                    
                    c_status.metric("數據連線", "✅ 正常")
                    c_info.write(f"📅 **基準交易日**：`{last_date}`")
                    c_info.write(f"💰 **台積電收盤**：`{last_price:.1f}`")
                else:
                    c_status.metric("數據連線", "⚠️ 假日")
                    c_info.warning("目前抓取到的是非交易日數據。")
            else:
                c_status.metric("數據連線", "❌ 失敗")
                c_info.error("無法取得數據，請檢查 API 限制。")
        
        except Exception as e:
            print(e)
            c_status.metric("數據連線", "🚫 錯誤")
            c_info.info("連線異常，請嘗試重新測試。")

        # 重新測試按鈕
        if c_btn.button("🔄 重新測試連線", use_container_width=True):
            st.rerun()
    # ------------------------------

    # (原本的切換按鈕與參數設定...)
    mode = st.segmented_control("掃描範圍", ["全台股", "資料庫"], default="全台股")

    with st.expander("🛠️ 進階篩選參數 (手機建議預設)", expanded=False):
        use_bias = st.toggle("開啟季年線位階篩選", value=True)
        bias_range = st.slider("季年乖離區間 (%)", -30, 60, (-10, 25), disabled=not use_bias)
        g_limit = st.slider("均線糾結度 (%)", 1.0, 10.0, 4.0)
        v_limit = st.slider("量比門檻 (窒息量)", 0.1, 2.5, 0.8)
        min_v = st.number_input("最低成交量 (張)", value=300)

    if st.button("🏁 執行深度掃描", use_container_width=True, type="primary"):
        all_tickers = get_taiwan_stock_tickers()
        if mode == "全台股":
            search_list = all_tickers
        else:
            db_sids = [re.search(r'\d{4}', str(x)).group(0) for x in db['標的'] if re.search(r'\d{4}', str(x))]
            search_list = [t for t in all_tickers if any(sid in t for sid in db_sids)]
        
        if search_list:
            hits = []
            prog, status = st.progress(0), st.empty()
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(check_breakout_v55, s, g_limit, v_limit, min_v, bias_range, use_bias): s for s in search_list}
                for i, f in enumerate(concurrent.futures.as_completed(futures)):
                    res = f.result()
                    if res: hits.append(res)
                    if i % 15 == 0:
                        prog.progress((i+1)/len(search_list))
                        status.text(f"掃描中: {i+1}/{len(search_list)}...")
            
            st.session_state.v55_results = pd.DataFrame(hits)
            status.success(f"⚡ 完成！發現 {len(hits)} 檔標的。")

    if st.session_state.v55_results is not None and not st.session_state.v55_results.empty:
        st.divider()
        st.write("💡 **手機操作**：點擊下方表格內任一列即可查看詳細 K 線圖與 AI 分析。")
        event = st.dataframe(
            st.session_state.v55_results, 
            on_select="rerun", 
            selection_mode="single-row", 
            hide_index=True,
            use_container_width=True,
            column_config={
                "現價": st.column_config.NumberColumn(format="$%.2f"),
                "糾結(%)": st.column_config.NumberColumn(format="%.2f%%")
            }
        )
        
        if event.selection.rows:
            row_idx = event.selection.rows[0]
            target = st.session_state.v55_results.iloc[row_idx]
            show_diagnosis(target['代號'], target['名稱'])
