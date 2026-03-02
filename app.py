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

# --- 1. 系統初始化 (iOS 響應式佈局) ---
st.set_page_config(page_title="AI 飆股診斷 v5.9", layout="wide", page_icon="🛡️")

# 初始化所有記憶體狀態
for key in ['v59_results', 'raw_json', 'rep_date', 'backtest_df']:
    if key not in st.session_state:
        st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"系統初始化失敗 (請檢查 Secrets): {e}")
    st.stop()

# --- 2. 核心運算引擎 (防呆回溯與穩定抓取) ---

def get_taiwan_stock_tickers():
    """獲取台股清單，過濾非個股"""
    all_codes = twstock.codes
    taiwan_tickers = []
    for code, info in all_codes.items():
        if not code.isdigit() or len(code) != 4: continue
        if any(x in info.type for x in ["權證", "ETF", "受益證券"]): continue
        suffix = ".TW" if info.market == "上市" else ".TWO"
        taiwan_tickers.append(f"{code}{suffix}")
    return sorted(list(set(taiwan_tickers)))

def check_breakout_v59(ticker, g_limit, v_limit, min_v, bias_range, use_bias):
    """偵測引擎：5/10/20MA 糾結 + 季年位階 + 數據清洗"""
    try:
        stock_obj = yf.Ticker(ticker)
        # 抓取較長區間以確保 MA240 計算穩定
        df = stock_obj.history(period="450d")
        
        if df.empty or len(df) < 245: return None
        
        # 修正 yfinance MultiIndex 問題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # 【防呆關鍵】：過濾掉週末或空值，確保最後一筆是最新收盤
        df = df[df['Volume'] > 0].dropna()
        if len(df) < 240: return None
        
        last = df.iloc[-1]
        
        # 1. 基礎成交量過濾 (張數)
        if (last['Volume'] / 1000) < min_v: return None
        
        # 2. 均線計算 (基於有效交易日)
        close = df['Close']
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        ma240 = close.rolling(240).mean().iloc[-1]
        
        # 3. 季年位階篩選
        ma_bias = round(((ma60 / ma240) - 1) * 100, 2)
        if use_bias and not (bias_range[0] <= ma_bias <= bias_range[1]): return None
        
        # 4. 短線糾結度 (5, 10, 20MA)
        ma_list = [float(ma5), float(ma10), float(ma20)]
        gap = round((max(ma_list) / min(ma_list) - 1) * 100, 2)
        
        # 5. 量比 (窒息量判斷)
        vol_avg20 = df['Volume'].rolling(20).mean().iloc[-1]
        v_ratio = round(last['Volume'] / vol_avg20, 2)
        
        if gap <= g_limit and v_ratio <= v_limit:
            pure_sid = re.search(r'\d{4}', ticker).group(0)
            info = twstock.codes.get(pure_sid)
            return {
                "代號": ticker, "名稱": info.name if info else "未知",
                "類股": info.category if info else "其他", "現價": round(float(last['Close']), 2),
                "糾結(%)": gap, "位階(%)": ma_bias, "量比": v_ratio,
                "屬性": "📈 多頭" if ma_bias > 0 else "🩹 底部",
                "基準日": df.index[-1].strftime('%m/%d')
            }
    except: return None

# --- 3. 診斷與考古介面 ---

def get_historical_theme_ai(ticker, name):
    """AI 考古標的爆發原因"""
    try:
        df = yf.download(ticker, period="6mo", progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df['Pct'] = df['Close'].pct_change()
        max_day = df['Pct'].idxmax()
        prompt = f"分析台股 {name}({ticker})。該股在 {max_day.strftime('%Y-%m-%d')} 大幅上漲。請簡述原因(40字內)。"
        return f"📅 {max_day.strftime('%Y-%m-%d')} 考古：{model.generate_content(prompt).text}"
    except: return "AI 考古分析暫時無法載入。"

@st.dialog("🚀 AI 飆股診斷室", width="large")
def show_diagnosis(ticker, name):
    st.write(f"### {name} ({ticker})")
    with st.spinner("AI 正在翻閱歷史檔案..."):
        st.info(get_historical_theme_ai(ticker, name))
    
    # 繪製專業 K 線
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    for p in [5, 20, 60, 240]: df[f'MA{p}'] = df['Close'].rolling(p).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    for ma, col in zip(['MA5','MA20','MA60','MA240'], ['white','yellow','orange','purple']):
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], name=ma, line=dict(color=col, width=1.5)), row=1, col=1)
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=10, b=5))
    st.plotly_chart(fig, width='stretch')
    if st.button("關閉診斷", width='stretch'): st.rerun()

# --- 4. 介面與分頁配置 ---

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報", "📈 績效", "📚 庫", "⚡ 偵測器"])

try:
    db = conn.read(worksheet="Sheet1").dropna(subset=['標的'])
except:
    db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

