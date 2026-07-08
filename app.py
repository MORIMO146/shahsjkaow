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
    page_title="MEXC AI Trader PRO",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== СТИЛИ ====================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.5rem;
        font-weight: 700;
    }
    
    .metric-card {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 20px;
        backdrop-filter: blur(10px);
    }
    
    .profit-positive {
        color: #00ff88;
        font-weight: 700;
        font-size: 1.2rem;
    }
    
    .profit-negative {
        color: #ff4757;
        font-weight: 700;
        font-size: 1.2rem;
    }
    
    .trade-buy {
        background: linear-gradient(135deg, rgba(0, 255, 136, 0.1), rgba(0, 255, 136, 0.05));
        border-left: 4px solid #00ff88;
        padding: 12px;
        border-radius: 8px;
        margin: 8px 0;
    }
    
    .trade-sell {
        background: linear-gradient(135deg, rgba(255, 71, 87, 0.1), rgba(255, 71, 87, 0.05));
        border-left: 4px solid #ff4757;
        padding: 12px;
        border-radius: 8px;
        margin: 8px 0;
    }
    
    .trade-hold {
        background: rgba(255, 255, 255, 0.03);
        border-left: 4px solid #ffa502;
        padding: 12px;
        border-radius: 8px;
        margin: 8px 0;
    }
    
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.2);
    }
    
    div[data-testid="stMetricValue"] {
        font-size: 2rem;
        font-weight: 700;
    }
    
    .status-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.9rem;
    }
    
    .status-online {
        background: rgba(0, 255, 136, 0.2);
        color: #00ff88;
    }
    
    .status-offline {
        background: rgba(255, 71, 87, 0.2);
        color: #ff4757;
    }
</style>
""", unsafe_allow_html=True)

# ==================== ИНИЦИАЛИЗАЦИЯ СЕССИИ ====================
def init_session_state():
    """Инициализация всех переменных сессии"""
    defaults = {
        'signals_history': [],
        'market_data': None,
        'auto_trading_active': False,
        'trading_logs': [],
        'price_history': pd.DataFrame(columns=['timestamp', 'price']),
        'open_positions': [],
        'closed_trades': [],
        'total_profit': 0.0,
        'total_loss': 0.0,
        'initial_balance': None,
        'current_balance': None,
        'min_amount': 10.0,
        'last_symbol': 'BTC/USDT',
        'last_timeframe': '1h',
        'current_price': None,
        'ai_model_name': 'deepseek/deepseek-v4-pro',
        'risk_percent': 1.0,
        'confidence_threshold': 0.7,
        'max_positions': 3,
        'trailing_stop': False,
        'trailing_percent': 2.0,
        'auto_restart': False,
        'last_error': None,
        'consecutive_errors': 0
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# ==================== ФУНКЦИИ MEXC ====================
@st.cache_resource(ttl=300)
def init_mexc(api_key, secret_key):
    """Инициализация клиента MEXC"""
    try:
        exchange = ccxt.mexc({
            'apiKey': api_key or '',
            'secret': secret_key or '',
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'adjustForTimeDifference': True,
            },
            'timeout': 15000,
        })
        # Проверяем соединение
        exchange.load_markets()
        return exchange, None
    except Exception as e:
        return None, str(e)

def fetch_ohlcv_safe(exchange, symbol, timeframe='1h', limit=50):
    """Безопасное получение свечных данных с повторными попытками"""
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if ohlcv and len(ohlcv) > 0:
                return ohlcv, None
        except Exception as e:
            if attempt == max_retries - 1:
                return None, str(e)
            time.sleep(2 ** attempt)
    
    return None, "Не удалось получить данные"

def get_current_price_safe(exchange, symbol):
    """Безопасное получение текущей цены"""
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker.get('last', 0), None
    except Exception as e:
        return 0, str(e)

def get_balance_safe(exchange):
    """Безопасное получение баланса"""
    try:
        balance = exchange.fetch_balance()
        return balance, None
    except Exception as e:
        return {}, str(e)

def create_market_order_safe(exchange, symbol, side, amount):
    """Безопасное создание рыночного ордера"""
    try:
        if side == 'buy':
            order = exchange.create_market_buy_order(symbol, amount)
        else:
            order = exchange.create_market_sell_order(symbol, amount)
        return order, None
    except Exception as e:
        return None, str(e)

# ==================== ФУНКЦИИ AI ====================
def analyze_with_ai(market_data, symbol, timeframe, current_price, ai_token, model_name):
    """Анализ рынка через RouterAI"""
    
    system_prompt = """Ты — профессиональный крипто-трейдер с 10-летним опытом. 
Проанализируй предоставленные свечные данные и дай торговую рекомендацию.

