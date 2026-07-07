import streamlit as st
import ccxt
import pandas as pd
import requests
import json
import re

st.set_page_config(page_title="MEXC AI PRO", layout="wide")
st.title("🤖 MEXC AI Trader PRO")

# Ключи берем из Secrets Streamlit
api_key = st.secrets.get("MEXC_API_KEY", "")
secret_key = st.secrets.get("MEXC_SECRET_KEY", "")
ai_token = st.secrets.get("ROUTERAI_API_KEY", "")

# Инициализация MEXC
@st.cache_resource
def get_mexc():
    return ccxt.mexc({
        'apiKey': api_key, 'secret': secret_key,
        'enableRateLimit': True,
        'urls': {'api': {'public': 'https://api.mexc.me/api/v3', 'private': 'https://api.mexc.me/api/v3'}}
    })

mexc = get_mexc()

# Интерфейс
col1, col2 = st.columns([1, 3])
with col1:
    symbol = st.selectbox("Пара", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    if st.button("Обновить данные"):
        try:
            ohlcv = mexc.fetch_ohlcv(symbol, '1h', limit=20)
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
            st.session_state['df'] = df
            st.session_state['data_json'] = df.to_json(orient='records')
        except Exception as e:
            st.error(f"Ошибка MEXC: {e}")

with col2:
    if 'df' in st.session_state:
        st.line_chart(st.session_state['df']['close'])
        st.dataframe(st.session_state['df'].tail(5))
        
        # Анализ
        if st.button("🚀 Анализ DeepSeek V4 Pro"):
            with st.spinner("Запрос к RouterAI..."):
                try:
                    res = requests.post(
                        "https://routerai.ru/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {ai_token}", "Content-Type": "application/json"},
                        json={
                            "model": "deepseek/deepseek-v4-pro",
                            "messages": [{"role": "user", "content": f"Анализ {symbol}. Данные: {st.session_state['data_json']}"}]
                        }
                    )
                    ans = res.json()
                    if 'choices' in ans:
                        st.success(ans['choices'][0]['message']['content'])
                    else:
                        st.error(f"Ошибка RouterAI: {ans}")
                except Exception as e:
                    st.error(f"Ошибка сети: {e}")