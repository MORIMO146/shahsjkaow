import streamlit as st
import ccxt
import pandas as pd
import requests
import json
import re

st.set_page_config(page_title="MEXC AI Trader PRO", page_icon="🤖", layout="centered")
st.title("🤖 MEXC AI Trader Panel")

# Настройки берем из Secrets Streamlit
api_key = st.secrets.get("MEXC_API_KEY", "")
secret_key = st.secrets.get("MEXC_SECRET_KEY", "")
ai_token = st.secrets.get("ROUTERAI_API_KEY", "")

@st.cache_resource
def init_mexc(api_key, secret_key):
    config = {
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'urls': {
            'api': {
                'public': 'https://api.mexc.me/api/v3',
                'private': 'https://api.mexc.me/api/v3',
            }
        }
    }
    if api_key and secret_key:
        config['apiKey'] = api_key
        config['secret'] = secret_key
    return ccxt.mexc(config)

mexc_client = init_mexc(api_key, secret_key)

tab1, tab2, tab3 = st.tabs(["📊 Данные рынка", "🧠 Настройки ИИ", "📜 Логи"])

with tab1:
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Торговая пара", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "MX/USDT"], index=0)
    with col2:
        timeframe = st.selectbox("Таймфрейм", ["5m", "15m", "1h", "4h", "1d"], index=2)

    if st.button("📊 Собрать данные", use_container_width=True):
        with st.spinner("Загрузка..."):
            try:
                ohlcv = mexc_client.fetch_ohlcv(symbol, timeframe, limit=20)
                df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
                
                st.line_chart(data=df, x='Timestamp', y='Close')
                st.session_state['market_data'] = df.tail(10).to_json(orient="records")
                st.success("Данные получены!")
            except Exception as e:
                st.error(f"Ошибка MEXC: {e}")

    if 'market_data' in st.session_state:
        if st.button("🚀 Запустить анализ (DeepSeek V4 Pro)", type="primary", use_container_width=True):
            with st.spinner("Анализ через RouterAI..."):
                try:
                    # Внедряем вашу структуру запроса
                    url = "https://routerai.ru/api/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {ai_token}",
                        "Content-Type": "application/json"
                    }
                    data = {
                        "model": "deepseek/deepseek-v4-pro",
                        "messages": [
                            {"role": "system", "content": "Ты эксперт-трейдер. Проанализируй данные JSON и верни вердикт."},
                            {"role": "user", "content": f"Проанализируй рынок: {st.session_state['market_data']}"}
                        ]
                    }
                    
                    response = requests.post(url, headers=headers, json=data)
                    result = response.json()
                    
                    # Вывод результата
                    if 'choices' in result:
                        ai_content = result['choices'][0]['message']['content']
                        st.subheader("📋 Результат анализа:")
                        st.write(ai_content)
                    else:
                        st.error(f"Ошибка ответа: {result}")
                        
                except Exception as e:
                    st.error(f"Ошибка выполнения запроса: {e}")

with tab2:
    st.write("### Настройки")
    st.info("Используется модель: deepseek/deepseek-v4-pro")

with tab3:
    st.write("### Логи")
    st.code("Система инициализирована...")