ПРАВИЛА АНАЛИЗА:
1. Учитывай тренд, объёмы, уровни поддержки/сопротивления
2. BUY только при явном восходящем тренде
3. SELL только при явном нисходящем тренде
4. HOLD если рынок неопределённый
5. Уверенность ниже 0.6 — только HOLD

ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (без markdown):
{
    "action": "BUY" | "SELL" | "HOLD",
    "confidence": 0.0 до 1.0,
    "reason": "краткое объяснение на русском",
    "stop_loss": рекомендуемая цена стоп-лосса,
    "take_profit": рекомендуемая цена тейк-профита,
    "risk_reward": соотношение риск/прибыль
}"""
    
    user_content = f"""
Символ: {symbol}
Таймфрейм: {timeframe}
Текущая цена: ${current_price:,.4f}

Последние свечные данные:
{market_data}

Дай торговую рекомендацию на основе этих данных."""

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
                "max_tokens": 400,
                "top_p": 0.9
            },
            timeout=25
        )
        
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:200]}"
        
        result = response.json()
        
        if 'error' in result:
            return None, f"API Error: {result['error'].get('message', 'Unknown')}"
        
        if 'choices' not in result or not result['choices']:
            return None, "Пустой ответ от модели"
        
        ai_reply = result['choices'][0]['message']['content']
        
        # Очистка ответа
        ai_reply = re.sub(r'```json\s*|\s*```', '', ai_reply).strip()
        
        # Поиск JSON
        json_match = re.search(r'\{[^{}]*\}', ai_reply, re.DOTALL)
        if not json_match:
            return None, f"JSON не найден в ответе: {ai_reply[:200]}"
        
        parsed = json.loads(json_match.group(0))
        
        # Валидация полей
        required_fields = ['action', 'confidence']
        if not all(field in parsed for field in required_fields):
            return None, "Отсутствуют обязательные поля в ответе"
        
        if parsed['action'] not in ['BUY', 'SELL', 'HOLD']:
            parsed['action'] = 'HOLD'
        
        parsed['confidence'] = max(0.0, min(1.0, float(parsed.get('confidence', 0.5))))
        
        return parsed, None
        
    except requests.exceptions.Timeout:
        return None, "Таймаут запроса к RouterAI"
    except json.JSONDecodeError as e:
        return None, f"Ошибка парсинга JSON: {str(e)}"
    except Exception as e:
        return None, f"Неизвестная ошибка: {str(e)}"

# ==================== ТОРГОВЫЕ ФУНКЦИИ ====================
def calculate_position_size(balance, price, risk_percent, min_amount):
    """Расчёт размера позиции с учётом рисков"""
    available = balance * 0.95  # Оставляем 5% на комиссии
    
    position_size = available * (risk_percent / 100)
    
    if position_size < min_amount:
        return 0, "Недостаточно средств"
    
    amount = position_size / price
    
    return amount, None

def execute_trade(exchange, symbol, action, current_price, risk_percent):
    """Исполнение торговой операции"""
    try:
        # Получаем баланс
        balance, balance_error = get_balance_safe(exchange)
        if balance_error:
            return False, f"Ошибка баланса: {balance_error}"
        
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        
        # Сохраняем начальный баланс
        if st.session_state['initial_balance'] is None and usdt_balance > 0:
            st.session_state['initial_balance'] = usdt_balance
        
        st.session_state['current_balance'] = usdt_balance
        
        if action == "BUY":
            # Проверяем лимит позиций
            if len(st.session_state['open_positions']) >= st.session_state['max_positions']:
                return False, f"❌ Достигнут лимит позиций ({st.session_state['max_positions']})"
            
            # Проверяем, нет ли уже позиции по этой паре
            existing = [p for p in st.session_state['open_positions'] if p['symbol'] == symbol]
            if existing:
                return False, "❌ Уже есть открытая позиция по этой паре"
            
            # Рассчитываем размер
            amount, size_error = calculate_position_size(
                usdt_balance, current_price, risk_percent, st.session_state['min_amount']
            )
            
            if size_error:
                return False, f"❌ {size_error}"
            
            # Округляем
            try:
                amount_precise = exchange.amount_to_precision(symbol, amount)
            except:
                amount_precise = round(amount, 6)
            
            # Создаём ордер
            order, order_error = create_market_order_safe(exchange, symbol, 'buy', amount_precise)
            if order_error:
                return False, f"❌ Ошибка ордера: {order_error}"
            
            # Сохраняем позицию
            position = {
                'id': len(st.session_state['open_positions']) + 1,
                'symbol': symbol,
                'type': 'BUY',
                'amount': float(amount_precise),
                'entry_price': current_price,
                'current_price': current_price,
                'timestamp': datetime.now(),
                'order_id': order.get('id', 'N/A'),
                'stop_loss': None,
                'take_profit': None
            }
            
            st.session_state['open_positions'].append(position)
            
            return True, f"✅ BUY {amount_precise} {symbol.split('/')[0]} @ ${current_price:,.4f}"
            
        elif action == "SELL":
            base_currency = symbol.split('/')[0]
            total_base = 0
            
            # Собираем все позиции по этой паре
            positions_to_close = []
            for pos in st.session_state['open_positions']:
                if pos['symbol'] == symbol and pos['type'] == 'BUY':
                    total_base += pos['amount']
                    positions_to_close.append(pos)
            
            if total_base <= 0:
                return False, "❌ Нет открытых позиций для продажи"
            
            # Округляем
            try:
                sell_amount = exchange.amount_to_precision(symbol, total_base)
            except:
                sell_amount = round(total_base, 6)
            
            # Создаём ордер на продажу
            order, order_error = create_market_order_safe(exchange, symbol, 'sell', sell_amount)
            if order_error:
                return False, f"❌ Ошибка ордера: {order_error}"
            
            # Закрываем позиции и считаем прибыль
            total_profit = 0
            for pos in positions_to_close:
                profit = (current_price - pos['entry_price']) * pos['amount']
                total_profit += profit
                
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
            
            # Обновляем общую прибыль
            st.session_state['total_profit'] += total_profit
            if total_profit < 0:
                st.session_state['total_loss'] += abs(total_profit)
            
            profit_emoji = "📈" if total_profit > 0 else "📉" if total_profit < 0 else "➡️"
            
            return True, f"✅ SELL {sell_amount} {base_currency} @ ${current_price:,.4f} | P&L: ${total_profit:+.2f} {profit_emoji}"
        
        return False, "⏸️ HOLD"
        
    except Exception as e:
        return False, f"❌ Критическая ошибка: {str(e)[:200]}"

# ==================== АВТОТРЕЙДИНГ ====================
def auto_trading_loop():
    """Основной цикл автотрейдинга"""
    
    # Создаём отдельный клиент для потока
    exchange, init_error = init_mexc(
        st.session_state.get('api_key', ''),
        st.session_state.get('secret_key', '')
    )
    
    if init_error:
        st.session_state['trading_logs'].append(f"❌ Ошибка инициализации: {init_error}")
        st.session_state['auto_trading_active'] = False
        return
    
    st.session_state['trading_logs'].append("🟢 Автотрейдинг запущен")
    
    iteration = 0
    
    while st.session_state.get('auto_trading_active', False):
        try:
            iteration += 1
            
            symbol = st.session_state.get('last_symbol', 'BTC/USDT')
            timeframe = st.session_state.get('last_timeframe', '1h')
            ai_token = st.session_state.get('ai_token', '')
            model_name = st.session_state.get('ai_model_name', 'deepseek/deepseek-v4-pro')
            risk_percent = st.session_state.get('risk_percent', 1.0)
            confidence_threshold = st.session_state.get('confidence_threshold', 0.7)
            
            # 1. Получаем рыночные данные
            ohlcv, data_error = fetch_ohlcv_safe(exchange, symbol, timeframe, limit=50)
            
            if data_error:
                st.session_state['consecutive_errors'] += 1
                st.session_state['trading_logs'].append(f"❌ Ошибка данных (попытка {iteration}): {data_error}")
                
                if st.session_state['consecutive_errors'] > 10:
                    st.session_state['trading_logs'].append("🚨 Слишком много ошибок. Остановка.")
                    st.session_state['auto_trading_active'] = False
                    break
                
                time.sleep(30)
                continue
            
            st.session_state['consecutive_errors'] = 0
            
            # 2. Обрабатываем данные
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            current_price = df['close'].iloc[-1]
            
            # Обновляем состояние
            st.session_state['current_price'] = current_price
            st.session_state['market_data'] = df.tail(15).to_json(orient="records")
            
            # Обновляем историю цен для графика
            new_row = pd.DataFrame({
                'timestamp': [datetime.now()],
                'price': [current_price]
            })
            st.session_state['price_history'] = pd.concat(
                [st.session_state['price_history'], new_row],
                ignore_index=True
            ).tail(200)
            
            # 3. Проверяем трейлинг-стоп если активен
            if st.session_state.get('trailing_stop', False):
                for pos in st.session_state['open_positions']:
                    if pos['symbol'] == symbol:
                        # Обновляем стоп-лосс если цена выросла
                        if pos.get('stop_loss'):
                            new_stop = current_price * (1 - st.session_state['trailing_percent'] / 100)
                            if new_stop > pos['stop_loss']:
                                pos['stop_loss'] = new_stop
                                st.session_state['trading_logs'].append(
                                    f"🔒 Трейлинг-стоп обновлён: ${new_stop:,.4f}"
                                )
                        
                        # Проверяем срабатывание стоп-лосса
                        if pos.get('stop_loss') and current_price <= pos['stop_loss']:
                            st.session_state['trading_logs'].append("🛑 Сработал трейлинг-стоп!")
                            execute_trade(exchange, symbol, 'SELL', current_price, risk_percent)
            
            # 4. AI Анализ
            if ai_token:
                market_json = df.tail(15).to_json(orient="records")
                ai_result, ai_error = analyze_with_ai(
                    market_json, symbol, timeframe, current_price, ai_token, model_name
                )
                
                if ai_error:
                    st.session_state['trading_logs'].append(f"⚠️ AI ошибка: {ai_error}")
                elif ai_result:
                    action = ai_result.get('action', 'HOLD')
                    confidence = ai_result.get('confidence', 0)
                    reason = ai_result.get('reason', 'Нет объяснения')
                    
                    log_prefix = f"[{datetime.now().strftime('%H:%M:%S')}] Итерация #{iteration}"
                    
                    if confidence >= confidence_threshold and action != 'HOLD':
                        # Исполняем сделку
                        success, trade_msg = execute_trade(
                            exchange, symbol, action, current_price, risk_percent
                        )
                        
                        log_msg = f"{log_prefix} | {action} | Уверенность: {confidence*100:.1f}% | {trade_msg}"
                        st.session_state['trading_logs'].append(log_msg)
                        
                        # Сохраняем в историю сигналов
                        signal_record = {
                            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            'action': action,
                            'symbol': symbol,
                            'price': current_price,
                            'confidence': confidence,
                            'reason': reason,
                            'result': trade_msg
                        }
                        st.session_state['signals_history'].append(signal_record)
                        
                        # Устанавливаем стоп-лосс если указан
                        if success and action == 'BUY' and ai_result.get('stop_loss'):
                            for pos in st.session_state['open_positions']:
                                if pos['symbol'] == symbol and pos['type'] == 'BUY':
                                    pos['stop_loss'] = ai_result['stop_loss']
                                    pos['take_profit'] = ai_result.get('take_profit')
                        
                        time.sleep(90)  # Пауза после сделки
                    else:
                        if iteration % 5 == 0:  # Логируем каждый 5-й раз
                            log_msg = f"{log_prefix} | HOLD | Уверенность: {confidence*100:.1f}% | {reason}"
                            st.session_state['trading_logs'].append(log_msg)
                        
                        time.sleep(30)
                else:
                    time.sleep(30)
            else:
                time.sleep(30)
                
        except Exception as e:
            st.session_state['consecutive_errors'] += 1
            error_msg = f"⚠️ Ошибка цикла #{iteration}: {str(e)[:200]}"
            st.session_state['trading_logs'].append(error_msg)
            st.session_state['last_error'] = error_msg
            
            time.sleep(30)
    
    st.session_state['trading_logs'].append("🔴 Автотрейдинг остановлен")

# ==================== UI КОМПОНЕНТЫ ====================
def render_sidebar():
    """Рендеринг боковой панели"""
    with st.sidebar:
        st.markdown("## 🔑 API Ключи")
        
        api_key = st.text_input(
            "MEXC API Key",
            type="password",
            key="api_key",
            help="Ключ из личного кабинета MEXC"
        )
        secret_key = st.text_input(
            "MEXC Secret Key",
            type="password",
            key="secret_key",
            help="Секретный ключ из личного кабинета MEXC"
        )
        ai_token = st.text_input(
            "RouterAI API Key",
            type="password",
            key="ai_token",
            help="Ключ из личного кабинета routerai.ru"
        )
        
        st.divider()
        
        st.markdown("## 📊 Статус системы")
        
        # Статус автотрейдинга
        is_active = st.session_state['auto_trading_active']
        status_class = "status-online" if is_active else "status-offline"
        status_text = "🟢 Активен" if is_active else "🔴 Остановлен"
        st.markdown(f'<span class="status-badge {status_class}">{status_text}</span>', unsafe_allow_html=True)
        
        # Основные метрики
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Сделок", len(st.session_state['closed_trades']))
        with col2:
            st.metric("Позиций", len(st.session_state['open_positions']))
        
        # Прибыль
        total_profit = st.session_state['total_profit']
        profit_color = "profit-positive" if total_profit > 0 else "profit-negative" if total_profit < 0 else ""
        st.markdown(f'<p class="{profit_color}">P&L: ${total_profit:+,.2f}</p>', unsafe_allow_html=True)
        
        # Баланс если доступен
        if st.session_state.get('initial_balance') and st.session_state.get('current_balance'):
            initial = st.session_state['initial_balance']
            current = st.session_state['current_balance']
            roi = ((current - initial) / initial) * 100 if initial > 0 else 0
            st.metric("ROI", f"{roi:+.2f}%")
        
        st.divider()
        
        st.markdown("## ℹ️ Информация")
        st.info("""
        **MEXC AI Trader PRO** использует DeepSeek AI для автоматической торговли.
        
        ⚠️ Торговля криптовалютами связана с высоким риском.
        """)

def render_market_tab():
    """Вкладка с данными рынка"""
    st.markdown("## 📊 Рыночные данные")
    
    # Выбор инструментов
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    with col1:
        symbol = st.selectbox(
            "Торговая пара",
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT"],
            key="market_symbol"
        )
    with col2:
        timeframe = st.selectbox(
            "Таймфрейм",
            ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
            index=4,
            key="market_timeframe"
        )
    with col3:
        st.write("")
        st.write("")
        auto_refresh = st.checkbox("Автообновление", value=True)
    with col4:
        st.write("")
        st.write("")
        if st.button("🔄 Обновить", use_container_width=True):
            st.rerun()
    
    # Инициализация MEXC
    exchange, error = init_mexc(
        st.session_state.get('api_key', ''),
        st.session_state.get('secret_key', '')
    )
    
    if error:
        st.error(f"❌ Ошибка подключения к MEXC: {error}")
        return
    
    # Получение данных
    with st.spinner("Загрузка данных..."):
        ohlcv, data_error = fetch_ohlcv_safe(exchange, symbol, timeframe, limit=100)
    
    if data_error:
        st.error(f"❌ Ошибка загрузки данных: {data_error}")
        return
    
    # Обработка данных
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    current_price = df['close'].iloc[-1]
    price_change = df['close'].iloc[-1] - df['close'].iloc[-2]
    price_change_pct = (price_change / df['close'].iloc[-2]) * 100
    
    # Обновляем стейт
    st.session_state['current_price'] = current_price
    st.session_state['last_symbol'] = symbol
    st.session_state['last_timeframe'] = timeframe
    st.session_state['market_data'] = df.tail(15).to_json(orient="records")
    
    # Метрики
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric(
            "Цена",
            f"${current_price:,.4f}",
            f"{price_change_pct:+.2f}%"
        )
    with col2:
        st.metric("24h High", f"${df['high'].max():,.4f}")
    with col3:
        st.metric("24h Low", f"${df['low'].min():,.4f}")
    with col4:
        volume_24h = df['volume'].sum()
        st.metric("Объём 24h", f"${volume_24h:,.0f}")
    with col5:
        # Изменение за период
        period_change = ((df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0]) * 100
        st.metric(f"За период", f"{period_change:+.2f}%")
    
    # График
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=(f'{symbol} - {timeframe}', 'Объём')
    )
    
    # Свечи
    fig.add_trace(
        go.Candlestick(
            x=df['timestamp'],
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='Price',
            increasing_line_color='#00ff88',
            decreasing_line_color='#ff4757'
        ),
        row=1, col=1
    )
    
    # Скользящие средние
    df['MA20'] = df['close'].rolling(window=20).mean()
    df['MA50'] = df['close'].rolling(window=50).mean()
    
    fig.add_trace(
        go.Scatter(x=df['timestamp'], y=df['MA20'], name='MA20',
                   line=dict(color='#ffa502', width=1)),
        row=1, col=1
    )
    fig.add_trace(
        go.Scatter(x=df['timestamp'], y=df['MA50'], name='MA50',
                   line=dict(color='#ff6b81', width=1)),
        row=1, col=1
    )
    
    # Объёмы
    colors = ['#00ff88' if c >= o else '#ff4757' for c, o in zip(df['close'], df['open'])]
    fig.add_trace(
        go.Bar(x=df['timestamp'], y=df['volume'], name='Volume',
               marker_color=colors, opacity=0.5),
        row=2, col=1
    )
    
    # Настройки графика
    fig.update_layout(
        template='plotly_dark',
        height=600,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        margin=dict(l=0, r=0, t=40, b=0),
        hovermode='x unified'
    )
    
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(title_text="Price (USDT)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Дополнительная информация
    with st.expander("📋 Детальная статистика"):
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Статистика цен:**")
            stats_df = pd.DataFrame({
                'Показатель': ['Open', 'High', 'Low', 'Close', 'Средняя', 'Волатильность'],
                'Значение': [
                    f"${df['open'].iloc[-1]:,.4f}",
                    f"${df['high'].max():,.4f}",
                    f"${df['low'].min():,.4f}",
                    f"${df['close'].iloc[-1]:,.4f}",
                    f"${df['close'].mean():,.4f}",
                    f"{df['close'].std():,.4f}"
                ]
            })
            st.dataframe(stats_df, hide_index=True, use_container_width=True)
        
        with col2:
            st.write("**Последние свечи:**")
            st.dataframe(
                df.tail(10)[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                .style.format({
                    'open': '${:.4f}',
                    'high': '${:.4f}',
                    'low': '${:.4f}',
                    'close': '${:.4f}',
                    'volume': '{:.0f}'
                }),
                hide_index=True,
                use_container_width=True
            )

def render_trading_tab():
    """Вкладка автотрейдинга"""
    st.markdown("## 🤖 Автотрейдинг")
    
    # Статус и управление
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if not st.session_state['auto_trading_active']:
            if st.button("▶️ Запустить", type="primary", use_container_width=True):
                if not st.session_state.get('api_key') or not st.session_state.get('secret_key'):
                    st.error("❌ Введите API ключи MEXC в боковой панели!")
                elif not st.session_state.get('ai_token'):
                    st.error("❌ Введите RouterAI API Key в боковой панели!")
                else:
                    st.session_state['auto_trading_active'] = True
                    st.session_state['trading_logs'] = []
                    
                    thread = threading.Thread(target=auto_trading_loop, daemon=True)
                    thread.start()
                    
                    st.success("🟢 Автотрейдинг запущен!")
                    st.rerun()
        else:
            st.success("🟢 Активен", icon="✅")
    
    with col2:
        if st.session_state['auto_trading_active']:
            if st.button("⏸️ Остановить", use_container_width=True):
                st.session_state['auto_trading_active'] = False
                st.warning("⏸️ Останавливаем...")
                st.rerun()
        else:
            st.button("⏸️ Остановить", disabled=True, use_container_width=True)
    
    with col3:
        if st.button("🔄 Сбросить логи", use_container_width=True):
            st.session_state['trading_logs'] = []
            st.rerun()
    
    with col4:
        if st.button("🧹 Очистить всё", use_container_width=True):
            st.session_state['signals_history'] = []
            st.session_state['trading_logs'] = []
            st.session_state['open_positions'] = []
            st.session_state['closed_trades'] = []
            st.session_state['total_profit'] = 0.0
            st.session_state['initial_balance'] = None
            st.rerun()
    
    # Открытые позиции
    if st.session_state['open_positions']:
        st.divider()
        st.markdown("### 📊 Открытые позиции")
        
        for pos in st.session_state['open_positions']:
            current_price = st.session_state.get('current_price', pos['entry_price'])
            pnl = (current_price - pos['entry_price']) * pos['amount']
            pnl_percent = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
            
            col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
            
            with col1:
                st.write(f"**{pos['symbol']}**")
                st.caption(f"Открыта: {pos['timestamp'].strftime('%H:%M:%S')}")
            
            with col2:
                st.metric("Размер", f"{pos['amount']:.6f}")
            
            with col3:
                st.metric("Вход", f"${pos['entry_price']:,.4f}")
            
            with col4:
                st.metric("Текущая", f"${current_price:,.4f}")
            
            with col5:
                pnl_color = "normal" if pnl == 0 else "off"
                st.metric(
                    "P&L",
                    f"${pnl:+,.2f}",
                    f"{pnl_percent:+.2f}%"
                )
    
    # Логи
    st.divider()
    st.markdown("### 📜 Логи")
    
    if st.session_state['trading_logs']:
        logs_container = st.container()
        with logs_container:
            for log in reversed(st.session_state['trading_logs'][-30:]):
                if '✅' in log:
                    st.success(log)
                elif '❌' in log or '🚨' in log:
                    st.error(log)
                elif '🛑' in log or '🔒' in log:
                    st.warning(log)
                elif '🟢' in log:
                    st.info(log)
                elif '🔴' in log:
                    st.warning(log)
                else:
                    st.text(log)
    else:
        st.info("Логи появятся после запуска автотрейдинга")

def render_history_tab():
    """Вкладка с историей сделок"""
    st.markdown("## 📈 История торговли")
    
    # Статистика
    col1, col2, col3, col4, col5 = st.columns(5)
    
    total_closed = len(st.session_state['closed_trades'])
    winning_trades = len([t for t in st.session_state['closed_trades'] if t.get('profit', 0) > 0])
    losing_trades = len([t for t in st.session_state['closed_trades'] if t.get('profit', 0) < 0])
    win_rate = (winning_trades / total_closed * 100) if total_closed > 0 else 0
    
    total_profit = sum(t.get('profit', 0) for t in st.session_state['closed_trades'] if t.get('profit', 0) > 0)
    total_loss = sum(abs(t.get('profit', 0)) for t in st.session_state['closed_trades'] if t.get('profit', 0) < 0)
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    
    with col1:
        st.metric("Всего сделок", total_closed)
    with col2:
        st.metric("Прибыльных", winning_trades)
    with col3:
        st.metric("Убыточных", losing_trades)
    with col4:
        st.metric("Win Rate", f"{win_rate:.1f}%")
    with col5:
        st.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor != float('inf') else "∞")
    
    # График P&L
    if st.session_state['closed_trades']:
        st.divider()
        st.markdown("### 📊 График прибыли")
        
        trades_df = pd.DataFrame(st.session_state['closed_trades'])
        trades_df['close_timestamp'] = pd.to_datetime(trades_df['close_timestamp'])
        trades_df = trades_df.sort_values('close_timestamp')
        trades_df['cumulative_pnl'] = trades_df['profit'].cumsum()
        
        fig = go.Figure()
        
        # Кумулятивная прибыль
        fig.add_trace(go.Scatter(
            x=trades_df['close_timestamp'],
            y=trades_df['cumulative_pnl'],
            mode='lines+markers',
            name='Кумулятивная P&L',
            line=dict(color='#00ff88', width=2),
            fill='tozeroy',
            fillcolor='rgba(0, 255, 136, 0.1)'
        ))
        
        # Отдельные сделки
        colors = ['#00ff88' if p > 0 else '#ff4757' for p in trades_df['profit']]
        fig.add_trace(go.Bar(
            x=trades_df['close_timestamp'],
            y=trades_df['profit'],
            name='P&L за сделку',
            marker_color=colors,
            opacity=0.7
        ))
        
        fig.update_layout(
            template='plotly_dark',
            height=400,
            hovermode='x unified',
            showlegend=True,
            margin=dict(l=0, r=0, t=20, b=0)
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Таблица сделок
        st.divider()
        st.markdown("### 📋 Все сделки")
        
        display_df = trades_df[[
            'close_timestamp', 'symbol', 'type', 'entry_price',
            'exit_price', 'amount', 'profit', 'profit_percent'
        ]].copy()
        
        display_df.columns = [
            'Время', 'Пара', 'Тип', 'Вход', 'Выход', 'Объём', 'P&L', 'P&L %'
        ]
        
        st.dataframe(
            display_df.style
            .format({
                'Вход': '${:.4f}',
                'Выход': '${:.4f}',
                'Объём': '{:.6f}',
                'P&L': '${:+.2f}',
                'P&L %': '{:+.2f}%'
            })
            .applymap(
                lambda x: 'color: #00ff88' if isinstance(x, (int, float)) and x > 0 
                else 'color: #ff4757' if isinstance(x, (int, float)) and x < 0 
                else '',
                subset=['P&L', 'P&L %']
            ),
            hide_index=True,
            use_container_width=True
        )
        
        # Экспорт
        csv = display_df.to_csv(index=False)
        st.download_button(
            "📥 Скачать историю (CSV)",
            csv,
            f"trades_{datetime.now().strftime('%Y%m%d')}.csv",
            "text/csv"
        )
    else:
        st.info("История сделок появится после начала торговли")
    
    # Сигналы AI
    if st.session_state['signals_history']:
        st.divider()
        st.markdown("### 🧠 История сигналов AI")
        
        signals_df = pd.DataFrame(st.session_state['signals_history'])
        st.dataframe(
            signals_df[['timestamp', 'action', 'symbol', 'price', 'confidence', 'reason']],
            hide_index=True,
            use_container_width=True
        )

def render_settings_tab():
    """Вкладка с настройками"""
    st.markdown("## ⚙️ Настройки")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🧠 AI Настройки")
        
        ai_choice = st.selectbox(
            "Модель AI",
            ["DeepSeek V4 Pro", "DeepSeek Chat", "DeepSeek Coder"],
            help="Выберите модель для анализа"
        )
        
        model_map = {
            "DeepSeek V4 Pro": "deepseek/deepseek-v4-pro",
            "DeepSeek Chat": "deepseek/deepseek-chat",
            "DeepSeek Coder": "deepseek/deepseek-coder"
        }
        st.session_state['ai_model_name'] = model_map[ai_choice]
        
        confidence_threshold = st.slider(
            "Минимальная уверенность (%)",
            min_value=50,
            max_value=95,
            value=int(st.session_state['confidence_threshold'] * 100),
            step=5,
            help="Сделка открывается только при уверенности AI выше этого порога"
        )
        st.session_state['confidence_threshold'] = confidence_threshold / 100
    
    with col2:
        st.markdown("### 💰 Управление рисками")
        
        risk_percent = st.slider(
            "Риск на сделку (% от баланса)",
            min_value=0.1,
            max_value=10.0,
            value=st.session_state['risk_percent'],
            step=0.1,
            help="Процент от USDT баланса на одну сделку"
        )
        st.session_state['risk_percent'] = risk_percent
        
        min_amount = st.number_input(
            "Минимальная сумма сделки (USDT)",
            min_value=5.0,
            value=st.session_state['min_amount'],
            step=5.0,
            help="Минимальный размер позиции в USDT"
        )
        st.session_state['min_amount'] = min_amount
        
        max_positions = st.number_input(
            "Максимум открытых позиций",
            min_value=1,
            max_value=10,
            value=st.session_state['max_positions'],
            step=1,
            help="Максимальное количество одновременных позиций"
        )
        st.session_state['max_positions'] = max_positions
    
    st.divider()
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🛡️ Защитные механизмы")
        
        trailing_stop = st.checkbox(
            "Трейлинг-стоп",
            value=st.session_state['trailing_stop'],
            help="Автоматически двигает стоп-лосс за ценой"
        )
        st.session_state['trailing_stop'] = trailing_stop
        
        if trailing_stop:
            trailing_percent = st.slider(
                "Отступ трейлинг-стопа (%)",
                min_value=0.5,
                max_value=10.0,
                value=st.session_state['trailing_percent'],
                step=0.5
            )
            st.session_state['trailing_percent'] = trailing_percent
    
    with col2:
        st.markdown("### 📊 Информация")
        
        st.info(f"""
        **Текущая конфигурация:**
        
        • Модель AI: **{ai_choice}**
        • Мин. уверенность: **{confidence_threshold}%**
        • Риск на сделку: **{risk_percent}%**
        • Мин. сумма: **${min_amount}**
        • Макс. позиций: **{max_positions}**
        • Трейлинг-стоп: **{'Вкл' if trailing_stop else 'Выкл'}**
        """)
    
    st.divider()
    st.markdown("### 🧪 Тестирование")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🔍 Проверить MEXC", use_container_width=True):
            with st.spinner("Проверка..."):
                exchange, error = init_mexc(
                    st.session_state.get('api_key', ''),
                    st.session_state.get('secret_key', '')
                )
                if error:
                    st.error(f"❌ {error}")
                else:
                    price, _ = get_current_price_safe(exchange, 'BTC/USDT')
                    st.success(f"✅ MEXC работает! BTC = ${price:,.2f}")
    
    with col2:
        if st.button("🧠 Проверить RouterAI", use_container_width=True):
            if not st.session_state.get('ai_token'):
                st.error("❌ Введите API ключ")
            else:
                with st.spinner("Проверка..."):
                    try:
                        response = requests.post(
                            "https://routerai.ru/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {st.session_state['ai_token']}"},
                            json={
                                "model": "deepseek/deepseek-chat",
                                "messages": [{"role": "user", "content": "Ответь OK"}],
                                "max_tokens": 10
                            },
                            timeout=10
                        )
                        if response.status_code == 200:
                            st.success("✅ RouterAI работает!")
                        else:
                            st.error(f"❌ HTTP {response.status_code}")
                    except Exception as e:
                        st.error(f"❌ {str(e)[:100]}")
    
    with col3:
        if st.button("📊 Проверить данные", use_container_width=True):
            with st.spinner("Загрузка..."):
                exchange, _ = init_mexc('', '')
                ohlcv, error = fetch_ohlcv_safe(exchange, 'BTC/USDT', '1h', 5)
                if error:
                    st.error(f"❌ {error}")
                else:
                    st.success(f"✅ Данные получены! {len(ohlcv)} свечей")

# ==================== ГЛАВНЫЙ UI ====================
def main():
    """Главная функция"""
    
    # Рендерим сайдбар
    render_sidebar()
    
    # Заголовок
    st.markdown('<h1 class="main-header">🤖 MEXC AI Trader PRO</h1>', unsafe_allow_html=True)
    st.caption("Автоматическая торговля криптовалютами с искусственным интеллектом DeepSeek")
    
    # Вкладки
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Рынок",
        "🤖 Автотрейдинг",
        "📈 История",
        "⚙️ Настройки"
    ])
    
    with tab1:
        render_market_tab()
    
    with tab2:
        render_trading_tab()
    
    with tab3:
        render_history_tab()
    
    with tab4:
        render_settings_tab()
    
    # Футер
    st.divider()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption("Powered by DeepSeek AI")
    with col2:
        st.caption(f"Session: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    with col3:
        st.caption("MEXC AI Trader PRO v2.0")

if __name__ == "__main__":
    main()