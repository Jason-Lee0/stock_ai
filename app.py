import streamlit as st
import pandas as pd
import yfinance as yf
import twstock
import re
import json
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import time
from streamlit_gsheets import GSheetsConnection
import google.generativeai as genai
from pypdf import PdfReader

# --- 1. 系統設定與初始化 ---
st.set_page_config(page_title="AI 飆股系統 v8.0", layout="wide", page_icon="🛡️")

# 初始化 Session State
for key in ['scan_results', 'raw_json', 'rep_date']:
    if key not in st.session_state: st.session_state[key] = None

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    model = genai.GenerativeModel('gemini-2.5-flash')
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"連線設定錯誤: {e}")
# --- 1. 資料轉換與同步邏輯 (關鍵：將長資料轉為寬資料存入 Sheets) ---

def sync_market_to_gsheets(conn, all_tickers):
    """
    抓取全台股 450 天數據，轉成寬表並存入 Google Sheets
    """
    st.warning("📡 正在從 Yahoo 下載全市場 450 天數據，這需要約 3-5 分鐘...")
    
    # 批次下載以避免超時 (每 100 檔一組)
    batch_size = 100
    all_close = pd.DataFrame()
    all_vol = pd.DataFrame()
    
    prog = st.progress(0)
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i : i + batch_size]
        data = yf.download(batch, period="450d", interval="1d", progress=False)
        
        if isinstance(data.columns, pd.MultiIndex):
            batch_close = data['Close']
            batch_vol = data['Volume']
        else:
            batch_close = data[['Close']].rename(columns={'Close': batch[0]})
            batch_vol = data[['Volume']].rename(columns={'Volume': batch[0]})
            
        all_close = pd.concat([all_close, batch_close], axis=1)
        all_vol = pd.concat([all_vol, batch_vol], axis=1)
        
        prog.progress(min((i + batch_size) / len(all_tickers), 1.0))

    # 整理索引，確保日期格式統一
    all_close.index = all_close.index.strftime('%Y-%m-%d')
    all_vol.index = all_vol.index.strftime('%Y-%m-%d')
    
    # 存入 Google Sheets (這會覆蓋舊數據)
    st.info("💾 正在將數據寫入 Google Sheets...")
    conn.update(worksheet="Market_Close", data=all_close.reset_index())
    conn.update(worksheet="Market_Vol", data=all_vol.reset_index())
    st.success("✅ 雲端行情同步完成！")
    st.cache_data.clear() # 清除讀取緩存

@st.cache_data(ttl=3600)
def load_cached_market_data(_conn):
    """
    從 Google Sheets 讀取寬表數據並還原格式
    """
    try:
        df_c = _conn.read(worksheet="Market_Close").set_index('Date')
        df_v = _conn.read(worksheet="Market_Vol").set_index('Date')
        return df_c, df_v
    except:
        return None, None

# --- 2. 策略計算邏輯 (純記憶體運算，速度極快) ---

