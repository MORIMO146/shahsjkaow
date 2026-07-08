import streamlit as st
import ccxt
import pandas as pd
import requests
import json
import re
import time
import threading
from datetime import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="MEXC AI Trader PRO", page_icon="🤖", layout="wide")

# CSS для улучшения интерфейса
st.markdown("""
<style>
    .big-metric {
        font-size: 28px !important;
        font-weight: bold;
    }
    .profit-positive {
        color: #00ff00;
        font-weight: bold;
    }
    .profit-negative {
        color: #ff0000;
        font-weight: bold;
    }
    .trade-buy {
        background-color: rgba(0, 255, 0, 0.1);
        padding: 10px;
        border-radius: 5px;
        margin: 5px 0;
    }
    .trade-sell {
        background-color: rgba(255, 0, 0, 0.1);
        padding: 10px;
        border-radius: 5px;
        margin: 5px 0;
    }
</style>
""", unsafe_allow_html=True)

st.title("🤖 MEXC AI Trader PRO с Автотрейдингом")

# Боковая панель
st.sidebar.header("🔑 Настройки API")
api_key = st.sidebar.text_input("MEXC API Key", type="password")
secret_key = st.sidebar.text_input("MEXC Secret Key", type="password")
ai_token = st.sidebar.text_input("RouterAI API Key", type="password")

# Инициализация всех сессионных переменных
if 'signals_history' not in st.session_state:
    st.session_state['signals_history'] = []
if 'market_data' not in st.session_state:
    st.session_state['market_data'] = None
if 'auto_trading_active' not in st.session_state:
    st.session_state['auto_trading_active'] = False
if 'trading_logs' not in st.session_state:
    st.session_state['trading_logs'] = []
if 'price_history' not in st.session_state:
    st.session_state['price_history'] = pd.DataFrame(columns=['timestamp', 'price'])
if 'open_positions' not in st.session_state:
    st.session_state['open_positions'] = []
if 'closed_trades' not in st.session_state:
    st.session_state['closed_trades'] = []
if 'total_profit' not in st.session_state:
    st.session_state['total_profit'] = 0.0
if 'initial_balance' not in st.session_state:
    st.session_state['initial_balance'] = None
if 'current_balance' not in st.session_state:
    st.session_state['current_balance'] = None

