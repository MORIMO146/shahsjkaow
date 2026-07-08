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

# ==================== ИНИЦИАЛИЗАЦИЯ СЕССИИ ====================
def init_session():
    defaults = {
        'signals_history': [],
        'market_data': None,
        'auto_trading_active': False,
        'trading_logs': [],
        'open_positions': [],
        'closed_trades': [],
        'total_profit': 0.0,
        'initial_balance': 0.0,
        'current_balance': 0.0,
        'current_price': 0.0,
        'last_symbol': 'BTC/USDT',
        'last_timeframe': '15m',
        'ai_model_name': 'deepseek/deepseek-v4-pro',
        'risk_percent': 5.0,
        'confidence_threshold': 0.65,
        'leverage': 5,
        'margin_type': 'isolated',
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
            'apiKey': api_key or '',
            'secret': secret_key or '',
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
            },
            'timeout': 15000,
        })
        exchange.load_markets()
        return exchange, None
    except Exception as e:
        return None, str(e)

def get_futures_balance(exchange):
    """Получение фьючерсного баланса"""
    try:
        balance = exchange.fetch_balance({'type': 'swap'})
        usdt_balance = 0.0
        
        if 'USDT' in balance:
            usdt_balance = float(balance['USDT'].get('free', 0))
        elif 'info' in balance and 'data' in balance['info']:
            for asset in balance['info']['data']:
                if asset.get('currency') == 'USDT':
                    usdt_balance = float(asset.get('availableBalance', 0))
        
        return usdt_balance, None
    except Exception as e:
        return 0.0, str(e)

def set_futures_leverage(exchange, symbol, leverage):
    """Установка плеча"""
    try:
        futures_symbol = symbol.replace('/', '') + ':USDT'
        exchange.set_leverage(leverage, futures_symbol)
        return True, None
    except Exception as e:
        if 'same' in str(e).lower() or 'leverage' in str(e).lower():
            return True, None
        return False, str(e)

def set_margin_type(exchange, symbol, margin_type='isolated'):
    """Установка типа маржи"""
    try:
        futures_symbol = symbol.replace('/', '') + ':USDT'
        exchange.set_margin_mode(margin_type, futures_symbol)
        return True, None
    except:
        return True, None

def get_current_price(exchange, symbol):
    """Получение текущей цены"""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return float(ticker.get('last', 0)), None
    except Exception as e:
        return 0.0, str(e)

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
            time.sleep(1)
    return None, "Не удалось загрузить данные"