def run_strategy_engine(df_c, df_v, mode, p):
    """
    df_c: 寬表格式收盤價 (pd.DataFrame)
    df_v: 寬表格式成交量 (pd.DataFrame)
    mode: 策略模式字串
    p: 參數字典 (含 gap, vol_ratio, min_v, short_gap 等)
    """
    hits = []
    symbols = df_c.columns
    total = len(symbols)
    
    # --- UI 監控元件初始化 ---
    prog_bar = st.progress(0)
    status_txt = st.empty()
    hit_count_txt = st.empty()
    
    # 取得最新一天的日期
    last_date = df_c.index[-1]
    
    for i, s in enumerate(symbols):
        # 每 20 檔更新一次 UI，展現輪動感並保持效能
        if i % 20 == 0:
            status_txt.text(f"🔍 正在掃描標的: {s} ({i+1}/{total})")
            prog_bar.progress((i+1)/total)
            hit_count_txt.caption(f"🎯 目前符合策略標的數: {len(hits)}")
            
        try:
            # 提取單檔數據並移除 NaN (加速運算)
            prices = df_c[s].dropna()
            volumes = df_v[s].dropna()
            
            # 基礎門檻：數據長度與今日張數
            if len(prices) < 240: continue
            
            close_p = float(prices.iloc[-1])
            vol_today = float(volumes.iloc[-1])
            shares = vol_today / 1000
            
            # 1. 通用門檻：最低張數限制
            if shares < p['min_v']: continue
            
            # 2. 預算均線 (向量化提取最後一筆)
            # 計算 5, 10, 20, 60, 120, 240 MA
            ma_5 = prices.rolling(5).mean().iloc[-1]
            ma_10 = prices.rolling(10).mean().iloc[-1]
            ma_20 = prices.rolling(20).mean().iloc[-1]
            ma_60 = prices.rolling(60).mean().iloc[-1]
            ma_120 = prices.rolling(120).mean().iloc[-1]
            ma_240 = prices.rolling(240).mean().iloc[-1]
            
            # 3. 策略分支判斷
            
            # --- 模式 A: 💎 量縮糾結 ---
            if mode == "💎 量縮糾結":
                # 條件 1: 量縮比 (今日量 / 20日均量)
                avg_v20 = volumes.tail(20).mean()
                v_ratio = vol_today / avg_v20
                if v_ratio > p['vol_ratio']: continue
                
                # 條件 2: 六線糾結度 (5,10,20,60,120,240)
                ma_list = [ma_5, ma_10, ma_20, ma_60, ma_120, ma_240]
                ma_gap = (max(ma_list) / min(ma_list) - 1) * 100
                if ma_gap > p['gap']: continue
                
                # 條件 3: 價格靠近 月/季/半年線 支撐 (3.5% 誤差)
                supports = [ma_20, ma_60, ma_120]
                is_near_support = any(abs(close_p / s - 1) < 0.035 for s in supports)
                if not is_near_support: continue
                
                hits.append({
                    "代號": s, "名稱": twstock.codes.get(s[:4]).name if twstock.codes.get(s[:4]) else "未知",
                    "現價": round(close_p, 2), "糾結%": round(ma_gap, 2), 
                    "量縮比": round(v_ratio, 2), "張數": int(shares)
                })

            # --- 模式 B: 🌀 量縮回測 ---
            elif mode == "🌀 量縮回測":
             
                # 取得各項均線數值 (今天與5日前)
                # ma_5, ma_10, ma_20, ma_60, ma_120, ma_240 已在前方計算
                
                # --- 條件 1: 短/中長期趨勢保護 ---
                ma_20_prev = prices.rolling(20).mean().iloc[-6]   # 5天前月線
                ma_60_prev = prices.rolling(60).mean().iloc[-6]   # 5天前季線
                
                # 修正：月線必須上揚 且 季線上揚 (雙重趨勢保護)
                if ma_20 < ma_20_prev or ma_60 < ma_60_prev: continue
                
                # --- 條件 2: 價格必須「站上月線」 ---
                # 確保短線動能恢復，排除月線下的弱勢整理
                if close_p < ma_20: continue
                
                # --- 條件 3: 長線乖離控制 ---
                # 60, 120, 240MA 距離需在 8% 內，避免追逐過度噴發的股票
                long_mas = [ma_60, ma_120, ma_240]
                long_gap = (max(long_mas) / min(long_mas) - 1) * 100
                if long_gap > 8.0: continue
                
                # --- 條件 4: 四線糾結度 (5, 10, 20, 60MA) ---
                # 股價站在月線上，且這四條線黏得很緊，代表變盤在即
                congest_mas = [ma_5, ma_10, ma_20, ma_60]
                congest_gap = (max(congest_mas) / min(congest_mas) - 1) * 100
                if congest_gap > p['short_gap']: continue
                
                # --- 條件 5: 極致量縮 ---
                avg_v20 = volumes.tail(20).mean()
                v_ratio = vol_today / avg_v20
                if v_ratio > p['vol_ratio']: continue
                
                # --- 條件 6: 支撐位階判定 ---
                # 價格需貼近 季線 或 半年線 (3.5% 誤差) 
                if abs(close_p/ma_60 - 1) < 0.035 or abs(close_p/ma_120 - 1) < 0.035:
                    rank = "強勢多頭" if ma_20 > ma_60 > ma_120 else "整理轉強"
                    hits.append({
                        "代號": s, 
                        "名稱": twstock.codes.get(s[:4]).name if twstock.codes.get(s[:4]) else "未知",
                        "現價": round(close_p, 2), 
                        "均線糾結%": round(congest_gap, 2), 
                        "量縮比": round(v_ratio, 2), 
                        "張數": int(shares), 
                        "位階趨勢": rank,
                        "提醒": "月線助漲中"
                    })
            elif mode == "🚀 帶量突破":
                # --- 1. 絕對張數與相對量比 (應用 p['min_v'] 與 p['breakout_vol']) ---
                avg_v20 = volumes.tail(20).mean()
                v_ratio = vol_today / avg_v20
                
                # 條件：今日成交量 > 設定張數 且 量比 > 設定倍數
                if shares < p['min_v'] : continue
                
                # 過濾極端異常爆量 (防範主力對倒，固定設定 10 倍)
                if v_ratio > 10.0: continue 
    
                # --- 2. 漲幅條件 (應用 p['min_up']) ---
                # 必須收盤價 > 月線(20MA) 且 漲幅 > 參數設定 (例如 3.5%)
                price_change = (close_p / df['Close'].iloc[-2] - 1) * 100
                if close_p < ma_10 or price_change < p['min_up']: continue
    
                # --- 3. 乖離率控制 (應用 p['max_bias']) ---
                # 避免追逐離年線(240MA)太遠的股票，防止接到最後一棒
                bias_240 = (close_p / ma_240 - 1) * 100
                if bias_240 > p['max_bias']: continue
    
                # --- 4. K線型態：上影線過濾 (固定 50% 邏輯) ---
                high_p = float(last['High'])
                open_p = float(last['Open'])
                upper_shadow = high_p - close_p
                body_length = close_p - open_p
                
                # 若上影線超過實體的一半，代表衝高回落壓力大
                if body_length > 0 and (upper_shadow / body_length) > 0.5: continue
                if close_p <= open_p: continue # 排除假紅棒
    
                # --- 5. 前置糾結過濾 (確保是整理後的首根) ---
                # 檢查 5 天前的 5/10/20MA 糾結度是否在設定參數內
                ma_5_p = df['Close'].rolling(5).mean().iloc[-6]
                ma_10_p = df['Close'].rolling(10).mean().iloc[-6]
                ma_20_p = df['Close'].rolling(20).mean().iloc[-6]
                prev_gap = (max([ma_5_p, ma_10_p, ma_20_p]) / min([ma_5_p, ma_10_p, ma_20_p]) - 1) * 100
                if prev_gap > p['short_gap'] : continue
    
                # --- 通過測試，輸出結果 ---
                hits.append({
                    "代號": s, 
                    "名稱": twstock.codes.get(s[:4]).name if twstock.codes.get(s[:4]) else "未知",
                    "現價": round(close_p, 2), 
                    "漲幅%": round(price_change, 2),
                    "量比": round(v_ratio, 2), 
                    "乖離%": round(bias_240, 1),
                    "張數": int(shares),
                    "狀態": "🚀 帶量突破"
                })
        except Exception:
            continue
            
    # 清除監控 UI
    status_txt.empty()
    prog_bar.empty()
    hit_count_txt.empty()
    
    return pd.DataFrame(hits)