@st.cache_resource
def init_mexc(api_key, secret_key):
    """Инициализация MEXC клиента"""
    exchange_class = getattr(ccxt, 'mexc')
    exchange = exchange_class({
        'apiKey': api_key if api_key else '',
        'secret': secret_key if secret_key else '',
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    return exchange

def fetch_ohlcv_safe(exchange, symbol, timeframe='1h', limit=50):
    """Безопасное получение свечных данных"""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return ohlcv, None
    except Exception as e:
        error_msg = str(e)
        if 'capital/config' in error_msg or 'getall' in error_msg:
            try:
                base_url = 'https://api.mexc.com'
                endpoint = '/api/v3/klines'
                params = {
                    'symbol': symbol.replace('/', ''),
                    'interval': timeframe,
                    'limit': limit
                }
                response = requests.get(f"{base_url}{endpoint}", params=params)
                if response.status_code == 200:
                    data = response.json()
                    ohlcv = []
                    for candle in data:
                        ohlcv.append([
                            int(candle[0]),
                            float(candle[1]),
                            float(candle[2]),
                            float(candle[3]),
                            float(candle[4]),
                            float(candle[5])
                        ])
                    return ohlcv, None
                else:
                    return None, f"HTTP {response.status_code}"
            except Exception as alt_e:
                return None, str(alt_e)
        return None, error_msg

def get_current_price(exchange, symbol):
    """Получение текущей цены"""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, '1m', limit=1)
            return ohlcv[0][4] if ohlcv else None
        except:
            return None

def analyze_with_ai(market_data, symbol, timeframe, current_price, ai_token, model_name):
    """AI анализ рынка"""
    system_prompt = (
        "Ты — профессиональный трейдер-аналитик. Проанализируй данные и выдай СТРОГО JSON:\n"
        '{"action": "BUY"|"SELL"|"HOLD", "confidence": 0.XX, '
        '"reason": "объяснение", "stop_loss": цена, "take_profit": цена, '
        '"risk_reward": X.X}'
    )
    
    user_content = f"Символ: {symbol}\nТаймфрейм: {timeframe}\nЦена: {current_price}\nДанные: {market_data}"
    
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
            return None, f"HTTP {response.status_code}"
        
        result = response.json()
        if 'choices' not in result:
            return None, "Нет ответа модели"
        
        ai_reply = result['choices'][0]['message']['content']
        
        # Очистка JSON
        ai_reply = re.sub(r'```json|```', '', ai_reply).strip()
        json_match = re.search(r'\{[^{}]*\}', ai_reply, re.DOTALL)
        if json_match:
            ai_reply = json_match.group(0)
        
        return json.loads(ai_reply), None
        
    except Exception as e:
        return None, str(e)

def execute_trade(exchange, symbol, action, current_price, amount=None, risk_percent=1.0):
    """Исполнение сделки"""
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        
        if st.session_state['initial_balance'] is None:
            st.session_state['initial_balance'] = usdt_balance
        st.session_state['current_balance'] = usdt_balance
        
        if action == "BUY":
            if amount is None:
                position_size = usdt_balance * (risk_percent / 100)
                amount = position_size / current_price
            
            market = exchange.market(symbol)
            amount = exchange.amount_to_precision(symbol, amount)
            
            order = exchange.create_market_buy_order(symbol, amount)
            
            trade = {
                'type': 'BUY',
                'symbol': symbol,
                'amount': float(amount),
                'price': current_price,
                'timestamp': datetime.now(),
                'order_id': order['id'],
                'status': 'OPEN'
            }
            st.session_state['open_positions'].append(trade)
            
            return True, f"✅ Куплено {amount} {symbol.split('/')[0]} по ${current_price:.4f}"
            
        elif action == "SELL":
            base_currency = symbol.split('/')[0]
            base_balance = balance[base_currency]['free']
            
            if base_balance > 0:
                sell_amount = exchange.amount_to_precision(symbol, base_balance)
                order = exchange.create_market_sell_order(symbol, sell_amount)
                
                # Закрываем открытые позиции
                profit = 0
                for pos in st.session_state['open_positions']:
                    if pos['type'] == 'BUY':
                        pos_profit = (current_price - pos['price']) * pos['amount']
                        profit += pos_profit
                        pos['status'] = 'CLOSED'
                        pos['exit_price'] = current_price
                        pos['profit'] = pos_profit
                        st.session_state['closed_trades'].append(pos)
                
                st.session_state['open_positions'] = [
                    p for p in st.session_state['open_positions'] 
                    if p['status'] == 'OPEN'
                ]
                
                st.session_state['total_profit'] += profit
                
                return True, f"✅ Продано {sell_amount} {base_currency} по ${current_price:.4f} | Прибыль: ${profit:.2f}"
            else:
                return False, "Нет актива для продажи"
                
        return False, "HOLD"
        
    except Exception as e:
        return False, f"Ошибка: {str(e)}"

def auto_trading_loop(exchange, symbol, timeframe, ai_token, model_name, risk_percent, confidence_threshold):
    """Цикл автотрейдинга"""
    st.session_state['auto_trading_active'] = True
    
    while st.session_state['auto_trading_active']:
        try:
            # Получаем данные
            ohlcv, error = fetch_ohlcv_safe(exchange, symbol, timeframe, limit=50)
            if error:
                st.session_state['trading_logs'].append(f"❌ Ошибка данных: {error}")
                time.sleep(30)
                continue
            
            # Обновляем график цен
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            current_price = df['close'].iloc[-1]
            
            new_row = pd.DataFrame({
                'timestamp': [datetime.now()],
                'price': [current_price]
            })
            st.session_state['price_history'] = pd.concat(
                [st.session_state['price_history'], new_row], 
                ignore_index=True
            ).tail(100)
            
            # AI анализ
            market_json = df.tail(15).to_json(orient="records")
            ai_json, ai_error = analyze_with_ai(
                market_json, symbol, timeframe, current_price, ai_token, model_name
            )
            
            if ai_error:
                st.session_state['trading_logs'].append(f"❌ AI ошибка: {ai_error}")
                time.sleep(30)
                continue
            
            action = ai_json.get('action', 'HOLD')
            confidence = float(ai_json.get('confidence', 0))
            
            if confidence >= confidence_threshold and action != 'HOLD':
                success, msg = execute_trade(
                    exchange, symbol, action, current_price, 
                    risk_percent=risk_percent
                )
                
                log = f"[{datetime.now().strftime('%H:%M:%S')}] {action} {symbol} | Уверенность: {confidence*100:.0f}% | {msg}"
                st.session_state['trading_logs'].append(log)
                
                signal_record = {
                    'timestamp': datetime.now().strftime("%H:%M:%S"),
                    'action': action,
                    'symbol': symbol,
                    'price': current_price,
                    'confidence': confidence,
                    'reason': ai_json.get('reason', ''),
                    'result': msg
                }
                st.session_state['signals_history'].append(signal_record)
                
                # Ждем дольше после сделки
                time.sleep(120)
            else:
                time.sleep(30)
                
        except Exception as e:
            st.session_state['trading_logs'].append(f"⚠️ Ошибка цикла: {str(e)}")
            time.sleep(30)

# Инициализация MEXC
mexc_client = init_mexc(api_key, secret_key)

# Основной интерфейс с вкладками
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Рынок в реальном времени", 
    "🤖 Автотрейдинг", 
    "📈 История сделок",
    "⚙️ Настройки"
])

