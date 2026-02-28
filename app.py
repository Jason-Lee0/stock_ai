import streamlit as st
import google.generativeai as genai
from pypdf import PdfReader
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import yfinance as yf
import re
import json
from datetime import datetime
import time

# --- 1. 初始化設定 ---
st.set_page_config(page_title="AI 飆股偵測與週報資料庫", layout="wide", page_icon="📈")

try:
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
    # 採用最新的 Gemini 模型
    model = genai.GenerativeModel('gemini-2.0-flash') 
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"初始化失敗，請檢查 Secrets 設定: {e}")
    st.stop()

# --- 2. 核心邏輯函式 ---

def extract_stock_ids(text):
    """從文字中提取 4 位數台股代碼"""
    return re.findall(r'\b\d{4}\b', text)

def get_stock_perf(sid):
    """獲取台股即時行情與漲跌幅"""
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        t = yf.Ticker(f"{sid}{suffix}")
        h = t.history(period="1mo")
        if h.empty: return None
        cur = h['Close'].iloc[-1]
        chg = ((cur - h['Close'].iloc[0]) / h['Close'].iloc[0]) * 100
        return {"price": cur, "change": chg}
    except: return None

def check_breakout_dna(sid):
    """
    飆股起漲 DNA 偵測：
    1. 均線糾結度 < 3.5% (5/10/20 MA)
    2. 成交量萎縮 < 0.75 (相較於 20 日均量)
    3. 股價在 60MA (季線) 之上 (多頭架構)
    """
    try:
        suffix = ".TW" if int(sid) < 9000 else ".TWO"
        t = yf.Ticker(f"{sid}{suffix}")
        df = t.history(period="65d") 
        if len(df) < 60: return None
        
        # 計算指標
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA10'] = df['Close'].rolling(10).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()
        
        last = df.iloc[-1]
        vol_avg