# --- 3. K 線診斷與 AI 分析視窗 ---

@st.dialog("📈 專業診斷報告", width="large")
def show_diagnosis(ticker, name):
    st.write(f"### {name} ({ticker})")
    df = yf.download(ticker, period="300d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    # 繪製圖表 (六色均線 + 成交量)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="K線"), row=1, col=1)
    
    ma_conf = {'MA5':'yellow','MA10':'#00BFFF','MA20':'#DA70D6','MA60':'#32CD32','MA120':'red','MA240':'#FF8C00'}
    for m, color in ma_conf.items():
        ma_s = df['Close'].rolling(int(m[2:])).mean()
        fig.add_trace(go.Scatter(x=df.index, y=ma_s, name=m, line=dict(color=color, width=1.2)), row=1, col=1)
    
    vol_colors = ['#FF4136' if c >= o else '#3D9970' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name="成交量", marker_color=vol_colors), row=2, col=1)
    fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False, margin=dict(l=5, r=5, t=5, b=5))
    st.plotly_chart(fig, use_container_width=True)

     # 2. Gemini AI 分析區塊 (加入錯誤攔截)
    if st.button("🤖 使用 Gemini 分析漲幅原因", use_container_width=True):
        with st.spinner("AI 正在分析歷史數據與新聞..."):
            try:
                # 建立精簡的 Prompt 以節省 Token
                prompt = f"請簡潔分析台股 {name} ({ticker}) 過去一年的重大漲幅原因（如產業利多、題材、財報）。請用繁體中文回答。"
                
                # 呼叫 Gemini
                res = model.generate_content(prompt)
                
                if res.text:
                    st.info(res.text)
                else:
                    st.warning("AI 回傳內容為空，請稍後再試。")
                    
            except Exception as e:
                # 專門攔截 429 Resource Exhausted 錯誤
                if "429" in str(e) or "ResourceExhausted" in str(e):
                    st.error("⚠️ AI 服務目前配額已滿（免費版限制）。請等候 60 秒後再點擊一次，或是今天請稍作休息。")
                else:
                    st.error(f"發生非預期錯誤: {e}")

    # 提供一個手動查詢連結作為備案
    st.markdown(f"🔗 [查看 Yahoo 股市新聞 - {name}](https://tw.stock.yahoo.com/quote/{ticker[:4]}/news)")

