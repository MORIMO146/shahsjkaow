import streamlit as st
import ccxt
import pandas as pd
import requests
import json
import re
from datetime import datetime

st.set_page_config(page_title="MEXC AI Trader PRO (RouterAI)", page_icon="🤖", layout="centered")
st.title("🤖 MEXC AI Trader Panel")

# Боковая панель
st.sidebar.header("🔑 Настройки API")
api_key = st.sidebar.text_input("MEXC API Key", type="password")
secret_key = st.sidebar.text_input("MEXC Secret Key", type="password")
ai_token = st.sidebar.text_input("RouterAI API Key", type="password", help="Ваш ключ из личного кабинета routerai.ru")

# Инициализация сессионных переменных
if 'signals_history' not in st.session_state:
    st.session_state['signals_history'] = []
if 'market_data' not in st.session_state:
    st.session_state['market_data'] = None

@st.cache_resource
def init_mexc(api_key, secret_key):
    config = {
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'},
        'hostname': 'api.mexc.me'
    }
    if api_key and secret_key:
        config['apiKey'] = api_key
        config['secret'] = secret_key
        
    client = ccxt.mexc(config)
    
    if 'spot' in client.urls.get('api', {}):
        client.urls['api']['spot']['public'] = 'https://api.mexc.me'
        client.urls['api']['spot']['private'] = 'https://api.mexc.me'
    else:
        client.urls['api']['public'] = 'https://api.mexc.me/api/v3'
        client.urls['api']['private'] = 'https://api.mexc.me/api/v3'
        
    return client

def analyze_with_ai(market_data, symbol, timeframe, current_price, ai_token, model_name):
    """Отправка данных на анализ в RouterAI"""
    
    system_prompt = (
        "Ты — профессиональный трейдер-аналитик. Проанализируй предоставленные свечные данные "
        "и выдай ТОЛЬКО JSON без лишнего текста.\n\n"
        "Формат ответа СТРОГО:\n"
        '{"action": "BUY"|"SELL"|"HOLD", "reason": "объяснение на русском", '
        '"confidence": 0.XX, "stop_loss": цена_в_числах, "take_profit": цена_в_числах}'
    )
    
    user_content = (
        f"Символ: {symbol}\n"
        f"Таймфрейм: {timeframe}\n"
        f"Текущая цена: {current_price}\n"
        f"Данные свечей (последние 10):\n{market_data}"
    )
    
    try:
        response = requests.post(
            url="https://routerai.ru/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {ai_token}",
                "Content-Type": "application/json"
            },
            json={
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.3,
                "max_tokens": 500
            },
            timeout=30
        )
        
        if response.status_code != 200:
            return None, f"HTTP Error {response.status_code}: {response.text}"
        
        result = response.json()
        
        if 'error' in result:
            err_msg = result['error'].get('message', str(result['error']))
            return None, f"API Error: {err_msg}"
        
        if 'choices' not in result or not result['choices']:
            return None, "Нет ответа от модели"
        
        ai_reply = result['choices'][0]['message']['content'].strip()
        
        # Очистка ответа от маркдауна
        if ai_reply.startswith("```json"):
            ai_reply = ai_reply[7:]
        elif ai_reply.startswith("```"):
            ai_reply = ai_reply[3:]
        if ai_reply.endswith("```"):
            ai_reply = ai_reply[:-3]
        
        # Поиск JSON в ответе
        json_match = re.search(r'\{[^{}]*\}', ai_reply, re.DOTALL)
        if json_match:
            ai_reply = json_match.group(0)
        
        ai_json = json.loads(ai_reply.strip())
        return ai_json, None
        
    except requests.exceptions.Timeout:
        return None, "Таймаут запроса к RouterAI"
    except json.JSONDecodeError as e:
        return None, f"Ошибка парсинга JSON: {str(e)}\nСырой ответ: {ai_reply if 'ai_reply' in locals() else 'Нет данных'}"
    except Exception as e:
        return None, f"Неизвестная ошибка: {str(e)}"

