import streamlit as st
import ccxt
import pandas as pd
import requests
import json
import re

# Настройка страницы
st.set_page_config(page_title="MEXC AI Pro", layout="centered")
st.title("🤖 MEXC AI Trader")

# Загрузка ключей из Secrets (Streamlit Cloud)
# В настройках Streamlit: [Secrets] -> MEXC_API_KEY, MEXC_SECRET_KEY, ROUTERAI_API_KEY
api_key = st.secrets.get("MEXC_API_KEY")
secret_key = st.secrets.get("MEXC_SECRET_KEY")
ai_token = st.secrets.get("ROUTERAI_API_KEY")

@st.cache_resource
def get_mexc():
    # Используем фиксированный адрес зеркала и отключаем проверку сертификатов, если нужно
    return ccxt.mexc({
        'apiKey': api_key,
        'secret': secret_key,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'urls': {'api': {'public': 'https://api.mexc.me/api/v3', 'private': 'https://api.mexc.me/api/v3'}}
    })

mexc_client = get_mexc()

# Вкладка с графиком
symbol = st.selectbox("Пара", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
if st.button("Загрузить котировки"):
    try:
        ohlcv = mexc_client.fetch_ohlcv(symbol, '1h', limit=20)
        df = pd.DataFrame(ohlcv, columns=['T', 'O', 'H', 'L', 'C', 'V'])
        st.session_state['data'] = df.to_json()
        st.line_chart(df['C'])
    except Exception as e:
        st.error(f"Ошибка биржи: {e}")

# ИИ анализ
if 'data' in st.session_state and st.button("Анализ DeepSeek"):
    try:
        response = requests.post(
            url="https://routerai.ru/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {ai_token}", "Content-Type": "application/json"},
            json={
                "model": "deepseek/deepseek-v4-pro",
                "messages": [{"role": "user", "content": f"Анализ пары: {symbol}. Данные: {st.session_state['data']}"}]
            },
            timeout=30
        )
        data = response.json()
        st.write("Ответ ИИ:", data['choices'][0]['message']['content'])
    except Exception as e:
        st.error(f"Ошибка ИИ: {e}")