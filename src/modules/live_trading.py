import os
import sys
import time
import json
import hmac
import base64
import hashlib
import logging
import threading
import requests
import pandas as pd
import numpy as np
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Any, Optional, Union, Tuple, Callable
from datetime import datetime, timedelta
from enum import Enum
import ccxt
from dotenv import load_dotenv

# Logging-Konfiguration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/live_trading.log"),
        logging.StreamHandler()
    ]
)

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LIMIT = "stop_limit"

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

class OrderStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    PENDING = "pending"

class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"

class LiveTradingConnector:
    """
    Verbindung zu Bitget für den Echtzeit-Handel.
    Implementiert API-Aufrufe, Order-Management und Fehlerbehandlung.
    """
    def __init__(self, config: Dict[str, Any]):
        self.logger = logging.getLogger("LiveTradingConnector")
        self.logger.info("Initialisiere Live Trading Connector...")
        self.api_key = config.get('api_key', os.getenv('BITGET_API_KEY', ''))
        self.api_secret = config.get('api_secret', os.getenv('BITGET_API_SECRET', ''))
        self.api_passphrase = config.get('api_passphrase', os.getenv('BITGET_API_PASSPHRASE', ''))
        if not self.api_key or not self.api_secret or not self.api_passphrase:
            self.logger.error("Bitget API-Schlüssel fehlen.")
            self.is_ready = False
        else:
            self.is_ready = True
        self.sandbox_mode = config.get('sandbox_mode', True)
        self.default_leverage = config.get('default_leverage', 1)
        self.default_margin_mode = config.get('margin_mode', 'cross')
        self.max_open_orders = config.get('max_open_orders', 50)
        self.max_retry_attempts = config.get('max_retry_attempts', 3)
        self.retry_delay = config.get('retry_delay', 2)
        self.max_position_size = config.get('max_position_size', 0.1)
        self.max_leverage = config.get('max_leverage', 5)
        self.default_stop_loss_pct = config.get('default_stop_loss_pct', 0.05)
        self.default_take_profit_pct = config.get('default_take_profit_pct', 0.1)
        self.is_trading_active = False
        self.error_count = 0
        self.last_error_time = None
        self.consecutive_errors = 0
        self.exchange_status = "disconnected"
        self.symbol_info_cache = {}
        self.ticker_cache = {}
        self.orderbook_cache = {}
        self.last_cache_update = datetime.now() - timedelta(hours=1)
        self.cache_ttl = config.get('cache_ttl', 60)
        self.rate_limits = {
            "orders": {"limit": 20, "remaining": 20, "reset_time": datetime.now()},
            "market_data": {"limit": 50, "remaining": 50, "reset_time": datetime.now()}
        }
        self.order_update_callbacks = []
        self.position_update_callbacks = []
        self.error_callbacks = []
        self._initialize_exchange()
        self.background_threads = {}
        self.logger.info("Live Trading Connector erfolgreich initialisiert")
    
    def _initialize_exchange(self):
        try:
            self.exchange = ccxt.bitget({
                'apiKey': self.api_key,
                'secret': self.api_secret,
                'password': self.api_passphrase,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',
                    'adjustForTimeDifference': True,
                    'recvWindow': 10000,
                }
            })
            if self.sandbox_mode:
                self.exchange.set_sandbox_mode(True)
                self.logger.warning("SANDBOX-MODUS AKTIV! Es werden keine echten Trades durchgeführt.")
            if self.is_ready:
                self.exchange.load_markets()
                self.logger.info(f"Verbindung zu Bitget hergestellt. {len(self.exchange.markets)} Märkte verfügbar.")
                self.exchange_status = "connected"
        except Exception as e:
            self.logger.error(f"Fehler bei der Exchange-Initialisierung: {str(e)}")
            self.exchange_status = "error"
            self.is_ready = False
    
    def _handle_rate_limit(self, rate_limit_type: str):
        rate_limit = self.rate_limits.get(rate_limit_type)
        if not rate_limit:
            return
        now = datetime.now()
        if now >= rate_limit['reset_time']:
            rate_limit['remaining'] = rate_limit['limit']
            rate_limit['reset_time'] = now + timedelta(seconds=1)
        if rate_limit['remaining'] <= 0:
            sleep_time = (rate_limit['reset_time'] - now).total_seconds()
            if sleep_time > 0:
                self.logger.debug(f"Rate-Limit für {rate_limit_type} erreicht, warte {sleep_time:.2f}s")
                time.sleep(sleep_time)
                rate_limit['remaining'] = rate_limit['limit']
                rate_limit['reset_time'] = datetime.now() + timedelta(seconds=1)
        rate_limit['remaining'] -= 1

    def _execute_with_retry(self, func, *args, rate_limit_type='market_data', **kwargs):
        attempts = 0
        while attempts < self.max_retry_attempts:
            try:
                self._handle_rate_limit(rate_limit_type)
                result = func(*args, **kwargs)
                self.consecutive_errors = 0
                return result
            except ccxt.NetworkError as e:
                attempts += 1
                self.consecutive_errors += 1
                self.last_error_time = datetime.now()
                self.logger.warning(f"Netzwerkfehler (Versuch {attempts}/{self.max_retry_attempts}): {str(e)}")
                if attempts < self.max_retry_attempts:
                    time.sleep(self.retry_delay * (2 ** (attempts - 1)))
                else:
                    self._handle_error(e, "Maximale Wiederholungsversuche erreicht")
                    return None
            except ccxt.ExchangeError as e:
                self.consecutive_errors += 1
                self.last_error_time = datetime.now()
                self._handle_error(e, "Exchange-Fehler")
                return None
            except Exception as e:
                self.consecutive_errors += 1
                self.last_error_time = datetime.now()
                self._handle_error(e, "Unerwarteter Fehler")
                return None
        return None

    def _handle_error(self, exception: Exception, context: str):
        self.error_count += 1
        error_msg = f"{context}: {str(exception)}"
        self.logger.error(error_msg)
        if self.consecutive_errors >= 5:
            self.logger.critical(f"Zu viele Fehler ({self.consecutive_errors}). Trading wird deaktiviert.")
            self.is_trading_active = False
        for callback in self.error_callbacks:
            try:
                callback({
                    'timestamp': datetime.now().isoformat(),
                    'message': error_msg,
                    'context': context,
                    'consecutive_errors': self.consecutive_errors
                })
            except Exception as callback_error:
                self.logger.error(f"Fehler im Error-Callback: {str(callback_error)}")

    def start_trading(self, mode: Optional[str] = None):
        """
        Startet den Trading-Prozess. Optional kann ein Trading-Modus (z. B. 'live' oder 'paper') übergeben werden.
        """
        if not self.is_ready:
            self.logger.error("Connector nicht bereit – Trading kann nicht gestartet werden.")
            return False
        self.is_trading_active = True
        if mode:
            self.logger.info(f"Trading-Modus: {mode}")
        else:
            self.logger.info("Kein spezieller Modus angegeben, Standardmodus wird verwendet.")
        self._start_background_tasks()
        self.logger.info("Live Trading aktiviert")
        return True

    def stop_trading(self):
        self.is_trading_active = False
        self._stop_background_tasks()
        self.logger.info("Live Trading deaktiviert")
        return True

    def _start_background_tasks(self):
        self.background_threads['account_monitor'] = threading.Thread(target=self._account_monitor_loop, daemon=True)
        self.background_threads['orderbook_cache'] = threading.Thread(target=self._orderbook_cache_loop, daemon=True)
        self.background_threads['order_monitor'] = threading.Thread(target=self._order_monitor_loop, daemon=True)
        for name, thread in self.background_threads.items():
            thread.start()
            self.logger.debug(f"Hintergrund-Thread '{name}' gestartet")

    def _stop_background_tasks(self):
        self.background_threads = {}
        self.logger.debug("Alle Hintergrund-Threads gestoppt")

    def _account_monitor_loop(self):
        while self.is_trading_active:
            try:
                self.get_account_balance()
                self.get_open_positions()
                time.sleep(5)
            except Exception as e:
                self.logger.error(f"Fehler im Account-Monitor: {str(e)}")
                time.sleep(10)

    def _orderbook_cache_loop(self):
        common_symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT']
        while self.is_trading_active:
            try:
                for symbol in common_symbols:
                    orderbook = self._execute_with_retry(self.exchange.fetch_order_book, symbol, limit=20)
                    if orderbook:
                        self.orderbook_cache[symbol] = {'data': orderbook, 'timestamp': datetime.now()}
                time.sleep(2)
            except Exception as e:
                self.logger.error(f"Fehler im Orderbook-Cache: {str(e)}")
                time.sleep(5)

    def _order_monitor_loop(self):
        while self.is_trading_active:
            try:
                open_orders = self.get_open_orders()
                if open_orders:
                    self.logger.debug(f"{len(open_orders)} offene Orders")
                time.sleep(5)
            except Exception as e:
                self.logger.error(f"Fehler im Order-Monitor: {str(e)}")
                time.sleep(10)

    def get_exchange_info(self):
        return self._execute_with_retry(self.exchange.fetch_markets)

    def get_symbol_info(self, symbol: str):
        if symbol in self.symbol_info_cache:
            return self.symbol_info_cache[symbol]
        if not self.exchange.markets:
            self.exchange.load_markets()
        info = self.exchange.market(symbol)
        self.symbol_info_cache[symbol] = info
        return info

    def get_ticker(self, symbol: str):
        return self._execute_with_retry(self.exchange.fetch_ticker, symbol)

    def get_orderbook(self, symbol: str, limit: int = 20):
        now = datetime.now()
        if (symbol in self.orderbook_cache and 
            (now - self.orderbook_cache[symbol]['timestamp']).total_seconds() < self.cache_ttl):
            return self.orderbook_cache[symbol]['data']
        orderbook = self._execute_with_retry(self.exchange.fetch_order_book, symbol, limit=limit)
        if orderbook:
            self.orderbook_cache[symbol] = {'data': orderbook, 'timestamp': now}
        return orderbook

    def get_account_balance(self):
        balance = self._execute_with_retry(self.exchange.fetch_balance, {'type': 'swap'}, rate_limit_type='orders')
        if balance:
            total = balance.get('total', {}).get('USDT', 0)
            free = balance.get('free', {}).get('USDT', 0)
            self.logger.debug(f"Kontostand: {total} USDT (frei: {free} USDT)")
        return balance

    def get_open_positions(self):
        positions = self._execute_with_retry(self.exchange.fetch_positions, None, rate_limit_type='orders')
        if positions:
            active = [p for p in positions if float(p['contracts']) > 0]
            if active:
                self.logger.debug(f"{len(active)} aktive Positionen")
            return active
        return []

    def get_open_orders(self, symbol: Optional[str] = None):
        if symbol:
            return self._execute_with_retry(self.exchange.fetch_open_orders, symbol, rate_limit_type='orders')
        else:
            return self._execute_with_retry(self.exchange.fetch_open_orders, None, rate_limit_type='orders')

    def create_market_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False, params: Dict = None):
        if not self.is_trading_active:
            self.logger.warning("Trading deaktiviert. Market-Order nicht erstellt.")
            return None
        if not params:
            params = {}
        if reduce_only:
            params['reduceOnly'] = True
        order_desc = f"{side.upper()} {amount} {symbol} zum Marktpreis"
        self.logger.info(f"Erstelle Market-Order: {order_desc}")
        try:
            order = self._execute_with_retry(self.exchange.create_market_order, symbol, side, amount, None, params, rate_limit_type='orders')
            if order:
                self.logger.info(f"Market-Order erstellt: {order['id']}")
                for cb in self.live_trading.order_update_callbacks:
                    try:
                        cb(order)
                    except Exception as e:
                        self.logger.error(f"Fehler im Order-Update-Callback: {str(e)}")
            return order
        except Exception as e:
            self._handle_error(e, f"Fehler bei Market-Order: {order_desc}")
            return None

    # Weitere Order-Methoden würden hier folgen ...

    def register_error_callback(self, callback: Callable[[Dict[str, Any]], None]):
        self.live_trading.register_error_callback(callback)

    def register_order_update_callback(self, callback: Callable[[Dict[str, Any]], None]):
        self.live_trading.register_order_update_callback(callback)

    def register_position_update_callback(self, callback: Callable[[Dict[str, Any]], None]):
        self.live_trading.register_position_update_callback(callback)

    def close_all_positions(self):
        try:
            self.logger.info("Schließe alle Positionen...")
            positions = self.get_open_positions()
            results = []
            for pos in positions:
                symbol = pos['symbol']
                side = 'sell' if pos['side'].lower() == 'long' else 'buy'
                amount = pos['contracts']
                result = self.create_market_order(symbol, side, amount, reduce_only=True)
                results.append(result)
            return results
        except Exception as e:
            self._handle_error(e, "Fehler beim Schließen aller Positionen")
            return None

    def _add_event(self, event_type: str, title: str, data: Dict[str, Any]):
        event = {
            "type": event_type,
            "title": title,
            "data": data,
            "timestamp": datetime.datetime.now().isoformat()
        }
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events.pop(0)

    def _send_notification(self, title: str, message: str, priority: str = "normal"):
        now = datetime.datetime.now()
        last_time = self.last_notification_time.get(priority, now - timedelta(seconds=self.notification_cooldown + 1))
        if (now - last_time).total_seconds() < self.notification_cooldown:
            self.logger.debug(f"Notification für {priority} im Cooldown.")
            return
        self.last_notification_time[priority] = now
        text = f"[{priority.upper()}] {title}\n{message}"
        self._send_notification_to_all(text)

    def _send_notification_to_all(self, text: str):
        for uid in self.allowed_users:
            try:
                self._send_message(int(uid), text)
            except Exception as e:
                self.logger.error(f"Fehler beim Senden der Notification an {uid}: {str(e)}")

    def _send_message(self, chat_id: int, text: str, reply_markup: Optional[Dict] = None):
        try:
            url = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_BOT_TOKEN')}/sendMessage"
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            response = requests.post(url, data=payload)
            response.raise_for_status()
            self.logger.debug(f"Nachricht an {chat_id} gesendet: {text}")
        except Exception as e:
            self.logger.error(f"Fehler beim Senden der Nachricht an {chat_id}: {str(e)}")

    def _send_main_menu(self, chat_id: int):
        reply_markup = {
            "inline_keyboard": [
                [{"text": "Start", "callback_data": "start_bot"}, {"text": "Stop", "callback_data": "stop_bot"}],
                [{"text": "Kontostand", "callback_data": "balance"}, {"text": "Positionen", "callback_data": "positions"}],
                [{"text": "Performance", "callback_data": "performance"}, {"text": "Report", "callback_data": "report"}],
                [{"text": "Notfall Stop", "callback_data": "confirm_emergency_stop"}]
            ]
        }
        self._send_message(chat_id, "Hauptmenü:", reply_markup)

    def process_callback_update(self, update: Dict[str, Any]):
        self._handle_callback_query(update)

    def _handle_callback_query(self, update: Dict[str, Any]):
        try:
            callback_query = update.get("callback_query", {})
            data = callback_query.get("data", "")
            chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
            if data == "ssh_start":
                self.logger.info("SSH Start Button gedrückt.")
                self._handle_ssh_start({"chat_id": chat_id, "user_id": "callback"})
            elif data == "confirm_emergency_stop":
                self.logger.critical("Notfall-Stop bestätigt über Button.")
                try:
                    self.emergency_stop()
                    self._send_message(chat_id, "Notfall-Stop wurde ausgeführt. Alle Aktivitäten wurden sofort gestoppt.")
                except Exception as e:
                    self._send_message(chat_id, f"Fehler beim Notfall-Stop: {str(e)}")
            elif data == "menu":
                self._send_main_menu(chat_id)
            else:
                self.logger.debug(f"Unbekannter Callback: {data}")
        except Exception as e:
            self.logger.error(f"Fehler in Callback-Verarbeitung: {str(e)}")