# --- 4. 主介面分頁規劃 ---

# 讀取雲端庫
try: db = conn.read(worksheet="Sheet1").dropna(subset=['標的'])
except: db = pd.DataFrame(columns=['日期', '標的', '題材', '原因'])

tab1, tab2, tab3, tab4 = st.tabs(["📄 週報提取", "📈 週報回顧", "📚 雲端資料庫", "⚡ 策略偵測器"])

with tab1:
    st.subheader("📄 Gemini 週報分析")
    pdf = st.file_uploader("上傳 PDF 週報", type="pdf")
    if pdf and st.button("🚀 解析週報", use_container_width=True):
        text = "".join([p.extract_text() for p in PdfReader(pdf).pages])
        res = model.generate_content(f"請提取股票標的、題材與推薦原因，並以 JSON 格式回傳: [{{\"標的\":\"\", \"題材\":\"\", \"原因\":\"\"}}]\n\n{text[:8000]}").text
        st.session_state.raw_json = res.replace('```json', '').replace('```', '').strip()
    
    if st.session_state.raw_json:
        st.code(st.session_state.raw_json, language='json')
        if st.button("📥 儲存至資料庫", use_container_width=True):
            new_data = pd.DataFrame(json.loads(st.session_state.raw_json))
            new_data['日期'] = datetime.date.today().strftime("%Y-%m-%d")
            conn.update(worksheet="Sheet1", data=pd.concat([db, new_data], ignore_index=True))
            st.success("存檔成功！")

with tab2:
    st.subheader("📈 週報推薦績效回顧")
    if st.button("執行績效回測 (最近 10 筆)", use_container_width=True):
        results = []
        for _, row in db.tail(10).iterrows():
            sid = re.search(r'\d{4}', str(row['標的'])).group(0)
            h = yf.download(f"{sid}.TW", start=row['日期'], progress=False)
            if not h.empty:
                chg = ((h['Close'].iloc[-1] / h['Close'].iloc[0]) - 1) * 100
                results.append({"標的": row['標的'], "推薦日": row['日期'], "目前漲跌%": round(float(chg), 2)})
        st.table(results)

