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

# ==================== КОНФИГУРАЦИЯ ====================
st.set_page_config(
    page_title="MEXC Futures AI Trader",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== СТИЛИ ====================
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 700;
    }
    .profit-positive { color: #00ff88; font-weight: 700; }
    .profit-negative { color: #ff4757; font-weight: 700; }
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.2);
    }
</style>
""", unsafe_allow_html=True)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
def init_session():
    defaults = {
        'signals_history': [],
        'market_data': None,
        'auto_trading_active': False,
        'trading_logs': [],
        'open_positions': [],
        'closed_trades': [],
        'total_profit': 0.0,
        'initial_balance': None,
        'current_balance': None,
        'current_price': None,
        'last_symbol': 'BTC/USDT',
        'last_timeframe': '15m',
        'ai_model_name': 'deepseek/deepseek-v4-pro',
        'risk_percent': 5.0,
        'confidence_threshold': 0.65,
        'leverage': 5,
        'margin_type': 'isolated',  # isolated или cross
        'min_amount': 5.0,
        'max_positions': 3,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session()

# ==================== ФУНКЦИИ MEXC ====================
@st.cache_resource(ttl=60)
def init_mexc_futures(api_key, secret_key):
    """Инициализация MEXC Futures"""
    try:
        exchange = ccxt.mexc({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # ВАЖНО: фьючерсы
            },
            'timeout': 15000,
        })
        
        # Проверяем соединение
        exchange.load_markets()
        return exchange, None
    except Exception as e:
        return None, str(e)

def get_futures_balance(exchange):
    """Получение фьючерсного баланса"""
    try:
        balance = exchange.fetch_balance({'type': 'swap'})
        
        # MEXC фьючерсный баланс
        usdt_balance = 0
        if 'USDT' in balance:
            usdt_balance = balance['USDT'].get('free', 0)
        elif 'info' in balance and 'data' in balance['info']:
            for asset in balance['info']['data']:
                if asset.get('currency') == 'USDT':
                    usdt_balance = float(asset.get('availableBalance', 0))
        
        return usdt_balance, None
    except Exception as e:
        return 0, str(e)

def set_futures_leverage(exchange, symbol, leverage):
    """Установка плеча"""
    try:
        # Формат символа для фьючерсов
        futures_symbol = symbol.replace('/', '')  # BTC/USDT -> BTCUSDT
        
        # Устанавливаем плечо
        exchange.set_leverage(leverage, f"{futures_symbol}:USDT")
        return True, None
    except Exception as e:
        # Возможно плечо уже установлено
        if 'leverage' in str(e).lower() and 'same' in str(e).lower():
            return True, None
        return False, str(e)

def set_margin_type(exchange, symbol, margin_type='isolated'):
    """Установка типа маржи"""
    try:
        futures_symbol = symbol.replace('/', '')
        exchange.set_margin_mode(margin_type, f"{futures_symbol}:USDT")
        return True, None
    except Exception as e:
        # Возможно уже установлен
        return True, None

def get_current_price(exchange, symbol):
    """Получение текущей цены"""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker.get('last', 0), None
    except Exception as e:
        return 0, str(e)

def fetch_ohlcv_safe(exchange, symbol, timeframe='15m', limit=50):
    """Безопасное получение свечей"""
    for attempt in range(3):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if ohlcv and len(ohlcv) > 0:
                return ohlcv, None
        except Exception as e:
            if attempt == 2:
                return None, str(e)
            time.sleep(2)
    return None, "Не удалось загрузить данные"

# ==================== ТОРГОВЫЕ ФУНКЦИИ ====================
def execute_futures_trade(exchange, symbol, action, current_price):
    """Исполнение фьючерсной сделки"""
    try:
        # Формат для фьючерсов
        futures_symbol = symbol.replace('/', '') + ':USDT'  # BTCUSDT:USDT
        
        # Получаем баланс
        usdt_balance, bal_error = get_futures_balance(exchange)
        if bal_error:
            return False, f"❌ Ошибка баланса: {bal_error}"
        
        # Сохраняем начальный баланс
        if st.session_state['initial_balance'] is None and usdt_balance > 0:
            st.session_state['initial_balance'] = usdt_balance
        
        st.session_state['current_balance'] = usdt_balance
        
        if action == "BUY":
            # Проверяем лимит позиций
            if len(st.session_state['open_positions']) >= st.session_state['max_positions']:
                return False, f"❌ Максимум позиций ({st.session_state['max_positions']})"
            
            # Проверяем, нет ли уже позиции
            existing = [p for p in st.session_state['open_positions'] if p['symbol'] == symbol]
            if existing:
                return False, "❌ Уже есть позиция"
            
            # Устанавливаем плечо и маржу
            leverage = st.session_state.get('leverage', 5)
            margin_type = st.session_state.get('margin_type', 'isolated')
            
            set_futures_leverage(exchange, symbol, leverage)
            set_margin_type(exchange, symbol, margin_type)
            
            # Расчёт размера позиции с плечом
            risk_amount = usdt_balance * (st.session_state['risk_percent'] / 100)
            position_size_usdt = risk_amount * leverage
            
            if position_size_usdt < st.session_state['min_amount']:
                return False, f"❌ Сумма ${position_size_usdt:.2f} меньше минимума ${st.session_state['min_amount']}"
            
            # Количество контрактов
            amount = position_size_usdt / current_price
            
            # Получаем информацию о рынке для округления
            market = exchange.market(futures_symbol)
            amount = exchange.amount_to_precision(futures_symbol, amount)
            
            # СОЗДАЁМ ОРДЕР НА ПОКУПКУ
            order = exchange.create_market_buy_order(futures_symbol, amount)
            
            # Сохраняем позицию
            position = {
                'id': len(st.session_state['open_positions']) + 1,
                'symbol': symbol,
                'futures_symbol': futures_symbol,
                'type': 'LONG',
                'amount': float(amount),
                'entry_price': current_price,
                'leverage': leverage,
                'margin_type': margin_type,
                'timestamp': datetime.now(),
                'order_id': order.get('id', 'N/A'),
            }
            
            st.session_state['open_positions'].append(position)
            
            return True, f"✅ LONG {amount} {symbol} @ ${current_price:.4f} | Плечо: {leverage}x"
            
        elif action == "SELL":
            # Ищем открытые позиции для закрытия
            positions_to_close = [
                p for p in st.session_state['open_positions'] 
                if p['symbol'] == symbol
            ]
            
            if not positions_to_close:
                # Открываем шорт
                leverage = st.session_state.get('leverage', 5)
                margin_type = st.session_state.get('margin_type', 'isolated')
                
                set_futures_leverage(exchange, symbol, leverage)
                set_margin_type(exchange, symbol, margin_type)
                
                risk_amount = usdt_balance * (st.session_state['risk_percent'] / 100)
                position_size_usdt = risk_amount * leverage
                amount = position_size_usdt / current_price
                
                market = exchange.market(futures_symbol)
                amount = exchange.amount_to_precision(futures_symbol, amount)
                
                # ОТКРЫВАЕМ ШОРТ
                order = exchange.create_market_sell_order(futures_symbol, amount)
                
                position = {
                    'id': len(st.session_state['open_positions']) + 1,
                    'symbol': symbol,
                    'futures_symbol': futures_symbol,
                    'type': 'SHORT',
                    'amount': float(amount),
                    'entry_price': current_price,
                    'leverage': leverage,
                    'margin_type': margin_type,
                    'timestamp': datetime.now(),
                    'order_id': order.get('id', 'N/A'),
                }
                
                st.session_state['open_positions'].append(position)
                
                return True, f"✅ SHORT {amount} {symbol} @ ${current_price:.4f} | Плечо: {leverage}x"
            
            else:
                # Закрываем все позиции по этой паре
                total_profit = 0
                
                for pos in positions_to_close:
                    # Определяем сторону закрытия
                    if pos['type'] == 'LONG':
                        close_side = 'sell'
                        profit = (current_price - pos['entry_price']) * pos['amount']
                    else:  # SHORT
                        close_side = 'buy'
                        profit = (pos['entry_price'] - current_price) * pos['amount']
                    
                    total_profit += profit
                    
                    # Закрываем позицию
                    close_amount = exchange.amount_to_precision(futures_symbol, pos['amount'])
                    
                    if close_side == 'sell':
                        order = exchange.create_market_sell_order(futures_symbol, close_amount, {'reduceOnly': True})
                    else:
                        order = exchange.create_market_buy_order(futures_symbol, close_amount, {'reduceOnly': True})
                    
                    # Сохраняем в историю
                    closed_trade = {
                        **pos,
                        'exit_price': current_price,
                        'profit': profit,
                        'profit_percent': ((current_price - pos['entry_price']) / pos['entry_price']) * 100,
                        'close_timestamp': datetime.now(),
                        'status': 'CLOSED'
                    }
                    st.session_state['closed_trades'].append(closed_trade)
                    
                    # Удаляем из открытых
                    st.session_state['open_positions'].remove(pos)
                
                st.session_state['total_profit'] += total_profit
                
                emoji = "📈" if total_profit > 0 else "📉"
                return True, f"✅ Закрыта позиция {symbol} | P&L: ${total_profit:+.2f} {emoji}"
        
        return False, "⏸️ HOLD"
        
    except Exception as e:
        return False, f"❌ Ошибка: {str(e)[:200]}"

# ==================== AI АНАЛИЗ ====================
def analyze_with_ai(market_data, symbol, timeframe, current_price, ai_token, model_name):
    """Анализ через RouterAI"""
    
    system_prompt = """Ты — профессиональный фьючерсный трейдер. Проанализируй данные и дай рекомендацию.

