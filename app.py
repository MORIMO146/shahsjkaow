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

st.markdown("""
<style>
    .big-metric { font-size: 28px !important; font-weight: bold; }
    .profit-positive { color: #00ff00; font-weight: bold; }
    .profit-negative { color: #ff0000; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# Инициализация стейта
defaults = {
    'signals_history': [], 'market_data': None, 'auto_trading_active': False,
    'trading_logs': [], 'price_history': pd.DataFrame(columns=['timestamp', 'price']),
    'open_positions': [], 'closed_trades': [], 'total_profit': 0.0,
    'initial_balance': None, 'current_balance': None, 'min_amount': 10.0,
    'last_symbol': 'BTC/USDT', 'last_timeframe': '1h', 'current_price': None
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# --- ФУНКЦИИ ---

@st.cache_resource
def init_mexc(api_key, secret_key):
    """Инициализация MEXC клиента"""
    exchange = ccxt.mexc({
        'apiKey': api_key or '',
        'secret': secret_key or '',
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
    return exchange

def fetch_ohlcv_safe(exchange, symbol, timeframe='1h', limit=50):
    """Безопасное получение данных"""
    try:
        return exchange.fetch_ohlcv(symbol, timeframe, limit=limit), None
    except Exception as e:
        return None, str(e)

def get_current_price(exchange, symbol):
    """Получить текущую цену"""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except:
        return None

def analyze_with_ai(market_data, symbol, timeframe, current_price, ai_token, model_name):
    """AI анализ рынка"""
    system_prompt = (
        'Ты профессиональный трейдер. Проанализируй свечные данные и верни СТРОГО JSON:\n'
        '{"action": "BUY"|"SELL"|"HOLD", "confidence": 0.XX, '
        '"reason": "объяснение на русском", "stop_loss": цена, "take_profit": цена}'
    )
    
    user_content = (
        f"Символ: {symbol}\nТаймфрейм: {timeframe}\n"
        f"Текущая цена: {current_price}\nДанные свечей: {market_data}"
    )
    
    try:
        response = requests.post(
            "https://routerai.ru/api/v1/chat/completions",
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
            timeout=20
        )
        
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        
        result = response.json()
        if 'choices' not in result or not result['choices']:
            return None, "Нет ответа от модели"
        
        ai_reply = result['choices'][0]['message']['content']
        ai_reply = re.sub(r'```json|```', '', ai_reply).strip()
        json_match = re.search(r'\{[^{}]*\}', ai_reply, re.DOTALL)
        
        if json_match:
            return json.loads(json_match.group(0)), None
        return None, "JSON не найден в ответе"
        
    except Exception as e:
        return None, str(e)

def execute_trade(exchange, symbol, action, current_price, risk_percent):
    """Исполнение сделки"""
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        
        # Сохраняем начальный баланс
        if st.session_state['initial_balance'] is None:
            st.session_state['initial_balance'] = usdt_balance
        
        st.session_state['current_balance'] = usdt_balance
        min_required = st.session_state.get('min_amount', 10.0)
        
        if action == "BUY":
            position_size = usdt_balance * (risk_percent / 100)
            
            if position_size < min_required:
                return False, f"❌ Сумма ${position_size:.2f} ниже лимита ${min_required}"
            
            amount = position_size / current_price
            amount = exchange.amount_to_precision(symbol, amount)
            
            order = exchange.create_market_buy_order(symbol, amount)
            
            # Сохраняем позицию
            st.session_state['open_positions'].append({
                'symbol': symbol,
                'amount': float(amount),
                'price': current_price,
                'type': 'BUY',
                'timestamp': datetime.now(),
                'order_id': order.get('id', 'N/A')
            })
            
            return True, f"✅ Куплено {amount} {symbol.split('/')[0]} по ${current_price:.4f}"
            
        elif action == "SELL":
            base_currency = symbol.split('/')[0]
            base_balance = balance.get(base_currency, {}).get('free', 0)
            
            if base_balance <= 0:
                return False, "❌ Нет актива для продажи"
            
            sell_amount = exchange.amount_to_precision(symbol, base_balance)
            order = exchange.create_market_sell_order(symbol, sell_amount)
            
            # Закрываем открытые позиции и считаем прибыль
            profit = 0
            for pos in st.session_state['open_positions'][:]:
                if pos['type'] == 'BUY' and pos['symbol'] == symbol:
                    pos_profit = (current_price - pos['price']) * pos['amount']
                    profit += pos_profit
                    
                    st.session_state['closed_trades'].append({
                        **pos,
                        'exit_price': current_price,
                        'profit': pos_profit,
                        'status': 'CLOSED'
                    })
                    st.session_state['open_positions'].remove(pos)
            
            st.session_state['total_profit'] += profit
            
            return True, f"✅ Продано {sell_amount} {base_currency} по ${current_price:.4f} | P&L: ${profit:+.2f}"
        
        return False, "⏸️ HOLD - сделка не открыта"
        
    except Exception as e:
        return False, f"❌ Ошибка: {str(e)}"

def auto_trading_loop(symbol, timeframe, ai_token, model_name, risk_percent, confidence_threshold):
    """Цикл автотрейдинга"""
    # Создаём отдельный клиент для потока
    exchange = ccxt.mexc({
        'apiKey': st.session_state.get('api_key', ''),
        'secret': st.session_state.get('secret_key', ''),
        'enableRateLimit': True
    })
    
    while st.session_state.get('auto_trading_active', False):
        try:
            ohlcv, err = fetch_ohlcv_safe(exchange, symbol, timeframe)
            if err:
                st.session_state['trading_logs'].append(f"❌ Ошибка данных: {err}")
                time.sleep(30)
                continue
            
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            current_price = df['c'].iloc[-1]
            
            # Обновляем цену в стейте
            st.session_state['current_price'] = current_price
            st.session_state['last_symbol'] = symbol
            st.session_state['last_timeframe'] = timeframe
            
            # AI анализ
            market_json = df.tail(10).to_json(orient="records")
            ai_res, ai_err = analyze_with_ai(
                market_json, symbol, timeframe, current_price, ai_token, model_name
            )
            
            if ai_err:
                st.session_state['trading_logs'].append(f"❌ AI ошибка: {ai_err}")
                time.sleep(30)
                continue
            
            if ai_res and ai_res.get('confidence', 0) >= confidence_threshold:
                action = ai_res.get('action', 'HOLD')
                
                if action in ['BUY', 'SELL']:
                    success, msg = execute_trade(
                        exchange, symbol, action, current_price, risk_percent
                    )
                    
                    log = f"[{datetime.now().strftime('%H:%M:%S')}] {action} {symbol} | Уверенность: {ai_res.get('confidence', 0)*100:.0f}% | {msg}"
                    st.session_state['trading_logs'].append(log)
                    
                    # Сохраняем сигнал в историю
                    st.session_state['signals_history'].append({
                        'timestamp': datetime.now().strftime("%H:%M:%S"),
                        'action': action,
                        'symbol': symbol,
                        'price': current_price,
                        'confidence': ai_res.get('confidence', 0),
                        'reason': ai_res.get('reason', ''),
                        'result': msg
                    })
                    
                    time.sleep(120)  # Пауза после сделки
                else:
                    time.sleep(30)
            else:
                time.sleep(30)
                
        except Exception as e:
            st.session_state['trading_logs'].append(f"⚠️ Ошибка цикла: {str(e)}")
            time.sleep(30)

# --- UI ИНТЕРФЕЙС ---

st.title("🤖 MEXC AI Trader PRO с DeepSeek")

# Сайдбар
with st.sidebar:
    st.header("🔑 API Ключи")
    api_key = st.text_input("MEXC API Key", type="password", key="api_key")
    secret_key = st.text_input("MEXC Secret Key", type="password", key="secret_key")
    ai_token = st.text_input("RouterAI API Key", type="password")
    
    st.write("---")
    st.session_state['min_amount'] = st.number_input(
        "Мин. сумма сделки (USDT)", 
        min_value=5.0, 
        value=10.0,
        help="Минимальная сумма для открытия сделки"
    )
    
    st.write("---")
    st.write("### 📊 Статус")
    status = "🟢 Онлайн" if st.session_state['auto_trading_active'] else "🔴 Оффлайн"
    st.write(f"**Автотрейдинг:** {status}")
    st.write(f"**Прибыль:** ${st.session_state['total_profit']:+.2f}")

# Инициализация клиента MEXC (для основного интерфейса)
mexc_client = init_mexc(api_key, secret_key)

# Вкладки
tab1, tab2, tab3, tab4 = st.tabs(["📊 Рынок", "🤖 Автотрейдинг", "📈 История", "⚙️ Настройки"])

with tab1:
    st.write("### 📊 Данные рынка в реальном времени")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        symbol = st.selectbox("Торговая пара", 
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"],
            index=0
        )
    with col2:
        timeframe = st.selectbox("Таймфрейм", 
            ["1m", "5m", "15m", "1h", "4h", "1d"],
            index=3
        )
    with col3:
        st.write("")
        st.write("")
        if st.button("🔄 Обновить", use_container_width=True):
            st.rerun()
    
    # Загрузка и отображение данных
    ohlcv, error = fetch_ohlcv_safe(mexc_client, symbol, timeframe, limit=100)
    
    if error:
        st.error(f"❌ Ошибка загрузки данных: {error}")
    else:
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        current_price = df['close'].iloc[-1]
        price_change = df['close'].iloc[-1] - df['close'].iloc[-2]
        price_change_pct = (price_change / df['close'].iloc[-2]) * 100
        
        # Сохраняем в стейт
        st.session_state['market_data'] = df.tail(10).to_json(orient="records")
        st.session_state['current_price'] = current_price
        st.session_state['last_symbol'] = symbol
        st.session_state['last_timeframe'] = timeframe
        
        # Метрики
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Текущая цена", f"${current_price:,.4f}", f"{price_change_pct:+.2f}%")
        col2.metric("24h High", f"${df['high'].max():,.4f}")
        col3.metric("24h Low", f"${df['low'].min():,.4f}")
        col4.metric("Объём", f"${df['volume'].sum():,.0f}")
        
        # График Plotly
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3]
        )
        
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
        
        colors = ['green' if c >= o else 'red' for c, o in zip(df['close'], df['open'])]
        fig.add_trace(
            go.Bar(x=df['timestamp'], y=df['volume'], name='Volume', marker_color=colors),
            row=2, col=1
        )
        
        fig.update_layout(
            title=f'{symbol} - {timeframe}',
            yaxis_title='Price (USDT)',
            template='plotly_dark',
            height=500,
            showlegend=False
        )
        
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.write("### 🤖 Автотрейдинг с AI")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if not st.session_state['auto_trading_active']:
            if st.button("▶️ Запустить автотрейдинг", type="primary", use_container_width=True):
                if not api_key or not secret_key:
                    st.error("❌ Нужны API ключи MEXC!")
                elif not ai_token:
                    st.error("❌ Нужен RouterAI API Key!")
                else:
                    st.session_state['auto_trading_active'] = True
                    
                    thread = threading.Thread(
                        target=auto_trading_loop,
                        args=(
                            st.session_state.get('last_symbol', 'BTC/USDT'),
                            st.session_state.get('last_timeframe', '1h'),
                            ai_token,
                            st.session_state.get('ai_model_name', 'deepseek/deepseek-v4-pro'),
                            st.session_state.get('risk_percent', 1.0),
                            st.session_state.get('confidence_threshold', 0.7)
                        ),
                        daemon=True
                    )
                    thread.start()
                    st.success("🟢 Автотрейдинг запущен!")
                    st.rerun()
    
    with col2:
        if st.session_state['auto_trading_active']:
            if st.button("⏸️ Остановить", use_container_width=True):
                st.session_state['auto_trading_active'] = False
                st.warning("⏸️ Останавливаем...")
                st.rerun()
    
    with col3:
        if st.button("🔄 Сбросить логи", use_container_width=True):
            st.session_state['trading_logs'] = []
            st.rerun()
    
    # Логи
    st.write("---")
    st.write("### 📜 Логи автотрейдинга")
    
    if st.session_state['trading_logs']:
        for log in st.session_state['trading_logs'][-20:]:
            if '✅' in log:
                st.success(log)
            elif '❌' in log:
                st.error(log)
            else:
                st.info(log)
    else:
        st.info("Ожидание запуска автотрейдинга...")

with tab3:
    st.write("### 📈 История сделок")
    
    col1, col2, col3 = st.columns(3)
    total_trades = len(st.session_state['closed_trades'])
    winning = len([t for t in st.session_state['closed_trades'] if t.get('profit', 0) > 0])
    
    col1.metric("Всего сделок", total_trades)
    col2.metric("Прибыльных", winning)
    
    if total_trades > 0:
        col3.metric("Win Rate", f"{(winning/total_trades)*100:.1f}%")
    else:
        col3.metric("Win Rate", "0%")
    
    # Прибыль
    if st.session_state.get('initial_balance') and st.session_state.get('current_balance'):
        initial = st.session_state['initial_balance']
        current = st.session_state['current_balance']
        roi = ((current - initial) / initial) * 100 if initial > 0 else 0
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Начальный баланс", f"${initial:,.2f}")
        col2.metric("Текущий баланс", f"${current:,.2f}", f"${current - initial:+,.2f}")
        col3.metric("ROI", f"{roi:+.2f}%")
    
    # Таблица сделок
    if st.session_state['closed_trades']:
        st.write("---")
        trades_df = pd.DataFrame(st.session_state['closed_trades'])
        st.dataframe(
            trades_df[['timestamp', 'symbol', 'price', 'exit_price', 'amount', 'profit']].style
            .format({'price': '${:.4f}', 'exit_price': '${:.4f}', 'profit': '${:+.2f}'}),
            use_container_width=True
        )

with tab4:
    st.write("### ⚙️ Настройки")
    
    ai_choice = st.selectbox("AI Модель", ["DeepSeek V4 Pro", "DeepSeek Chat"])
    model_map = {
        "DeepSeek V4 Pro": "deepseek/deepseek-v4-pro",
        "DeepSeek Chat": "deepseek/deepseek-chat"
    }
    st.session_state['ai_model_name'] = model_map[ai_choice]
    
    col1, col2 = st.columns(2)
    with col1:
        risk = st.slider("Риск на сделку (%)", 0.1, 10.0, 1.0, 0.1)
        st.session_state['risk_percent'] = risk
    with col2:
        conf = st.slider("Мин. уверенность AI (%)", 50, 95, 70, 5)
        st.session_state['confidence_threshold'] = conf / 100