with tab4:
    st.subheader("⚡ 數據連線檢查")
    with st.container(border=True):
        c_status, c_info, c_btn = st.columns([1, 2, 1])
        
        # --- 強化抓取函數 ---
       # --- 強化修復版抓取函數 ---
        def robust_check():
            try:
                time.sleep(0.5) 
                test_ticker = "2330.TW"
                
                # 1. 只保留最核心參數，移除可能導致意外關鍵字錯誤的參數
                raw_data = yf.download(
                    test_ticker, 
                    period="5d", 
                    interval="1d", 
                    progress=False
                )
                
                # 2. 如果 download 還是失敗，嘗試最原始的 Ticker 抓取
                if raw_data is None or raw_data.empty:
                    st.write("嘗試備援連線...")
                    stock = yf.Ticker(test_ticker)
                    raw_data = stock.history(period="5d")

                if not raw_data.empty:
                    # 3. 徹底處理 MultiIndex (2026 必備)
                    if isinstance(raw_data.columns, pd.MultiIndex):
                        raw_data.columns = [col[0] if isinstance(col, tuple) else col for col in raw_data.columns]
                    
                    # 4. 清洗 NaN 資料
                    valid_data = raw_data.dropna(subset=['Close'])
                    valid_data = valid_data[valid_data['Volume'] > 0]
                    
                    if not valid_data.empty:
                        last_row = valid_data.iloc[-1]
                        return {
                            "status": "✅ 正常",
                            "date": valid_data.index[-1].strftime('%Y-%m-%d'),
                            "price": float(last_row['Close'])
                        }
                return {"status": "❌ 失敗", "date": "連線被拒絕", "price": 0}
            except Exception as e:
                # 顯示實際錯誤原因
                return {"status": "🚫 錯誤", "date": f"指令錯誤: {str(e)}", "price": 0}

        # 執行檢查
        res = robust_check()
        
        c_status.metric("數據連線", res["status"])
        if res["status"] == "✅ 正常":
            c_info.write(f"📅 **基準交易日**：`{res['date']}`")
            c_info.write(f"💰 **台積電基準**：`{res['price']}`")
        else:
            c_info.error(f"連線異常：{res['date']}。請嘗試重新測試或檢查網路。")
        
        if c_btn.button("🔄 重新測試連線", width='stretch'):
            # 清除快取並重新載入
            st.cache_data.clear()
            st.rerun()
    st.write("---")
    mode = st.segmented_control("掃描範圍", ["全台股", "資料庫"], default="全台股")
    
    with st.expander("🛠️ 調整參數 (手機建議預設)", expanded=False):
        use_bias = st.toggle("位階過濾", value=True)
        bias_range = st.slider("位階 (%)", -30, 60, (-10, 25), disabled=not use_bias)
        g_limit = st.slider("糾結度 (%)", 1.0, 10.0, 4.0)
        v_limit = st.slider("量比 (窒息量)", 0.1, 2.5, 0.8)
        min_v = st.number_input("最低成交量 (張)", value=300)

    if st.button("🏁 執行深度掃描", width='stretch', type="primary"):
        all_tickers = get_taiwan_stock_tickers()
        if mode == "全台股": search_list = all_tickers
        else:
            db_sids = [re.search(r'\d{4}', str(x)).group(0) for x in db['標的'] if re.search(r'\d{4}', str(x))]
            search_list = [t for t in all_tickers if any(sid in t for sid in db_sids)]
        
        if search_list:
            hits = []
            prog, status = st.progress(0), st.empty()
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(check_breakout_v59, s, g_limit, v_limit, min_v, bias_range, use_bias): s for s in search_list}
                for i, f in enumerate(concurrent.futures.as_completed(futures)):
                    res = f.result()
                    if res: hits.append(res)
                    if i % 15 == 0:
                        prog.progress((i+1)/len(search_list))
                        status.text(f"掃描中: {i+1}/{len(search_list)}")
            st.session_state.v59_results = pd.DataFrame(hits)
            status.success(f"⚡ 完成！發現 {len(hits)} 檔標的。")

    if st.session_state.v59_results is not None and not st.session_state.v59_results.empty:
        st.write("💡 點擊下方表格查看 K 線分析")
        event = st.dataframe(st.session_state.v59_results, on_select="rerun", selection_mode="single-row", hide_index=True, width='stretch')
        if event.selection.rows:
            target = st.session_state.v59_results.iloc[event.selection.rows[0]]
            show_diagnosis(target['代號'], target['名稱'])

# --- 其餘功能整合 (週報、回測) ---
with tab1:
    pdf = st.file_uploader("上傳 PDF", type="pdf")
    if pdf and st.button("🚀 解析週報", width='stretch'):
        reader = PdfReader(pdf)
        text = "".join([p.extract_text() for p in reader.pages])
        res = model.generate_content(f"轉 JSON (題材, 原因, 標的): {text[:8000]}").text
        st.session_state.raw_json = res
        st.session_state.rep_date = datetime.date.today().strftime("%Y-%m-%d")
    if st.session_state.raw_json:
        st.code(st.session_state.raw_json)
        if st.button("📥 存入雲端", width='stretch'):
            clean = st.session_state.raw_json.replace('```json', '').replace('```', '').strip()
            new_data = pd.DataFrame(json.loads(clean))
            new_data['日期'] = st.session_state.rep_date
            conn.update(worksheet="Sheet1", data=pd.concat([db, new_data], ignore_index=True))
            st.success("存檔成功")

with tab2:
    if st.button("🏁 開始回測", width='stretch'):
        bt = []
        for _, r in db.tail(10).iterrows():
            m = re.search(r'\d{4}', str(r['標的']))
            if m:
                s = f"{m.group(0)}.TW" if int(m.group(0)) < 9000 else f"{m.group(0)}.TWO"
                h = yf.download(s, start=r['日期'], progress=False)
                if not h.empty:
                    p0, pn = h['Close'].iloc[0], h['Close'].iloc[-1]
                    bt.append({"標的": r['標的'], "推薦日": r['日期'], "漲跌%": round(((pn/p0)-1)*100, 2)})
        st.session_state.backtest_df = pd.DataFrame(bt)
    if st.session_state.backtest_df is not None:
        st.dataframe(st.session_state.backtest_df, width='stretch', hide_index=True)

with tab3:
    st.dataframe(db, width='stretch', hide_index=True)