# ==================== ТОРГОВЫЕ ФУНКЦИИ ====================
def execute_futures_trade(exchange, symbol, action, current_price):
    """Исполнение фьючерсной сделки"""
    try:
        futures_symbol = symbol.replace('/', '') + ':USDT'
        
        # Получаем баланс
        usdt_balance, bal_error = get_futures_balance(exchange)
        if bal_error:
            return False, f"❌ Ошибка баланса: {bal_error}"
        
        # Сохраняем баланс
        if st.session_state['initial_balance'] == 0 and usdt_balance > 0:
            st.session_state['initial_balance'] = usdt_balance
        
        st.session_state['current_balance'] = usdt_balance
        
        if action == "BUY":
            # Проверка лимита позиций
            if len(st.session_state['open_positions']) >= st.session_state['max_positions']:
                return False, f"❌ Максимум позиций ({st.session_state['max_positions']})"
            
            # Проверка существующей позиции
            existing = [p for p in st.session_state['open_positions'] if p['symbol'] == symbol]
            if existing:
                return False, "❌ Уже есть позиция по этой паре"
            
            # Установка плеча и маржи
            leverage = st.session_state.get('leverage', 5)
            set_futures_leverage(exchange, symbol, leverage)
            set_margin_type(exchange, symbol, st.session_state.get('margin_type', 'isolated'))
            
            # Расчёт размера позиции
            risk_amount = usdt_balance * (st.session_state['risk_percent'] / 100)
            position_size_usdt = risk_amount * leverage
            
            if position_size_usdt < st.session_state['min_amount']:
                return False, f"❌ Сумма ${position_size_usdt:.2f} меньше минимума ${st.session_state['min_amount']}"
            
            # Количество контрактов
            amount = position_size_usdt / current_price
            market = exchange.market(futures_symbol)
            amount = exchange.amount_to_precision(futures_symbol, amount)
            
            # СОЗДАЁМ ОРДЕР
            order = exchange.create_market_buy_order(futures_symbol, amount)
            
            # Сохраняем позицию
            st.session_state['open_positions'].append({
                'id': len(st.session_state['open_positions']) + 1,
                'symbol': symbol,
                'type': 'LONG',
                'amount': float(amount),
                'entry_price': current_price,
                'leverage': leverage,
                'timestamp': datetime.now(),
                'order_id': order.get('id', 'N/A'),
            })
            
            return True, f"✅ LONG {amount} {symbol} @ ${current_price:.4f} | Плечо: {leverage}x"
            
        elif action == "SELL":
            positions_to_close = [p for p in st.session_state['open_positions'] if p['symbol'] == symbol]
            
            if not positions_to_close:
                # Открываем SHORT
                leverage = st.session_state.get('leverage', 5)
                set_futures_leverage(exchange, symbol, leverage)
                set_margin_type(exchange, symbol, st.session_state.get('margin_type', 'isolated'))
                
                risk_amount = usdt_balance * (st.session_state['risk_percent'] / 100)
                position_size_usdt = risk_amount * leverage
                amount = position_size_usdt / current_price
                
                market = exchange.market(futures_symbol)
                amount = exchange.amount_to_precision(futures_symbol, amount)
                
                order = exchange.create_market_sell_order(futures_symbol, amount)
                
                st.session_state['open_positions'].append({
                    'id': len(st.session_state['open_positions']) + 1,
                    'symbol': symbol,
                    'type': 'SHORT',
                    'amount': float(amount),
                    'entry_price': current_price,
                    'leverage': leverage,
                    'timestamp': datetime.now(),
                    'order_id': order.get('id', 'N/A'),
                })
                
                return True, f"✅ SHORT {amount} {symbol} @ ${current_price:.4f} | Плечо: {leverage}x"
            
            else:
                # Закрываем позиции
                total_profit = 0.0
                
                for pos in positions_to_close:
                    if pos['type'] == 'LONG':
                        profit = (current_price - pos['entry_price']) * pos['amount']
                        close_side = 'sell'
                    else:
                        profit = (pos['entry_price'] - current_price) * pos['amount']
                        close_side = 'buy'
                    
                    total_profit += profit
                    
                    close_amount = exchange.amount_to_precision(futures_symbol, pos['amount'])
                    
                    try:
                        if close_side == 'sell':
                            exchange.create_market_sell_order(futures_symbol, close_amount, {'reduceOnly': True})
                        else:
                            exchange.create_market_buy_order(futures_symbol, close_amount, {'reduceOnly': True})
                    except:
                        pass
                    
                    # В историю
                    st.session_state['closed_trades'].append({
                        'symbol': pos['symbol'],
                        'type': pos['type'],
                        'entry_price': pos['entry_price'],
                        'exit_price': current_price,
                        'amount': pos['amount'],
                        'profit': profit,
                        'close_timestamp': datetime.now(),
                    })
                    
                    st.session_state['open_positions'].remove(pos)
                
                st.session_state['total_profit'] += total_profit
                
                emoji = "📈" if total_profit > 0 else "📉"
                return True, f"✅ Закрыта {symbol} | P&L: ${total_profit:+,.2f} {emoji}"
        
        return False, "⏸️ HOLD"
        
    except Exception as e:
        return False, f"❌ Ошибка: {str(e)[:200]}"