def execute_trade_signal(signal, symbol, mexc_client):
    """Эмуляция исполнения торгового сигнала"""
    action = signal.get('action', 'HOLD')
    if action == 'HOLD':
        return "Сигнал HOLD - сделка не открывается"
    
    # Здесь можно добавить реальное исполнение через mexc_client.create_order()
    trade_msg = f"📊 [{action}] {symbol} | Уверенность: {signal.get('confidence', 0)*100:.1f}%"
    if 'stop_loss' in signal:
        trade_msg += f" | SL: ${signal['stop_loss']:.2f}"
    if 'take_profit' in signal:
        trade_msg += f" | TP: ${signal['take_profit']:.2f}"
    
    return trade_msg

# Инициализация клиента MEXC
mexc_client = init_mexc(api_key, secret_key)

# Вкладки интерфейса
tab1, tab2, tab3, tab4 = st.tabs(["📊 Данные рынка", "🧠 Настройки ИИ", "📈 Сигналы", "📜 Логи"])

with tab1:
    st.metric(
        label="Статус системы", 
        value="ПОДКЛЮЧЕНО (API КЛЮЧИ)" if (api_key and secret_key) else "ДЕМО-РЕЖИМ"
    )
    
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.selectbox("Торговая пара", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "MX/USDT"], index=0)
    with col2:
        timeframe = st.selectbox("Таймфрейм", ["5m", "15m", "1h", "4h", "1d"], index=2)

    st.write("---")
    
    if st.button("📊 Собрать данные рынка", use_container_width=True):
        with st.spinner("Загрузка данных с MEXC..."):
            try:
                ohlcv = mexc_client.fetch_ohlcv(symbol, timeframe, limit=15)
                df = pd.DataFrame(ohlcv, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='ms')
                
                current_price = df['Close'].iloc[-1]
                
                # Сохраняем в сессию
                st.session_state['market_data'] = df.tail(10).to_json(orient="records")
                st.session_state['current_price'] = current_price
                st.session_state['last_symbol'] = symbol
                st.session_state['last_timeframe'] = timeframe
                
                # Отображение
                col1, col2, col3 = st.columns(3)
                col1.metric("Текущая цена", f"${current_price:,.4f}")
                col2.metric("Макс. за период", f"${df['High'].max():,.4f}")
                col3.metric("Мин. за период", f"${df['Low'].min():,.4f}")
                
                st.line_chart(data=df, x='Timestamp', y='Close')
                st.success("✅ Данные успешно загружены!")
                
            except Exception as e:
                st.error(f"❌ Ошибка MEXC: {e}")

with tab2:
    st.write("### 🧠 Настройка ИИ-модели")
    
    ai_choice = st.selectbox(
        "Выбранная модель", 
        ["DeepSeek V4 Pro", "DeepSeek Chat (запасной)", "DeepSeek Coder"],
        help="Выберите модель для анализа рынка"
    )
    
    model_map = {
        "DeepSeek V4 Pro": "deepseek/deepseek-v4-pro",
        "DeepSeek Chat (запасной)": "deepseek/deepseek-chat",
        "DeepSeek Coder": "deepseek/deepseek-coder"
    }
    
    st.session_state['ai_model_name'] = model_map[ai_choice]
    
    col1, col2 = st.columns(2)
    with col1:
        risk_percent = st.slider("Риск на сделку (% от баланса)", 0.5, 5.0, 1.0, 0.5)
        st.session_state['risk_percent'] = risk_percent
    with col2:
        confidence_threshold = st.slider("Минимальная уверенность ИИ", 50, 95, 70, 5)
        st.session_state['confidence_threshold'] = confidence_threshold / 100
    
    st.info(f"🔧 Текущая модель: **{ai_choice}** | Риск: **{risk_percent}%**")