ПРАВИЛА:
1. BUY (LONG) — при восходящем тренде
2. SELL (SHORT) — при нисходящем тренде
3. HOLD — при неопределённости
4. Уверенность ниже 0.6 — только HOLD

ОТВЕТЬ ТОЛЬКО JSON:
{"action": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, "reason": "объяснение на русском"}"""
    
    user_content = f"Символ: {symbol}\nТаймфрейм: {timeframe}\nЦена: ${current_price:,.4f}\nДанные: {market_data}"
    
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
                "temperature": 0.2,
                "max_tokens": 300
            },
            timeout=20
        )
        
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        
        result = response.json()
        ai_reply = result['choices'][0]['message']['content']
        
        # Парсим JSON
        ai_reply = re.sub(r'```json|```', '', ai_reply).strip()
        json_match = re.search(r'\{[^{}]*\}', ai_reply, re.DOTALL)
        
        if json_match:
            parsed = json.loads(json_match.group(0))
            parsed['confidence'] = float(parsed.get('confidence', 0.5))
            return parsed, None
        
        return None, "JSON не найден"
        
    except Exception as e:
        return None, str(e)

# ==================== АВТОТРЕЙДИНГ ====================
def auto_trading_loop():
    """Основной цикл"""
    
    # Создаём клиент
    exchange, error = init_mexc_futures(
        st.session_state.get('api_key', ''),
        st.session_state.get('secret_key', '')
    )
    
    if error:
        st.session_state['trading_logs'].append(f"❌ Ошибка: {error}")
        st.session_state['auto_trading_active'] = False
        return
    
    st.session_state['trading_logs'].append("🟢 Фьючерсный бот запущен")
    
    iteration = 0
    
    while st.session_state['auto_trading_active']:
        try:
            iteration += 1
            symbol = st.session_state['last_symbol']
            timeframe = st.session_state['last_timeframe']
            
            # 1. Получаем данные
            ohlcv, err = fetch_ohlcv_safe(exchange, symbol, timeframe, limit=50)
            if err:
                st.session_state['trading_logs'].append(f"❌ Данные: {err}")
                time.sleep(30)
                continue
            
            # 2. Обработка
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            current_price = df['c'].iloc[-1]
            st.session_state['current_price'] = current_price
            st.session_state['market_data'] = df.tail(15).to_json(orient="records")
            
            # 3. Обновляем баланс
            balance, _ = get_futures_balance(exchange)
            st.session_state['current_balance'] = balance
            
            # 4. AI анализ
            if st.session_state.get('ai_token'):
                ai_res, ai_err = analyze_with_ai(
                    df.tail(15).to_json(orient="records"),
                    symbol, timeframe, current_price,
                    st.session_state['ai_token'],
                    st.session_state['ai_model_name']
                )
                
                if ai_err:
                    if iteration % 10 == 0:
                        st.session_state['trading_logs'].append(f"⚠️ AI: {ai_err}")
                elif ai_res:
                    action = ai_res.get('action', 'HOLD')
                    confidence = ai_res.get('confidence', 0)
                    reason = ai_res.get('reason', '')
                    
                    if confidence >= st.session_state['confidence_threshold'] and action != 'HOLD':
                        # ИСПОЛНЯЕМ СДЕЛКУ
                        success, msg = execute_futures_trade(exchange, symbol, action, current_price)
                        
                        log = f"[{datetime.now().strftime('%H:%M:%S')}] {action} | Уверенность: {confidence*100:.0f}% | {msg}"
                        st.session_state['trading_logs'].append(log)
                        
                        # Сохраняем сигнал
                        st.session_state['signals_history'].append({
                            'timestamp': datetime.now().strftime("%H:%M:%S"),
                            'action': action,
                            'symbol': symbol,
                            'price': current_price,
                            'confidence': confidence,
                            'reason': reason,
                            'result': msg
                        })
                        
                        time.sleep(60)
                    else:
                        if iteration % 20 == 0:
                            st.session_state['trading_logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] HOLD | Уверенность: {confidence*100:.0f}%"
                            )
                        time.sleep(30)
            
            time.sleep(30)
            
        except Exception as e:
            st.session_state['trading_logs'].append(f"⚠️ Цикл: {str(e)[:150]}")
            time.sleep(30)
    
    st.session_state['trading_logs'].append("🔴 Бот остановлен")

# ==================== UI ====================
def main():
    st.markdown('<h1 class="main-header">🤖 MEXC Futures AI Bot</h1>', unsafe_allow_html=True)
    st.caption("Автотрейдинг фьючерсами с DeepSeek AI")
    
    # Сайдбар
    with st.sidebar:
        st.header("🔑 API Ключи")
        api_key = st.text_input("MEXC API Key", type="password", key="api_key")
        secret_key = st.text_input("MEXC Secret Key", type="password", key="secret_key")
        ai_token = st.text_input("RouterAI API Key", type="password", key="ai_token")
        
        st.divider()
        
        st.header("⚙️ Настройки")
        st.session_state['leverage'] = st.selectbox("Плечо", [1, 2, 3, 5, 10, 20], index=3)
        st.session_state['risk_percent'] = st.slider("Риск на сделку (%)", 1.0, 100.0, 5.0, 1.0)
        st.session_state['confidence_threshold'] = st.slider("Мин. уверенность AI (%)", 50, 95, 65, 5) / 100
        
        st.divider()
        
        # Статус
        is_active = st.session_state['auto_trading_active']
        st.metric("Статус", "🟢 Активен" if is_active else "🔴 Остановлен")
        st.metric("Баланс USDT", f"${st.session_state.get('current_balance', 0):,.2f}")
        st.metric("P&L", f"${st.session_state['total_profit']:+,.2f}")
    
    # Вкладки
    tab1, tab2, tab3 = st.tabs(["📊 Терминал", "🤖 Автотрейдинг", "📈 История"])
    
    with tab1:
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            symbol = st.selectbox("Пара", ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"])
            st.session_state['last_symbol'] = symbol
        with col2:
            timeframe = st.selectbox("Таймфрейм", ["5m", "15m", "30m", "1h", "4h"], index=1)
            st.session_state['last_timeframe'] = timeframe
        with col3:
            st.write("")
            st.write("")
            if st.button("🔄 Обновить", use_container_width=True):
                st.rerun()
        
        # График
        exchange, err = init_mexc_futures(
            st.session_state.get('api_key', ''),
            st.session_state.get('secret_key', '')
        )
        
        if exchange:
            ohlcv, err = fetch_ohlcv_safe(exchange, symbol, timeframe, 100)
            if not err:
                df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                df['t'] = pd.to_datetime(df['t'], unit='ms')
                
                current_price = df['c'].iloc[-1]
                st.session_state['current_price'] = current_price
                
                # Метрики
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Цена", f"${current_price:,.4f}")
                col2.metric("High", f"${df['h'].max():,.4f}")
                col3.metric("Low", f"${df['l'].min():,.4f}")
                col4.metric("Объём", f"{df['v'].sum():,.0f}")
                
                # График
                fig = go.Figure()
                fig.add_trace(go.Candlestick(
                    x=df['t'], open=df['o'], high=df['h'],
                    low=df['l'], close=df['c'],
                    increasing_line_color='#00ff88',
                    decreasing_line_color='#ff4757'
                ))
                fig.update_layout(template='plotly_dark', height=500)
                st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        col1, col2 = st.columns(2)
        with col1:
            if not st.session_state['auto_trading_active']:
                if st.button("▶️ ЗАПУСТИТЬ БОТА", type="primary", use_container_width=True):
                    if not api_key or not secret_key:
                        st.error("❌ Введите API ключи MEXC!")
                    elif not ai_token:
                        st.error("❌ Введите RouterAI API Key!")
                    else:
                        st.session_state['auto_trading_active'] = True
                        st.session_state['trading_logs'] = []
                        threading.Thread(target=auto_trading_loop, daemon=True).start()
                        st.rerun()
            else:
                st.success("🟢 Бот работает!")
        with col2:
            if st.button("⏸️ ОСТАНОВИТЬ", use_container_width=True):
                st.session_state['auto_trading_active'] = False
                st.rerun()
        
        # Логи
        st.divider()
        st.subheader("📜 Логи")
        for log in st.session_state['trading_logs'][-20:]:
            if '✅' in log:
                st.success(log)
            elif '❌' in log:
                st.error(log)
            else:
                st.info(log)
    
    with tab3:
        st.subheader("📈 История сделок")
        
        if st.session_state['closed_trades']:
            trades_df = pd.DataFrame(st.session_state['closed_trades'])
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Всего сделок", len(trades_df))
            col2.metric("Прибыльных", len(trades_df[trades_df['profit'] > 0]))
            col3.metric("P&L", f"${st.session_state['total_profit']:+,.2f}")
            
            st.dataframe(
                trades_df[['close_timestamp', 'symbol', 'type', 'entry_price', 'exit_price', 'profit']]
                .style.format({
                    'entry_price': '${:.4f}',
                    'exit_price': '${:.4f}',
                    'profit': '${:+.2f}'
                }),
                use_container_width=True
            )
        else:
            st.info("Нет закрытых сделок")

if __name__ == "__main__":
    main()