# ==================== AI АНАЛИЗ ====================
def analyze_with_ai(market_data, symbol, timeframe, current_price, ai_token, model_name):
    """Анализ через RouterAI"""
    
    system_prompt = """Ты — профессиональный фьючерсный трейдер. Дай рекомендацию.

ПРАВИЛА:
1. BUY (LONG) — восходящий тренд
2. SELL (SHORT) — нисходящий тренд
3. HOLD — неопределённость
4. Уверенность < 0.6 — только HOLD

ОТВЕТЬ СТРОГО JSON:
{"action": "BUY"|"SELL"|"HOLD", "confidence": 0.0-1.0, "reason": "объяснение"}"""
    
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
        
        if 'choices' not in result:
            return None, "Нет ответа от модели"
        
        ai_reply = result['choices'][0]['message']['content']
        ai_reply = re.sub(r'```json|```', '', ai_reply).strip()
        json_match = re.search(r'\{[^{}]*\}', ai_reply, re.DOTALL)
        
        if json_match:
            parsed = json.loads(json_match.group(0))
            parsed['confidence'] = float(parsed.get('confidence', 0.5))
            parsed['action'] = parsed.get('action', 'HOLD')
            return parsed, None
        
        return None, "JSON не найден в ответе"
        
    except Exception as e:
        return None, str(e)

# ==================== АВТОТРЕЙДИНГ ====================
def auto_trading_loop():
    """Основной цикл автотрейдинга"""
    
    exchange, error = init_mexc_futures(
        st.session_state.get('api_key', ''),
        st.session_state.get('secret_key', '')
    )
    
    if error:
        st.session_state['trading_logs'].append(f"❌ Ошибка подключения: {error}")
        st.session_state['auto_trading_active'] = False
        return
    
    st.session_state['trading_logs'].append("🟢 Фьючерсный бот запущен")
    
    iteration = 0
    
    while st.session_state.get('auto_trading_active', False):
        try:
            iteration += 1
            symbol = st.session_state.get('last_symbol', 'BTC/USDT')
            timeframe = st.session_state.get('last_timeframe', '15m')
            
            # 1. Данные рынка
            ohlcv, err = fetch_ohlcv_safe(exchange, symbol, timeframe, limit=50)
            if err:
                if iteration % 10 == 0:
                    st.session_state['trading_logs'].append(f"❌ Данные: {err[:100]}")
                time.sleep(30)
                continue
            
            # 2. Обработка
            df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            current_price = float(df['c'].iloc[-1])
            
            st.session_state['current_price'] = current_price
            st.session_state['market_data'] = df.tail(15).to_json(orient="records")
            
            # 3. Баланс
            balance, _ = get_futures_balance(exchange)
            st.session_state['current_balance'] = balance
            
            # 4. AI анализ
            ai_token = st.session_state.get('ai_token', '')
            if ai_token:
                ai_res, ai_err = analyze_with_ai(
                    df.tail(15).to_json(orient="records"),
                    symbol, timeframe, current_price,
                    ai_token,
                    st.session_state.get('ai_model_name', 'deepseek/deepseek-v4-pro')
                )
                
                if ai_err:
                    if iteration % 10 == 0:
                        st.session_state['trading_logs'].append(f"⚠️ AI: {ai_err[:100]}")
                elif ai_res:
                    action = ai_res.get('action', 'HOLD')
                    confidence = float(ai_res.get('confidence', 0))
                    reason = ai_res.get('reason', '')
                    
                    threshold = st.session_state.get('confidence_threshold', 0.65)
                    
                    if confidence >= threshold and action in ['BUY', 'SELL']:
                        # ИСПОЛНЯЕМ СДЕЛКУ
                        success, msg = execute_futures_trade(exchange, symbol, action, current_price)
                        
                        log = f"[{datetime.now().strftime('%H:%M:%S')}] {action} | Уверенность: {confidence*100:.0f}% | {msg}"
                        st.session_state['trading_logs'].append(log)
                        
                        st.session_state['signals_history'].append({
                            'timestamp': datetime.now().strftime("%H:%M:%S"),
                            'action': action,
                            'symbol': symbol,
                            'price': current_price,
                            'confidence': confidence,
                            'reason': reason,
                        })
                        
                        time.sleep(60)
                    else:
                        if iteration % 20 == 0:
                            st.session_state['trading_logs'].append(
                                f"[{datetime.now().strftime('%H:%M:%S')}] HOLD | Уверенность: {confidence*100:.0f}% | {reason[:50]}"
                            )
                        time.sleep(30)
                else:
                    time.sleep(30)
            else:
                time.sleep(30)
                
        except Exception as e:
            st.session_state['trading_logs'].append(f"⚠️ Цикл: {str(e)[:150]}")
            time.sleep(30)
    
    st.session_state['trading_logs'].append("🔴 Бот остановлен")