with tab3:
    st.write("### 📈 Торговые сигналы")
    
    if 'market_data' not in st.session_state or st.session_state['market_data'] is None:
        st.warning("⚠️ Сначала загрузите данные рынка во вкладке 'Данные рынка'")
    else:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🧠 Получить AI-сигнал", type="primary", use_container_width=True):
                if not ai_token:
                    st.error("❌ Введите RouterAI API Key в боковом меню!")
                else:
                    with st.spinner("🤖 DeepSeek анализирует рынок..."):
                        symbol = st.session_state.get('last_symbol', symbol)
                        timeframe = st.session_state.get('last_timeframe', timeframe)
                        
                        ai_json, error = analyze_with_ai(
                            st.session_state['market_data'],
                            symbol,
                            timeframe,
                            st.session_state['current_price'],
                            ai_token,
                            st.session_state['ai_model_name']
                        )
                        
                        if error:
                            st.error(f"❌ Ошибка анализа: {error}")
                        else:
                            action = ai_json.get('action', 'HOLD')
                            confidence = float(ai_json.get('confidence', 0))
                            reason = ai_json.get('reason', 'Нет объяснения')
                            
                            # Проверка порога уверенности
                            threshold = st.session_state.get('confidence_threshold', 0.7)
                            
                            if confidence < threshold:
                                st.warning(f"⚠️ Уверенность ИИ ({confidence*100:.1f}%) ниже порога ({threshold*100:.0f}%). Сигнал HOLD.")
                                action = "HOLD"
                            
                            # Отображение сигнала
                            st.subheader("📋 Вердикт ИИ:")
                            
                            if action == "BUY":
                                st.success(f"🟢 **СИГНАЛ НА ПОКУПКУ** | Уверенность: {confidence*100:.1f}%")
                            elif action == "SELL":
                                st.error(f"🔴 **СИГНАЛ НА ПРОДАЖУ** | Уверенность: {confidence*100:.1f}%")
                            else:
                                st.info(f"⚪ **ВНЕ РЫНКА (HOLD)** | Уверенность: {confidence*100:.1f}%")
                            
                            st.write(f"**Анализ:** {reason}")
                            
                            # Дополнительные уровни
                            if 'stop_loss' in ai_json:
                                st.write(f"🛑 Stop Loss: ${ai_json['stop_loss']:.2f}")
                            if 'take_profit' in ai_json:
                                st.write(f"🎯 Take Profit: ${ai_json['take_profit']:.2f}")
                            
                            # Сохраняем сигнал в историю
                            signal_record = {
                                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                'symbol': symbol,
                                'action': action,
                                'confidence': confidence,
                                'reason': reason,
                                'price': st.session_state['current_price']
                            }
                            st.session_state['signals_history'].append(signal_record)
                            
                            # Показываем исполнение
                            trade_result = execute_trade_signal(ai_json, symbol, mexc_client)
                            st.write(f"**Исполнение:** {trade_result}")
        
        with col2:
            if st.button("🔄 Авто-трейдинг (каждые 60с)", use_container_width=True):
                st.info("Режим авто-трейдинга активирован (демо)")
                # Здесь можно добавить цикл с time.sleep(60)
    
    # История сигналов
    if st.session_state['signals_history']:
        st.write("---")
        st.write("### 📜 История сигналов")
        history_df = pd.DataFrame(st.session_state['signals_history'])
        st.dataframe(history_df, use_container_width=True)

with tab4:
    st.write("### 📜 Системные логи")
    
    if st.session_state['signals_history']:
        for signal in st.session_state['signals_history'][-5:]:
            log_msg = f"[{signal['timestamp']}] {signal['action']} {signal['symbol']} @ ${signal['price']:.4f} | Уверенность: {signal['confidence']*100:.1f}%"
            st.code(log_msg)
    else:
        st.code("[INFO] Система ожидает первого сигнала...")
    
    # Тест API
    st.write("---")
    if st.button("🔍 Проверить подключение к RouterAI"):
        if not ai_token:
            st.error("Введите API ключ")
        else:
            with st.spinner("Проверка подключения..."):
                try:
                    response = requests.post(
                        url="https://routerai.ru/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {ai_token}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": "deepseek/deepseek-chat",
                            "messages": [{"role": "user", "content": "Ping"}],
                            "max_tokens": 10
                        },
                        timeout=10
                    )
                    if response.status_code == 200:
                        st.success("✅ Подключение к RouterAI успешно!")
                        st.json(response.json())
                    else:
                        st.error(f"❌ Ошибка {response.status_code}: {response.text}")
                except Exception as e:
                    st.error(f"❌ Не удалось подключиться: {e}")