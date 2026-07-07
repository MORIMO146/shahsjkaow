import streamlit as st
import ccxt
import pandas as pd
import requests
import json
import re

st.set_page_config(page_title="MEXC AI Trader PRO (RouterAI)", page_icon="🤖", layout="centered")
st.title("🤖 MEXC AI Trader Panel")

# Боковая панель
st.sidebar.header("🔑 Настройки API")
api_key = st.sidebar.text_input("MEXC API Key", type="password")
secret_key = st.sidebar.text_input("MEXC Secret Key", type="password")
ai_token = st.sidebar.text_input("RouterAI API Key", type="password", help="Ваш ключ из личного кабинета routerai.ru")

@st.cache_resource
def init_mexc(api_key, secret_key):
    config = {
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'hostname': 'api.mexc.me' # <-- Правильная подмена адреса для CCXT
    }
    if api_key and secret_key:
        config['apiKey'] = api_key
        config['secret'] = secret_key
        
    client = ccxt.mexc(config)
    
    # Принудительная перезапись ссылок на зеркало для любых версий библиотеки
    if 'spot' in client.urls.get('api', {}):
        client.urls['api']['spot']['public'] = 'https://api.mexc.me'
        client.urls['api']['spot']['private'] = 'https://api.mexc.me'
    else:
        client.urls['api']['public'] = 'https://api.mexc.me/api/v3'
        client.urls['api']['private'] = 'https://api.mexc.me/api/v3'
        
    return client

mexc_client = init_mexc(api_key, secret_key)

tab1, tab2, tab3 = st.tabs(["📊 Данные рынка", "🧠 Настройки ИИ", "📜 Логи"])

with tab1:
    st.metric(label="Статус системы", value="ПОДКЛЮЧЕНО (API КЛЮЧИ)" if (api_key and secret_key) else "ДЕМО-РЕЖИМ")
    
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Торговая пара", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "MX/USDT"], index=0)
    with col2:
        timeframe = st.selectbox("Таймфрейм (Свеча)", ["5m", "15m", "1h", "4h", "1d"], index=2)

    st.write("---")
    
    if st.button("📊 Собрать данные и построить график", use_container_width=True):
        with st.spinner("Связываемся с зеркалом MEXC..."):
            try:
                ohlcv = mexc_client.fetch_ohlcv(symbol, timeframe, limit=15)
                df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
                
                current_price = df['Close'].iloc[-1]
                st.metric(label=f"Текущая цена {symbol}", value=f"${current_price:,.4f}")
                st.line_chart(data=df, x='Timestamp', y='Close')
                
                st.session_state['market_data'] = df.tail(10).to_json(orient="records")
                st.session_state['current_price'] = current_price
                st.success("Данные рынка успешно загружены!")
            except Exception as e:
                st.error(f"Ошибка при работе с биржей MEXC: {e}")

    if 'market_data' in st.session_state:
        st.write("### 🧠 ИИ-Анализ графика")
        if st.button("🚀 Отправить этот график на анализ в DeepSeek Pro", type="primary", use_container_width=True):
            if not ai_token:
                st.warning("Пожалуйста, введите ваш RouterAI API Key в боковом меню!")
            else:
                with st.spinner("DeepSeek V4 Pro анализирует рынок через RouterAI..."):
                    try:
                        ai_model_mapped = st.session_state.get('ai_model_name', "deepseek/deepseek-v4-pro")
                        
                        system_prompt = (
                            "Ты — ведущий квантовый трейдер. Проанализируй массив свечей и вынеси торговое решение. "
                            "Ответь СТРОГО в формате JSON без какого-либо другого текста вокруг.\n"
                            "Формат ответа:\n"
                            '{"action": "BUY" или "SELL" или "HOLD", "reason": "объяснение на русском", "confidence": 0.90}'
                        )
                        
                        user_content = f"Данные последних свечей {symbol} ({timeframe}):\n{st.session_state['market_data']}\nТекущая цена: {st.session_state['current_price']}"

                        response = requests.post(
                            url="https://routerai.ru/api/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {ai_token}",
                                "Content-Type": "application/json"
                            },
                            data=json.dumps({
                                "model": ai_model_mapped,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_content}
                                ]
                            })
                        )
                        
                        result = response.json()
                        
                        if 'error' in result:
                            err_msg = result['error'].get('message', str(result['error'])) if isinstance(result['error'], dict) else str(result['error'])
                            st.error(f"Ошибка RouterAI: {err_msg}")
                        elif 'choices' not in result:
                            st.error(f"Неожиданный формат ответа от RouterAI. Проверьте баланс. Ответ: {result}")
                        else:
                            ai_reply = result['choices'][0]['message']['content']
                            
                            json_match = re.search(r'\{.*\}', ai_reply, re.DOTALL)
                            cleaned_reply = json_match.group(0) if json_match else ai_reply
                                
                            ai_json = json.loads(cleaned_reply.strip())
                            
                            st.subheader("📋 Вердикт DeepSeek V4 Pro:")
                            action = ai_json.get('action', 'HOLD')
                            reason = ai_json.get('reason', 'Анализ завершен')
                            try:
                                confidence_val = float(ai_json.get('confidence', 0.5)) * 100
                            except:
                                confidence_val = 50.0
                            
                            if action == "BUY":
                                st.success(f"🟢 СИГНАЛ НА ПОКУПКУ (Уверенность: {confidence_val:.1f}%)")
                            elif action == "SELL":
                                st.error(f"🔴 СИГНАЛ НА ПРОДАЖУ (Уверенность: {confidence_val:.1f}%)")
                            else:
                                st.info(f"⚪️ ПОЗИЦИЯ: ВНЕ РЫНКА (HOLD)")
                                
                            st.write(f"**Логика робота:** {reason}")
                            st.session_state['last_log'] = f"[AI-SIGNAL] {symbol} -> {action} ({reason})"
                        
                    except Exception as e:
                        st.error(f"Ошибка разбора ответа ИИ: {e}")
                        if 'ai_reply' in locals():
                            st.text(f"Сырой ответ модели:\n{ai_reply}")

with tab2:
    st.write("### Настройка «Мозга» трейдера")
    ai_choice = st.selectbox(
        "Выбранная модель", 
        ["DeepSeek V4 Pro (RouterAI)", "Запасной канал DeepSeek"]
    )
    
    model_map = {
        "DeepSeek V4 Pro (RouterAI)": "deepseek/deepseek-v4-pro",
        "Запасной канал DeepSeek": "deepseek/deepseek-chat"
    }
    st.session_state['ai_model_name'] = model_map[ai_choice]
    st.slider("Риск на одну сделку (% от баланса)", 0.5, 5.0, 1.0, 0.5)

with tab3:
    st.write("### Логи работы:")
    if 'last_log' in st.session_state:
        st.code(st.session_state['last_log'])
    else:
        st.code("[INFO] Система ожидает генерации первого сигнала...")