# ==================== UI ====================
def main():
    st.markdown('<h1 class="main-header">🤖 MEXC Futures AI Bot</h1>', unsafe_allow_html=True)
    st.caption("Автотрейдинг фьючерсами с DeepSeek AI | Плечо • Лонг/Шорт • Изолированная маржа")
    
    # ========== САЙДБАР ==========
    with st.sidebar:
        st.header("🔑 API Ключи")
        api_key = st.text_input("MEXC API Key", type="password", key="api_key")
        secret_key = st.text_input("MEXC Secret Key", type="password", key="secret_key")
        ai_token = st.text_input("RouterAI API Key", type="password", key="ai_token")
        
        st.divider()
        
        st.header("⚙️ Настройки торговли")
        st.session_state['leverage'] = st.selectbox(
            "Плечо", 
            [1, 2, 3, 5, 10, 20], 
            index=3,
            help="Кредитное плечо для фьючерсов"
        )
        st.session_state['risk_percent'] = st.slider(
            "Риск на сделку (%)", 
            1.0, 100.0, 5.0, 1.0,
            help="Процент от депозита на одну сделку"
        )
        st.session_state['confidence_threshold'] = st.slider(
            "Мин. уверенность AI (%)", 
            50, 95, 65, 5,
            help="Сделка только при уверенности выше порога"
        ) / 100
        
        st.divider()
        
        st.header("📊 Статистика")
        
        is_active = st.session_state.get('auto_trading_active', False)
        st.metric("Статус", "🟢 Активен" if is_active else "🔴 Остановлен")
        
        # Баланс (безопасно)
        current_balance = st.session_state.get('current_balance')
        if current_balance is None:
            current_balance = 0.0
        st.metric("Баланс USDT", f"${current_balance:,.2f}")
        
        # P&L (безопасно)
        total_profit = st.session_state.get('total_profit')
        if total_profit is None:
            total_profit = 0.0
        st.metric("Общий P&L", f"${total_profit:+,.2f}")
        
        # Открытые позиции
        open_count = len(st.session_state.get('open_positions', []))
        st.metric("Открыто позиций", open_count)
    
    # ========== ВКЛАДКИ ==========
    tab1, tab2, tab3 = st.tabs(["📊 Терминал", "🤖 Автотрейдинг", "📈 История"])
    
    # ========== TAB 1: ТЕРМИНАЛ ==========
    with tab1:
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            symbol = st.selectbox(
                "Торговая пара",
                ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"],
                key="terminal_symbol"
            )
            st.session_state['last_symbol'] = symbol
        with col2:
            timeframe = st.selectbox(
                "Таймфрейм",
                ["1m", "5m", "15m", "30m", "1h", "4h"],
                index=2,
                key="terminal_timeframe"
            )
            st.session_state['last_timeframe'] = timeframe
        with col3:
            st.write("")
            st.write("")
            if st.button("🔄 Обновить", use_container_width=True):
                st.rerun()
        
        # Инициализация биржи
        exchange, err = init_mexc_futures(
            st.session_state.get('api_key', ''),
            st.session_state.get('secret_key', '')
        )
        
        if err:
            st.error(f"❌ Ошибка подключения к MEXC: {err}")
        elif exchange:
            # Загрузка данных
            ohlcv, data_err = fetch_ohlcv_safe(exchange, symbol, timeframe, 100)
            
            if data_err:
                st.error(f"❌ Ошибка данных: {data_err}")
            else:
                df = pd.DataFrame(ohlcv, columns=['t', 'o', 'h', 'l', 'c', 'v'])
                df['t'] = pd.to_datetime(df['t'], unit='ms')
                
                current_price = float(df['c'].iloc[-1])
                price_change = current_price - float(df['c'].iloc[-2])
                price_change_pct = (price_change / float(df['c'].iloc[-2])) * 100
                
                st.session_state['current_price'] = current_price
                st.session_state['market_data'] = df.tail(15).to_json(orient="records")
                
                # Метрики
                col1, col2, col3, col4, col5 = st.columns(5)
                with col1:
                    st.metric("Цена", f"${current_price:,.4f}", f"{price_change_pct:+.2f}%")
                with col2:
                    st.metric("24h High", f"${df['h'].max():,.4f}")
                with col3:
                    st.metric("24h Low", f"${df['l'].min():,.4f}")
                with col4:
                    st.metric("Объём", f"{df['v'].sum():,.0f}")
                with col5:
                    change_24h = ((current_price - float(df['c'].iloc[0])) / float(df['c'].iloc[0])) * 100
                    st.metric("За период", f"{change_24h:+.2f}%")
                
                # График
                fig = make_subplots(
                    rows=2, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.05,
                    row_heights=[0.7, 0.3]
                )
                
                fig.add_trace(
                    go.Candlestick(
                        x=df['t'],
                        open=df['o'],
                        high=df['h'],
                        low=df['l'],
                        close=df['c'],
                        name='Цена',
                        increasing_line_color='#00ff88',
                        decreasing_line_color='#ff4757'
                    ),
                    row=1, col=1
                )
                
                # Скользящие средние
                df['MA20'] = df['c'].rolling(20).mean()
                df['MA50'] = df['c'].rolling(50).mean()
                
                fig.add_trace(go.Scatter(x=df['t'], y=df['MA20'], name='MA20',
                                         line=dict(color='#ffa502', width=1)), row=1, col=1)
                fig.add_trace(go.Scatter(x=df['t'], y=df['MA50'], name='MA50',
                                         line=dict(color='#ff6b81', width=1)), row=1, col=1)
                
                # Объёмы
                colors = ['#00ff88' if c >= o else '#ff4757' for c, o in zip(df['c'], df['o'])]
                fig.add_trace(go.Bar(x=df['t'], y=df['v'], name='Объём',
                                     marker_color=colors, opacity=0.5), row=2, col=1)
                
                fig.update_layout(
                    template='plotly_dark',
                    height=600,
                    showlegend=True,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(l=0, r=0, t=40, b=0),
                    hovermode='x unified'
                )
                
                fig.update_xaxes(rangeslider_visible=False)
                fig.update_yaxes(title_text="Цена (USDT)", row=1, col=1)
                fig.update_yaxes(title_text="Объём", row=2, col=1)
                
                st.plotly_chart(fig, use_container_width=True)
    
    # ========== TAB 2: АВТОТРЕЙДИНГ ==========
    with tab2:
        col1, col2, col3 = st.columns(3)
        
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
                        
                        thread = threading.Thread(target=auto_trading_loop, daemon=True)
                        thread.start()
                        
                        st.success("🟢 Бот запущен!")
                        st.rerun()
            else:
                st.success("🟢 Бот активен")
        
        with col2:
            if st.session_state['auto_trading_active']:
                if st.button("⏸️ ОСТАНОВИТЬ", use_container_width=True):
                    st.session_state['auto_trading_active'] = False
                    st.warning("⏸️ Останавливаем...")
                    st.rerun()
            else:
                st.button("⏸️ ОСТАНОВИТЬ", disabled=True, use_container_width=True)
        
        with col3:
            if st.button("🗑️ Очистить логи", use_container_width=True):
                st.session_state['trading_logs'] = []
                st.rerun()
        
        st.divider()
        
        # Открытые позиции
        if st.session_state['open_positions']:
            st.subheader("📊 Открытые позиции")
            for pos in st.session_state['open_positions']:
                current_price = st.session_state.get('current_price', pos['entry_price'])
                
                if pos['type'] == 'LONG':
                    pnl = (current_price - pos['entry_price']) * pos['amount']
                else:
                    pnl = (pos['entry_price'] - current_price) * pos['amount']
                
                pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                if pos['type'] == 'SHORT':
                    pnl_pct = -pnl_pct
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric(f"{pos['symbol']} ({pos['type']})", f"{pos['amount']:.6f}")
                col2.metric("Вход", f"${pos['entry_price']:,.4f}")
                col3.metric("Текущая", f"${current_price:,.4f}")
                col4.metric("P&L", f"${pnl:+,.2f}", f"{pnl_pct:+.2f}%")
        
        # Логи
        st.subheader("📜 Логи автотрейдинга")
        
        logs = st.session_state.get('trading_logs', [])
        if logs:
            for log in reversed(logs[-30:]):
                if '✅' in log:
                    st.success(log)
                elif '❌' in log:
                    st.error(log)
                elif '🟢' in log or '🔴' in log:
                    st.info(log)
                else:
                    st.text(log)
        else:
            st.info("Логи появятся после запуска бота")
    
    # ========== TAB 3: ИСТОРИЯ ==========
    with tab3:
        st.subheader("📈 История сделок")
        
        closed_trades = st.session_state.get('closed_trades', [])
        
        if closed_trades:
            trades_df = pd.DataFrame(closed_trades)
            
            # Статистика
            total_trades = len(trades_df)
            winning = len(trades_df[trades_df['profit'] > 0])
            losing = len(trades_df[trades_df['profit'] < 0])
            win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
            
            total_profit = trades_df['profit'].sum()
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Всего сделок", total_trades)
            col2.metric("Прибыльных", winning)
            col3.metric("Убыточных", losing)
            col4.metric("Win Rate", f"{win_rate:.1f}%")
            
            col1, col2 = st.columns(2)
            col1.metric("Общий P&L", f"${total_profit:+,.2f}")
            col2.metric("Средний P&L", f"${total_profit/total_trades:+,.2f}" if total_trades > 0 else "$0.00")
            
            st.divider()
            
            # График P&L
            trades_df['cumulative_pnl'] = trades_df['profit'].cumsum()
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=trades_df['close_timestamp'],
                y=trades_df['cumulative_pnl'],
                mode='lines+markers',
                name='Кумулятивный P&L',
                line=dict(color='#00ff88', width=2),
                fill='tozeroy',
                fillcolor='rgba(0,255,136,0.1)'
            ))
            
            colors = ['#00ff88' if p > 0 else '#ff4757' for p in trades_df['profit']]
            fig.add_trace(go.Bar(
                x=trades_df['close_timestamp'],
                y=trades_df['profit'],
                name='P&L за сделку',
                marker_color=colors,
                opacity=0.7
            ))
            
            fig.update_layout(template='plotly_dark', height=400, hovermode='x unified')
            st.plotly_chart(fig, use_container_width=True)
            
            st.divider()
            
            # Таблица сделок
            display_df = trades_df[['close_timestamp', 'symbol', 'type', 'entry_price', 'exit_price', 'amount', 'profit']].copy()
            display_df.columns = ['Время', 'Пара', 'Тип', 'Вход', 'Выход', 'Объём', 'P&L']
            
            st.dataframe(
                display_df.style
                .format({
                    'Вход': '${:.4f}',
                    'Выход': '${:.4f}',
                    'Объём': '{:.6f}',
                    'P&L': '${:+.2f}'
                })
                .applymap(lambda x: 'color: #00ff88' if isinstance(x, (int, float)) and x > 0 
                         else 'color: #ff4757' if isinstance(x, (int, float)) and x < 0 else '',
                         subset=['P&L']),
                hide_index=True,
                use_container_width=True
            )
            
            # Экспорт
            csv = display_df.to_csv(index=False)
            st.download_button(
                "📥 Скачать CSV",
                csv,
                f"trades_{datetime.now().strftime('%Y%m%d')}.csv",
                "text/csv"
            )
        else:
            st.info("История сделок пуста. Запустите бота для торговли.")
    
    # Футер
    st.divider()
    st.caption(f"🤖 MEXC Futures AI Bot v2.0 | {datetime.now().strftime('%Y-%m-%d %H:%M')} | DeepSeek AI")

if __name__ == "__main__":
    main()