with tab3:
    st.subheader("📚 雲端庫存分析")
    st.dataframe(db, use_container_width=True, hide_index=True)

# --- 在 Tab 4 的 UI 部分進行微調 ---

with tab4:
    st.subheader("⚡ 參數調優偵測器 (極速版)")
    
    # 1. 雲端同步區 (每天收盤後點一次)
    if st.button("🔄 同步雲端行情 (450天全量數據)", use_container_width=True):
        all_tickers = [f"{c}.TW" if i.market=="上市" else f"{c}.TWO" for c, i in twstock.codes.items() if c.isdigit() and len(c)==4]
        sync_market_to_gsheets(conn, all_tickers)

    # 2. 數據載入 (從 Google Sheets 緩存到記憶體)
    df_c, df_v = load_cached_market_data(conn)
    
    if df_c is not None:
        # 3. 策略與參數設定區
        strategy_options = ["💎 量縮糾結", "🌀 量縮回測", "🚀 帶量突破"]
        mode = st.segmented_control("策略模式", strategy_options, default="🌀 量縮回測")
        
        with st.expander("🛠️ 參數設定", expanded=True):
            c_a, c_b, c_c, c_d, c_e = st.columns(5)
            p_min_v = c_a.number_input("最低張數", value=300)
            p_dict = {'min_v': p_min_v}
            
            if mode == "💎 量縮糾結":
                p_dict['gap'] = c_b.slider("糾結度門檻 %", 1.0, 8.0, 4.5)
                p_dict['vol_ratio'] = c_c.slider("量縮比門檻", 0.1, 1.0, 0.5)
            elif mode == "🌀 量縮回測":
                p_dict['short_gap'] = c_b.slider("短線糾結 % (5/10/20/60)", 1.0, 5.0, 3.0)
                p_dict['vol_ratio'] = c_c.slider("量縮比門檻", 0.1, 1.0, 0.5)
            elif mode == "🚀 帶量突破":
                p_dict['breakout_vol'] = c_b.slider("量比倍數 (vs 20MA)", 2.0, 8.0, 3.0)
                p_dict['min_up'] = c_c.slider("最低要求漲幅 (%)", 1.0, 7.0, 3.5)
                p_dict['max_bias'] = c_d.slider("噴發乖離限制 (%)", 10.0, 50.0, 25.0, help="股價/年線 比率上限")
                p_dict['short_gap'] = c_e.slider("短線糾結 % (5/10/20)", 1.0, 10.0, 3.0)

        # --- 關鍵：手動篩選按鈕 ---
        if st.button("🎯 執行策略篩選", type="primary", use_container_width=True):
            with st.spinner("正在從大數據矩陣過濾標的..."):
                # 從記憶體直接運算，1800 檔通常在 1~3 秒內完成
                results = run_strategy_engine(df_c, df_v, mode, p_dict)
                st.session_state.scan_results = results # 存入 session 確保點選表格時數據還在
        
        # 4. 顯示結果區
        if st.session_state.scan_results is not None:
            res_df = st.session_state.scan_results
            st.write(f"### 🎯 篩選結果 (共 {len(res_df)} 檔)")
            
            if not res_df.empty:
                # 根據不同策略自動選擇排序邏輯
                sort_col = "糾結%" if "糾結%" in res_df.columns else ("短糾%" if "短糾%" in res_df.columns else "現價")
                df_display = res_df.sort_values(sort_col).reset_index(drop=True)
                
                # 互動表格
                sel = st.dataframe(
                    df_display, 
                    on_select="rerun", 
                    selection_mode="single-row", 
                    use_container_width=True, 
                    hide_index=True
                )
                
                # 點擊顯示詳細 K 線與 AI 診斷
                if sel.selection.rows:
                    target = df_display.iloc[sel.selection.rows[0]]
                    show_diagnosis(target['代號'], target.get('名稱', '選定標的'))
            else:
                st.warning("查無符合條件標的，請放寬參數後再次執行篩選。")
                
    else:
        st.info("💡 尚未偵測到雲端數據。請先點擊上方按鈕執行「同步雲端行情」。")