with tab1:
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        symbol = st.selectbox("Торговая пара", 
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT"],
            index=0
        )
    with col2:
        timeframe = st.selectbox("Таймфрейм", ["1m", "5m", "15m", "1h", "4h", "1d"], index=3)
    with col3:
        st.write("")
        st.write("")
        refresh_button = st.button("🔄 Обновить", use_container_width=True)
    
    # График в реальном времени
    chart_placeholder = st.empty()
    metrics_placeholder = st.empty()
    
    # Автообновление каждые 5 секунд
    if 'last_update' not in st.session_state:
        st.session_state['last_update'] = 0
    
    current_time = time.time()
    if current_time - st.session_state['last_update'] > 5 or refresh_button:
        st.session_state['last_update'] = current_time
        
        with st.spinner("Загрузка данных..."):
            ohlcv, error = fetch_ohlcv_safe(mexc_client, symbol, timeframe, limit=100)
            
            if error:
                st.error(f"Ошибка: {error}")
            else:
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                
                current_price = df['close'].iloc[-1]
                price_change = df['close'].iloc[-1] - df['close'].iloc[-2]
                price_change_pct = (price_change / df['close'].iloc[-2]) * 100
                
                # Сохраняем в сессию
                st.session_state['market_data'] = df.tail(15).to_json(orient="records")
                st.session_state['current_price'] = current_price
                st.session_state['last_symbol'] = symbol
                st.session_state['last_timeframe'] = timeframe
                
                # Обновляем историю цен
                if len(st.session_state['price_history']) == 0:
                    st.session_state['price_history'] = df[['timestamp', 'close']].rename(
                        columns={'close': 'price'}
                    )
                
                # Метрики
                with metrics_placeholder.container():
                    col1, col2, col3, col4, col5 = st.columns(5)
                    
                    col1.metric(
                        "Текущая цена", 
                        f"${current_price:,.4f}",
                        f"{price_change_pct:+.2f}%"
                    )
                    col2.metric("24h High", f"${df['high'].max():,.4f}")
                    col3.metric("24h Low", f"${df['low'].min():,.4f}")
                    col4.metric("Объём", f"${df['volume'].sum():,.0f}")
                    
                    # Прибыль если есть
                    if st.session_state['total_profit'] != 0:
                        profit_color = "green" if st.session_state['total_profit'] > 0 else "red"
                        col5.metric(
                            "Общая прибыль", 
                            f"${st.session_state['total_profit']:,.2f}",
                            delta=f"{st.session_state['total_profit']:+,.2f}"
                        )
                    else:
                        col5.metric("Общая прибыль", "$0.00")
                
                # График с Plotly
                with chart_placeholder.container():
                    fig = make_subplots(
                        rows=2, cols=1,
                        shared_xaxes=True,
                        vertical_spacing=0.03,
                        row_heights=[0.7, 0.3]
                    )
                    
                    # Свечной график
                    fig.add_trace(
                        go.Candlestick(
                            x=df['timestamp'],
                            open=df['open'],
                            high=df['high'],
                            low=df['low'],
                            close=df['close'],
                            name=symbol
                        ),
                        row=1, col=1
                    )
                    
                    # Объёмы
                    colors = ['green' if close >= open else 'red' 
                             for close, open in zip(df['close'], df['open'])]
                    fig.add_trace(
                        go.Bar(
                            x=df['timestamp'],
                            y=df['volume'],
                            name='Volume',
                            marker_color=colors
                        ),
                        row=2, col=1
                    )
                    
                    fig.update_layout(
                        title=f'{symbol} - {timeframe}',
                        yaxis_title='Price (USDT)',
                        xaxis_title='Time',
                        template='plotly_dark',
                        height=600,
                        showlegend=False
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.write("### 🤖 Автотрейдинг с AI")
    
    # Статус автотрейдинга
    status_color = "🟢" if st.session_state['auto_trading_active'] else "🔴"
    st.write(f"**Статус:** {status_color} {'Активен' if st.session_state['auto_trading_active'] else 'Остановлен'}")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if not st.session_state['auto_trading_active']:
            if st.button("▶️ Запустить автотрейдинг", type="primary", use_container_width=True):
                if not api_key or not secret_key:
                    st.error("❌ Нужны API ключи MEXC!")
                elif not ai_token:
                    st.error("❌ Нужен RouterAI API Key!")
                else:
                    trading_thread = threading.Thread(
                        target=auto_trading_loop,
                        args=(
                            mexc_client,
                            st.session_state.get('last_symbol', 'BTC/USDT'),
                            st.session_state.get('last_timeframe', '1h'),
                            ai_token,
                            st.session_state.get('ai_model_name', 'deepseek/deepseek-v4-pro'),
                            st.session_state.get('risk_percent', 1.0),
                            st.session_state.get('confidence_threshold', 0.7)
                        ),
                        daemon=True
                    )
                    trading_thread.start()
                    st.success("🟢 Автотрейдинг запущен!")
                    st.rerun()
    
    with col2:
        if st.session_state['auto_trading_active']:
            if st.button("⏸️ Остановить", use_container_width=True):
                st.session_state['auto_trading_active'] = False
                st.warning("Остановка...")
                st.rerun()
    
    with col3:
        if st.button("📊 Статистика", use_container_width=True):
            total_trades = len(st.session_state['closed_trades'])
            winning_trades = len([t for t in st.session_state['closed_trades'] if t.get('profit', 0) > 0])
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            st.metric("Всего сделок", total_trades)
            st.metric("Win Rate", f"{win_rate:.1f}%")
            st.metric("Общая прибыль", f"${st.session_state['total_profit']:,.2f}")
    
    # Логи в реальном времени
    st.write("---")
    st.write("### 📜 Логи автотрейдинга")
    
    log_container = st.empty()
    with log_container.container():
        if st.session_state['trading_logs']:
            for log in st.session_state['trading_logs'][-20:]:
                if '✅' in log:
                    st.success(log)
                elif '❌' in log:
                    st.error(log)
                else:
                    st.info(log)
        else:
            st.info("Ожидание начала торговли...")

with tab3:
    st.write("### 📈 История сделок и прибыль")
    
    # Сводка
    col1, col2, col3, col4 = st.columns(4)
    
    total_trades = len(st.session_state['closed_trades']) + len(st.session_state['open_positions'])
    winning_trades = len([t for t in st.session_state['closed_trades'] if t.get('profit', 0) > 0])
    losing_trades = len([t for t in st.session_state['closed_trades'] if t.get('profit', 0) < 0])
    
    col1.metric("Всего сделок", total_trades)
    col2.metric("Прибыльных", winning_trades)
    col3.metric("Убыточных", losing_trades)
    
    win_rate = (winning_trades / (winning_trades + losing_trades) * 100) if (winning_trades + losing_trades) > 0 else 0
    col4.metric("Win Rate", f"{win_rate:.1f}%")
    
    # Прибыль
    st.write("---")
    st.write("### 💰 Финансовый результат")
    
    col1, col2, col3 = st.columns(3)
    
    initial_balance = st.session_state.get('initial_balance', 0)
    current_balance = st.session_state.get('current_balance', 0)
    
    if initial_balance and current_balance:
        total_return = ((current_balance - initial_balance) / initial_balance) * 100
        
        col1.metric("Начальный баланс", f"${initial_balance:,.2f}")
        col2.metric("Текущий баланс", f"${current_balance:,.2f}", 
                   delta=f"${current_balance - initial_balance:+,.2f}")
        col3.metric("Доходность", f"{total_return:+.2f}%")
    
    # Открытые позиции
    if st.session_state['open_positions']:
        st.write("---")
        st.write("### 📊 Открытые позиции")
        
        for pos in st.session_state['open_positions']:
            current_price = st.session_state.get('current_price', pos['price'])
            unrealized_pl = (current_price - pos['price']) * pos['amount']
            pl_percent = ((current_price - pos['price']) / pos['price']) * 100
            
            col1, col2, col3, col4 = st.columns(4)
            col1.write(f"**{pos['symbol']}**")
            col2.write(f"Куплено: {pos['amount']:.6f}")
            col3.write(f"Цена входа: ${pos['price']:.4f}")
            col4.write(f"P&L: ${unrealized_pl:+.2f} ({pl_percent:+.2f}%)")
    
    # История закрытых сделок
    if st.session_state['closed_trades']:
        st.write("---")
        st.write("### 📜 Закрытые сделки")
        
        trades_df = pd.DataFrame(st.session_state['closed_trades'])
        trades_df['profit'] = trades_df['profit'].round(2)
        trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'])
        
        # Стилизуем датафрейм
        def color_profit(val):
            color = 'green' if val > 0 else 'red' if val < 0 else 'gray'
            return f'color: {color}'
        
        styled_df = trades_df[['timestamp', 'symbol', 'price', 'exit_price', 'amount', 'profit']].style\
            .applymap(color_profit, subset=['profit'])\
            .format({
                'price': '${:.4f}',
                'exit_price': '${:.4f}',
                'amount': '{:.6f}',
                'profit': '${:+.2f}'
            })
        
        st.dataframe(styled_df, use_container_width=True)
        
        # График прибыли
        if len(trades_df) > 0:
            trades_df['cumulative_profit'] = trades_df['profit'].cumsum()
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=trades_df['timestamp'],
                y=trades_df['cumulative_profit'],
                mode='lines+markers',
                name='Cumulative P&L',
                fill='tozeroy',
                fillcolor='rgba(0,255,0,0.1)'
            ))
            
            fig.update_layout(
                title='Рост прибыли',
                yaxis_title='Profit (USDT)',
                template='plotly_dark',
                height=400
            )
            
            st.plotly_chart(fig, use_container_width=True)

