# black_swan_detector.py

import os
import logging
import threading
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union, Tuple, Callable
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# Konfiguration des Loggings
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/black_swan_detector.log"),
        logging.StreamHandler()
    ]
)

class BlackSwanDetector:
    """
    Überwacht Marktdaten auf Anzeichen von außergewöhnlichen Ereignissen ("Black Swans")
    und ergreift entsprechende Notfallmaßnahmen, wenn notwendig.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialisiert den Black Swan Detector.
        
        Args:
            config: Konfigurationseinstellungen für die Erkennung von Black Swan Events
        """
        self.logger = logging.getLogger("BlackSwanDetector")
        self.logger.info("Initialisiere BlackSwanDetector...")
        
        # Schwellenwerte aus Konfiguration laden
        self.volatility_threshold = config.get('volatility_threshold', 3.5)
        self.volume_threshold = config.get('volume_threshold', 5.0)
        self.correlation_threshold = config.get('correlation_threshold', 0.85)
        self.news_sentiment_threshold = config.get('news_sentiment_threshold', -0.6)
        self.check_interval = config.get('check_interval', 300)  # Sekunden
        
        # Liste der zu überwachenden Assets
        self.watch_list = config.get('watch_list', [
            'BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT', 'SOL/USDT:USDT'
        ])
        
        # Zusätzliche Parameter
        self.historical_lookback_days = config.get('historical_lookback_days', 365)
        self.volatility_window = config.get('volatility_window', 20)
        self.volume_window = config.get('volume_window', 20)
        self.max_alerts_per_day = config.get('max_alerts_per_day', 3)
        self.alert_cooldown_minutes = config.get('alert_cooldown_minutes', 60)
        
        # Status und Steuerung
        self.is_monitoring = False
        self.monitor_thread = None
        self.last_check_time = None
        self.last_alert_time = None
        self.alert_count_today = 0
        self.reset_date = datetime.now().date()
        
        # Data Pipeline Referenz (wird später gesetzt)
        self.data_pipeline = None
        
        # Notification Callbacks
        self.notification_callbacks = []
        
        # Event-Speicher
        self.detected_events = []
        self.max_event_history = 100
        
        # Ausgabeverzeichnis für Plots und Berichte
        self.output_dir = Path('data/black_swan')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Historische Daten für Referenzwerte
        self.historical_data = {}
        self.historical_stats = {}
        
        self.logger.info("BlackSwanDetector erfolgreich initialisiert")
    
    def set_data_pipeline(self, data_pipeline):
        """
        Setzt die Datenpipeline für den Zugriff auf Marktdaten.
        
        Args:
            data_pipeline: Referenz zur Datenpipeline
        """
        self.data_pipeline = data_pipeline
        self.logger.info("Datenpipeline erfolgreich verbunden")
    
    def register_notification_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Registriert eine Callback-Funktion für Black Swan Notifications.
        
        Args:
            callback: Funktion, die bei Erkennung eines Black Swan Events aufgerufen wird
        """
        self.notification_callbacks.append(callback)
        self.logger.info("Notification Callback erfolgreich registriert")
    
    def start_monitoring(self):
        """Startet die kontinuierliche Überwachung auf Black Swan Events."""
        if self.is_monitoring:
            self.logger.warning("Überwachung läuft bereits")
            return False
        
        self.is_monitoring = True
        
        # Lade historische Referenzdaten
        self._load_historical_reference_data()
        
        # Starte Monitoring-Thread
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        
        self.logger.info("Black Swan Monitoring gestartet")
        return True
    
    def stop_monitoring(self):
        """Stoppt die kontinuierliche Überwachung."""
        if not self.is_monitoring:
            self.logger.warning("Überwachung läuft nicht")
            return False
        
        self.is_monitoring = False
        
        # Warten, bis der Thread beendet ist
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=10)
        
        self.logger.info("Black Swan Monitoring gestoppt")
        return True
    
    def _monitoring_loop(self):
        """Hauptschleife für die kontinuierliche Überwachung."""
        self.logger.info("Monitoring-Schleife gestartet")
        
        while self.is_monitoring:
            try:
                # Reset Alert-Zähler um Mitternacht
                current_date = datetime.now().date()
                if current_date > self.reset_date:
                    self.alert_count_today = 0
                    self.reset_date = current_date
                
                # Marktdaten überprüfen
                self._check_market_conditions()
                
                # Zeit des letzten Checks aktualisieren
                self.last_check_time = datetime.now()
                
                # Warten bis zum nächsten Check
                time.sleep(self.check_interval)
                
            except Exception as e:
                self.logger.error(f"Fehler in der Monitoring-Schleife: {str(e)}")
                time.sleep(60)  # Längere Pause bei Fehlern
        
        self.logger.info("Monitoring-Schleife beendet")
    
    def _load_historical_reference_data(self):
        """Lädt historische Daten für Referenzstatistiken."""
        if not self.data_pipeline:
            self.logger.error("Keine Datenpipeline verfügbar, kann keine historischen Daten laden")
            return
        
        self.logger.info("Lade historische Referenzdaten...")
        
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=self.historical_lookback_days)).strftime('%Y-%m-%d')
            
            for symbol in self.watch_list:
                # Vereinfache das Symbol für die Datenpipeline (entferne ":USDT" für Futures)
                base_symbol = symbol.split(':')[0] if ':' in symbol else symbol
                
                # Historische Daten abrufen
                historical_data = self.data_pipeline.fetch_crypto_data(
                    base_symbol, start_date, end_date, interval='1d'
                )
                
                if historical_data is not None and not historical_data.empty:
                    self.historical_data[symbol] = historical_data
                    
                    # Berechne historische Statistiken
                    self.historical_stats[symbol] = {
                        'mean_returns': historical_data['close'].pct_change().mean(),
                        'std_returns': historical_data['close'].pct_change().std(),
                        'mean_volume': historical_data['volume'].mean(),
                        'std_volume': historical_data['volume'].std(),
                        'percentiles': {
                            'returns': {
                                '1%': historical_data['close'].pct_change().quantile(0.01),
                                '5%': historical_data['close'].pct_change().quantile(0.05),
                                '95%': historical_data['close'].pct_change().quantile(0.95),
                                '99%': historical_data['close'].pct_change().quantile(0.99)
                            },
                            'volume': {
                                '95%': historical_data['volume'].quantile(0.95),
                                '99%': historical_data['volume'].quantile(0.99)
                            }
                        }
                    }
                    
                    self.logger.info(f"Historische Daten für {symbol} geladen: {len(historical_data)} Datenpunkte")
                else:
                    self.logger.warning(f"Keine historischen Daten für {symbol} verfügbar")
            
            # Berechne Asset-Korrelationen
            if len(self.historical_data) > 1:
                self._calculate_correlations()
            
        except Exception as e:
            self.logger.error(f"Fehler beim Laden historischer Daten: {str(e)}")
    
    def _calculate_correlations(self):
        """Berechnet die Korrelationen zwischen den überwachten Assets."""
        try:
            # DataFrame für die Schlusskurse aller Assets erstellen
            price_data = {}
            
            for symbol, data in self.historical_data.items():
                # Symbol-Name für den DataFrame bereinigen
                clean_symbol = symbol.replace('/', '_').replace(':', '_')
                price_data[clean_symbol] = data['close']
            
            # DataFrame erstellen und NaN-Werte entfernen
            price_df = pd.DataFrame(price_data)
            price_df = price_df.dropna()
            
            # Tägliche Returns berechnen
            returns_df = price_df.pct_change().dropna()
            
            # Korrelationsmatrix berechnen
            self.correlation_matrix = returns_df.corr()
            
            # Korrelationsplot erstellen und speichern
            self._save_correlation_plot()
            
            self.logger.info("Asset-Korrelationen erfolgreich berechnet")
            
        except Exception as e:
            self.logger.error(f"Fehler bei der Berechnung der Asset-Korrelationen: {str(e)}")
    
    def _save_correlation_plot(self):
        """Erstellt und speichert einen Korrelationsplot."""
        try:
            plt.figure(figsize=(10, 8))
            sns.heatmap(
                self.correlation_matrix, 
                annot=True, 
                cmap='coolwarm', 
                vmin=-1, 
                vmax=1,
                linewidths=0.5
            )
            plt.title('Asset-Korrelationen (Tägliche Returns)')
            plt.tight_layout()
            
            # Plot speichern
            plot_path = self.output_dir / f"correlation_matrix_{datetime.now().strftime('%Y%m%d')}.png"
            plt.savefig(plot_path)
            plt.close()
            
            self.logger.info(f"Korrelationsmatrix-Plot gespeichert unter {plot_path}")
            
        except Exception as e:
            self.logger.error(f"Fehler beim Erstellen des Korrelationsplots: {str(e)}")
    
    def _check_market_conditions(self):
        """Überprüft aktuelle Marktbedingungen auf Anzeichen von Black Swan Events."""
        if not self.data_pipeline:
            self.logger.error("Keine Datenpipeline verfügbar, kann Marktbedingungen nicht überprüfen")
            return
        
        self.logger.debug("Überprüfe aktuelle Marktbedingungen...")
        
        alerts = []
        
        try:
            # Daten für alle überwachten Assets abrufen
            current_data = {}
            
            for symbol in self.watch_list:
                # Vereinfache das Symbol für die Datenpipeline
                base_symbol = symbol.split(':')[0] if ':' in symbol else symbol
                
                # Aktuelle Daten abrufen (letzte 30 Datenpunkte)
                data = self.data_pipeline.get_crypto_data(base_symbol, timeframe='1h', limit=30)
                
                if data is not None and not data.empty:
                    current_data[symbol] = data
                else:
                    self.logger.warning(f"Keine aktuellen Daten für {symbol} verfügbar")
            
            # Überprüfung der verschiedenen Indikatoren
            volatility_alerts = self._check_volatility(current_data)
            volume_alerts = self._check_volume(current_data)
            correlation_alerts = self._check_correlation_breakdown(current_data)
            
            # Alle Alerts sammeln
            alerts.extend(volatility_alerts)
            alerts.extend(volume_alerts)
            alerts.extend(correlation_alerts)
            
            # Wenn Alerts vorhanden sind, sende Benachrichtigungen
            if alerts:
                self._handle_alerts(alerts)
                
        except Exception as e:
            self.logger.error(f"Fehler bei der Überprüfung der Marktbedingungen: {str(e)}")
    
    def _check_volatility(self, current_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Überprüft die aktuelle Volatilität auf Anzeichen von Black Swan Events.
        
        Args:
            current_data: Dictionary mit aktuellen Marktdaten pro Symbol
            
        Returns:
            Liste von Volatilitäts-Alerts
        """
        alerts = []
        
        for symbol, data in current_data.items():
            try:
                # Berechne aktuelle und historische Volatilität
                current_returns = data['close'].pct_change().dropna()
                current_volatility = current_returns.std() * np.sqrt(24)  # Annualisierte Volatilität (24h)
                
                if symbol in self.historical_stats:
                    historical_volatility = self.historical_stats[symbol]['std_returns'] * np.sqrt(252)  # Annualisierte Volatilität (252 Handelstage)
                    volatility_ratio = current_volatility / historical_volatility
                    
                    # Überprüfe, ob die aktuelle Volatilität den Schwellenwert überschreitet
                    if volatility_ratio > self.volatility_threshold:
                        alert = {
                            'type': 'volatility',
                            'symbol': symbol,
                            'timestamp': datetime.now().isoformat(),
                            'severity': self._calculate_severity('volatility', volatility_ratio),
                            'details': {
                                'current_volatility': current_volatility,
                                'historical_volatility': historical_volatility,
                                'volatility_ratio': volatility_ratio,
                                'threshold': self.volatility_threshold
                            }
                        }
                        
                        alerts.append(alert)
                        self.logger.warning(
                            f"Hohe Volatilität erkannt für {symbol}: "
                            f"Ratio = {volatility_ratio:.2f}x (Schwelle: {self.volatility_threshold}x)"
                        )
                
            except Exception as e:
                self.logger.error(f"Fehler bei der Volatilitätsprüfung für {symbol}: {str(e)}")
        
        return alerts
    
    def _check_volume(self, current_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Überprüft das aktuelle Handelsvolumen auf Anzeichen von Black Swan Events.
        
        Args:
            current_data: Dictionary mit aktuellen Marktdaten pro Symbol
            
        Returns:
            Liste von Volumen-Alerts
        """
        alerts = []
        
        for symbol, data in current_data.items():
            try:
                # Berechne aktuelles und historisches Volumen
                current_volume = data['volume'].iloc[-1]
                
                if symbol in self.historical_stats:
                    historical_avg_volume = self.historical_stats[symbol]['mean_volume']
                    volume_ratio = current_volume / historical_avg_volume
                    
                    # Überprüfe, ob das aktuelle Volumen den Schwellenwert überschreitet
                    if volume_ratio > self.volume_threshold:
                        alert = {
                            'type': 'volume',
                            'symbol': symbol,
                            'timestamp': datetime.now().isoformat(),
                            'severity': self._calculate_severity('volume', volume_ratio),
                            'details': {
                                'current_volume': current_volume,
                                'historical_avg_volume': historical_avg_volume,
                                'volume_ratio': volume_ratio,
                                'threshold': self.volume_threshold
                            }
                        }
                        
                        alerts.append(alert)
                        self.logger.warning(
                            f"Hohes Volumen erkannt für {symbol}: "
                            f"Ratio = {volume_ratio:.2f}x (Schwelle: {self.volume_threshold}x)"
                        )
                
            except Exception as e:
                self.logger.error(f"Fehler bei der Volumenprüfung für {symbol}: {str(e)}")
        
        return alerts
    
    def _check_correlation_breakdown(self, current_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Überprüft Korrelationsänderungen auf Anzeichen von Marktkrisen.
        
        Args:
            current_data: Dictionary mit aktuellen Marktdaten pro Symbol
            
        Returns:
            Liste von Korrelations-Alerts
        """
        if len(current_data) < 2 or not hasattr(self, 'correlation_matrix'):
            return []
        
        alerts = []
        
        try:
            # DataFrame für die aktuellen Schlusskurse aller Assets erstellen
            price_data = {}
            
            for symbol, data in current_data.items():
                # Symbol-Name für den DataFrame bereinigen
                clean_symbol = symbol.replace('/', '_').replace(':', '_')
                price_data[clean_symbol] = data['close']
            
            # DataFrame erstellen
            price_df = pd.DataFrame(price_data)
            
            # Stündliche Returns berechnen
            returns_df = price_df.pct_change().dropna()
            
            # Aktuelle Korrelationsmatrix berechnen
            current_corr = returns_df.corr()
            
            # Überprüfe auf signifikante Änderungen in der Korrelationsstruktur
            if hasattr(self, 'correlation_matrix'):
                # Mittlere absolute Korrelationsänderung berechnen
                correlation_change = np.abs(current_corr - self.correlation_matrix).mean().mean()
                
                # Überprüfe, ob die Korrelationsänderung den Schwellenwert überschreitet
                if correlation_change > 1 - self.correlation_threshold:
                    alert = {
                        'type': 'correlation',
                        'timestamp': datetime.now().isoformat(),
                        'severity': self._calculate_severity('correlation', correlation_change),
                        'details': {
                            'correlation_change': correlation_change,
                            'threshold': 1 - self.correlation_threshold
                        }
                    }
                    
                    alerts.append(alert)
                    self.logger.warning(
                        f"Signifikante Korrelationsänderung erkannt: "
                        f"Änderung = {correlation_change:.2f} (Schwelle: {1 - self.correlation_threshold:.2f})"
                    )
                    
                    # Speichere aktuelle Korrelationsmatrix für Analyse
                    self._save_correlation_breakdown_plot(current_corr)
        
        except Exception as e:
            self.logger.error(f"Fehler bei der Korrelationsanalyse: {str(e)}")
        
        return alerts
    
    def _save_correlation_breakdown_plot(self, current_corr: pd.DataFrame):
        """
        Erstellt und speichert einen Vergleichsplot der Korrelationsmatrizen.
        
        Args:
            current_corr: Aktuelle Korrelationsmatrix
        """
        try:
            fig, axes = plt.subplots(1, 2, figsize=(18, 8))
            
            # Historische Korrelation (links)
            sns.heatmap(
                self.correlation_matrix, 
                annot=True, 
                cmap='coolwarm', 
                vmin=-1, 
                vmax=1,
                linewidths=0.5,
                ax=axes[0]
            )
            axes[0].set_title('Historische Korrelation')
            
            # Aktuelle Korrelation (rechts)
            sns.heatmap(
                current_corr, 
                annot=True, 
                cmap='coolwarm', 
                vmin=-1, 
                vmax=1,
                linewidths=0.5,
                ax=axes[1]
            )
            axes[1].set_title('Aktuelle Korrelation')
            
            plt.tight_layout()
            
            # Plot speichern
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            plot_path = self.output_dir / f"correlation_breakdown_{timestamp}.png"
            plt.savefig(plot_path)
            plt.close()
            
            self.logger.info(f"Korrelationsvergleich gespeichert unter {plot_path}")
            
        except Exception as e:
            self.logger.error(f"Fehler beim Erstellen des Korrelationsvergleichs: {str(e)}")
    
    def _calculate_severity(self, alert_type: str, value: float) -> float:
        """
        Berechnet den Schweregrad eines Alerts.
        
        Args:
            alert_type: Typ des Alerts ('volatility', 'volume', 'correlation', etc.)
            value: Gemessener Wert
            
        Returns:
            Schweregrad zwischen 0 und 1
        """
        severity = 0.0
        
        if alert_type == 'volatility':
            # Formel: (ratio - threshold) / (2 * threshold)
            severity = min(1.0, max(0.0, (value - self.volatility_threshold) / (2 * self.volatility_threshold)))
        
        elif alert_type == 'volume':
            # Formel: (ratio - threshold) / (3 * threshold)
            severity = min(1.0, max(0.0, (value - self.volume_threshold) / (3 * self.volume_threshold)))
        
        elif alert_type == 'correlation':
            # Formel: (change - threshold) / (1 - threshold)
            threshold = 1 - self.correlation_threshold
            severity = min(1.0, max(0.0, (value - threshold) / (1 - threshold)))
        
        return severity
    
    def _handle_alerts(self, alerts: List[Dict[str, Any]]):
        """
        Verarbeitet erkannte Alerts und ergreift entsprechende Maßnahmen.
        
        Args:
            alerts: Liste von erkannten Alerts
        """
        # Prüfen, ob wir das tägliche Alert-Limit erreicht haben
        if self.alert_count_today >= self.max_alerts_per_day:
            self.logger.warning(f"Tägliches Alert-Limit erreicht ({self.max_alerts_per_day}), keine weiteren Alerts werden gesendet")
            return
        
        # Prüfen, ob die Alert-Abklingzeit noch aktiv ist
        if self.last_alert_time:
            cooldown_end = self.last_alert_time + timedelta(minutes=self.alert_cooldown_minutes)
            if datetime.now() < cooldown_end:
                self.logger.info(f"Alert-Abklingzeit aktiv, keine weiteren Alerts bis {cooldown_end.strftime('%H:%M:%S')}")
                return
        
        # Bestimme den höchsten Schweregrad aller Alerts
        max_severity = max([alert.get('severity', 0) for alert in alerts], default=0)
        
        # Erstelle einen zusammengefassten Alert
        combined_alert = {
            'title': 'Black Swan Event erkannt',
            'message': f"Ungewöhnliche Marktaktivität mit Schweregrad {max_severity:.2f} erkannt",
            'severity': max_severity,
            'timestamp': datetime.now().isoformat(),
            'details': alerts
        }
        
        # Füge Alert zur Historie hinzu
        self.detected_events.append(combined_alert)
        
        # Begrenze die Größe der Event-Historie
        if len(self.detected_events) > self.max_event_history:
            self.detected_events = self.detected_events[-self.max_event_history:]
        
        # Aktualisiere Zähler und Zeitstempel
        self.last_alert_time = datetime.now()
        self.alert_count_today += 1
        
        # Meldung ins Log schreiben
        alert_symbols = ', '.join(set([alert.get('symbol', 'unknown') for alert in alerts if 'symbol' in alert]))
        self.logger.warning(
            f"BLACK SWAN EVENT erkannt! Schweregrad: {max_severity:.2f}, "
            f"Betroffene Assets: {alert_symbols}"
        )
        
        # Callbacks aufrufen
        for callback in self.notification_callbacks:
            try:
                callback(combined_alert)
            except Exception as e:
                self.logger.error(f"Fehler beim Aufrufen des Notification-Callbacks: {str(e)}")
    
    def manual_check(self, symbol: str) -> Dict[str, Any]:
        """
        Führt eine manuelle Überprüfung für ein bestimmtes Symbol durch.
        
        Args:
            symbol: Trading-Symbol (z.B. 'BTC/USDT')
            
        Returns:
            Ergebnis der Überprüfung
        """
        if not self.data_pipeline:
            return {'status': 'error', 'message': 'Keine Datenpipeline verfügbar'}
        
        try:
            self.logger.info(f"Führe manuelle Überprüfung für {symbol} durch...")
            
            # Aktuelle Daten abrufen
            base_symbol = symbol.split(':')[0] if ':' in symbol else symbol
            data = self.data_pipeline.get_crypto_data(base_symbol, timeframe='1h', limit=30)
            
            if data is None or data.empty:
                return {'status': 'error', 'message': f'Keine Daten für {symbol} verfügbar'}
            
            # Erstelle Dictionary mit nur diesem Symbol
            current_data = {symbol: data}
            
            # Checks durchführen
            volatility_alerts = self._check_volatility(current_data)
            volume_alerts = self._check_volume(current_data)
            
            # Alle Alerts sammeln
            alerts = []
            alerts.extend(volatility_alerts)
            alerts.extend(volume_alerts)
            
            # Berechne aktuellen Preis, 24h-Change, ATR, etc.
            current_price = data['close'].iloc[-1]
            price_change_24h = (data['close'].iloc[-1] / data['close'].iloc[-24] - 1) * 100 if len(data) >= 24 else None
            
            # ATR (Average True Range) berechnen
            high_low = data['high'] - data['low']
            high_close = (data['high'] - data['close'].shift()).abs()
            low_close = (data['low'] - data['close'].shift()).abs()
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            atr = true_range.rolling(window=14).mean().iloc[-1]
            
            # Volatilität berechnen
            volatility = data['close'].pct_change().std() * np.sqrt(24)  # 24h annualisiert
            
            # Ergebnis zusammenstellen
            result = {
                'status': 'success',
                'symbol': symbol,
                'current_price': current_price,
                'price_change_24h': price_change_24h,
                'atr': atr,
                'volatility': volatility,
                'alerts': alerts,
                'alert_count': len(alerts),
                'timestamp': datetime.now().isoformat()
            }
            
            # Wenn alerts vorhanden sind, aber keine Benachrichtigung gesendet wurde
            # (wegen Cooldown oder Max-Limit), füge dies zum Ergebnis hinzu
            if alerts and (
                self.alert_count_today >= self.max_alerts_per_day or 
                (self.last_alert_time and datetime.now() < self.last_alert_time + timedelta(minutes=self.alert_cooldown_minutes))
            ):
                result['notification_suppressed'] = True
                result['suppression_reason'] = (
                    'daily_limit' if self.alert_count_today >= self.max_alerts_per_day else 'cooldown'
                )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Fehler bei der manuellen Überprüfung für {symbol}: {str(e)}")
            return {'status': 'error', 'message': f'Fehler: {str(e)}'}
    
    def get_event_history(self) -> List[Dict[str, Any]]:
        """
        Gibt die Historie der erkannten Black Swan Events zurück.
        
        Returns:
            Liste der erkannten Events
        """
        return self.detected_events
    
    def get_current_status(self) -> Dict[str, Any]:
        """
        Gibt den aktuellen Status des Black Swan Detectors zurück.
        
        Returns:
            Status-Dictionary
        """
        status = {
            'is_monitoring': self.is_monitoring,
            'last_check_time': self.last_check_time.isoformat() if self.last_check_time else None,
            'last_alert_time': self.last_alert_time.isoformat() if self.last_alert_time else None,
            'alert_count_today': self.alert_count_today,
            'max_alerts_per_day': self.max_alerts_per_day,
            'watch_list': self.watch_list,
            'event_count': len(self.detected_events),
            'high_severity_count': sum(1 for event in self.detected_events if event.get('severity', 0) > 0.7),
            'thresholds': {
                'volatility': self.volatility_threshold,
                'volume': self.volume_threshold,
                'correlation': self.correlation_threshold
            }
        }
        
        return status

# Beispiel für die Nutzung
if __name__ == "__main__":
    # Konfiguration
    config = {
        'volatility_threshold': 3.5,
        'volume_threshold': 5.0,
        'correlation_threshold': 0.85,
        'check_interval': 300,  # 5 Minuten
        'watch_list': ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    }
    
    # Black Swan Detector initialisieren
    detector = BlackSwanDetector(config)
    
    # Beispiel-Callback registrieren
    def on_black_swan(event_data):
        print(f"BLACK SWAN ALERT: {event_data['title']} (Schweregrad: {event_data['severity']:.2f})")
        print(f"Meldung: {event_data['message']}")
        print(f"Details: {len(event_data['details'])} Alerts")
    
    detector.register_notification_callback(on_black_swan)
    
    # Monitoring starten (benötigt eine Datenpipeline-Instanz)
    # detector.set_data_pipeline(data_pipeline)
    # detector.start_monitoring()