with tab4:
    st.write("### ⚙️ Настройки AI и рисков")
    
    ai_choice = st.selectbox(
        "AI Модель",
        ["DeepSeek V4 Pro", "DeepSeek Chat", "DeepSeek Coder"]
    )
    
    model_map = {
        "DeepSeek V4 Pro": "deepseek/deepseek-v4-pro",
        "DeepSeek Chat": "deepseek/deepseek-chat",
        "DeepSeek Coder": "deepseek/deepseek-coder"
    }
    st.session_state['ai_model_name'] = model_map[ai_choice]
    
    col1, col2 = st.columns(2)
    with col1:
        risk_percent = st.slider(
            "Риск на сделку (% от баланса)", 
            0.1, 10.0, 1.0, 0.1,
            help="Процент от USDT баланса на одну сделку"
        )
        st.session_state['risk_percent'] = risk_percent
    
    with col2:
        confidence_threshold = st.slider(
            "Минимальная уверенность AI (%)",
            50, 95, 70, 5,
            help="Сигнал исполняется только если уверенность выше порога"
        )
        st.session_state['confidence_threshold'] = confidence_threshold / 100
    
    st.write("---")
    
    # Кнопки управления
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Сбросить статистику", use_container_width=True):
            st.session_state['closed_trades'] = []
            st.session_state['open_positions'] = []
            st.session_state['total_profit'] = 0.0
            st.session_state['initial_balance'] = None
            st.session_state['signals_history'] = []
            st.session_state['trading_logs'] = []
            st.success("✅ Статистика сброшена!")
            st.rerun()
    
    with col2:
        if st.button("💾 Экспорт истории", use_container_width=True):
            if st.session_state['closed_trades']:
                df = pd.DataFrame(st.session_state['closed_trades'])
                csv = df.to_csv(index=False)
                st.download_button(
                    "📥 Скачать CSV",
                    csv,
                    "trades_history.csv",
                    "text/csv"
                )
    
    # Информация о системе
    st.write("---")
    st.write("### ℹ️ О системе")
    st.info("""
    **MEXC AI Trader PRO** использует искусственный интеллект DeepSeek через RouterAI 
    для анализа рынка и автоматического исполнения сделок.
    
    **Как это работает:**
    1. Система получает рыночные данные каждые 30 секунд
    2. AI анализирует паттерны и выдает торговый сигнал
    3. При достаточной уверенности сделка исполняется автоматически
    4. Все результаты записываются в историю
    
    **⚠️ Предупреждение:** Торговля криптовалютами связана с высоким риском. 
    Используйте систему осторожно и только с деньгами, которые готовы потерять.
    """)