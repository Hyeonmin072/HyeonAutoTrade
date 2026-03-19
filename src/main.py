"""
AutoCoinTrade - 메인 진입점
24시간 연속 자동 코인 트레이딩 봇
"""
import asyncio
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import os

# python -m src.main 실행 시 프로젝트 루트를 경로에 추가
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import yaml
from dotenv import load_dotenv

# 로거 설정
from src.monitoring.logger import setup_logger, get_logger
from src.monitoring.health_check import HealthCheck, check_websocket_connection, check_exchange_api
from src.monitoring.notifier import Notifier, TelegramNotifier, SlackNotifier, NotificationLevel

# 데이터 수집
from src.data.collector import DataCollector
from src.data.normalizer import DataNormalizer

# 분석
from src.analysis.signal_generator import SignalGenerator, TradingSignal, SignalType

# 실행
from src.execution.order_manager import OrderManager
from src.execution.exchange_adapter import ExchangeAdapter
from src.execution.risk_manager import RiskManager, RiskConfig, Position

# 저장
from src.storage.timeseries import TimeseriesStore
from src.storage.position_store import PositionStore

# AI 컨텍스트 (입력 데이터 포맷용)
from src.ai.context_builder import create_context_builder

# 시장 스캐너 (동적 심볼)
from src.scanner import MarketScanner


logger = get_logger("main")


class TradingBot:
    """
    메인 트레이딩 봇 클래스
    24시간 연속 운용을 위한 통합 관리
    """
    
    def __init__(self, config_path: str = "config/config.yaml"):
        """
        Args:
            config_path: 설정 파일 경로
        """
        # 환경변수 로드 (config/.env 또는 프로젝트 루트 .env)
        config_dir = Path(config_path).parent
        env_path = config_dir / ".env"
        load_dotenv(env_path)
        load_dotenv()  # 루트 .env도 시도
        
        # 설정 로드
        self.config = self._load_config(config_path)
        
        # 상태
        self._running = False
        self._shutdown_requested = False
        
        # 컴포넌트 초기화
        self._init_components()
        
        # 헬스체크
        self.health_check = HealthCheck(
            interval=self.config["monitoring"]["health_check"]["interval"],
            failure_threshold=self.config["monitoring"]["health_check"]["failure_threshold"],
            on_failure_callback=self._on_health_failure
        )
    
    def _get_exchange_credentials(self, exchange_name: str) -> tuple:
        """거래소별 API 키 환경변수 반환"""
        key_map = {
            "upbit": ("UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY"),
            "binance": ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
            "bithumb": ("BITHUMB_API_KEY", "BITHUMB_SECRET_KEY"),
        }
        key_names = key_map.get(exchange_name.lower(), (f"{exchange_name.upper()}_API_KEY", f"{exchange_name.upper()}_API_SECRET"))
        return os.getenv(key_names[0]), os.getenv(key_names[1])
    
    def _load_config(self, config_path: str) -> Dict:
        """설정 로드"""
        config_file = Path(config_path)
        if not config_file.exists():
            logger.error(f"Config file not found: {config_path}")
            raise FileNotFoundError(config_path)
        
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        logger.info("Configuration loaded")
        return config
    
    def _init_components(self) -> None:
        """컴포넌트 초기화"""
        exchange_name = self.config["exchange"]["name"]
        testnet = self.config["exchange"]["testnet"]
        scanner_cfg = self.config.get("scanner", {})
        self._scanner_enabled = scanner_cfg.get("enabled", False)
        symbols = self.config["exchange"]["symbols"]
        
        # 로거 설정
        log_config = self.config["monitoring"]["log_rotation"]
        setup_logger(
            log_file=self.config["monitoring"]["log_file"],
            log_level=self.config["monitoring"]["log_level"],
            max_size_mb=log_config["max_size"],
            backup_count=log_config["backup_count"]
        )
        
        # 알림 설정
        self.notifier = Notifier()
        self._setup_notifiers()
        
        # 거래소 어댑터 (업비트는 ACCESS_KEY/SECRET_KEY, 기타는 API_KEY/API_SECRET)
        api_key, api_secret = self._get_exchange_credentials(exchange_name)
        exchange_cfg = self.config.get("exchange", {})
        self.exchange = ExchangeAdapter(
            exchange_name=exchange_name,
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
            mode=exchange_cfg.get("mode", "spot"),
            leverage=exchange_cfg.get("leverage", 1)
        )
        
        # 리스크 관리자
        risk_config = RiskConfig(
            stop_loss_percent=self.config["risk_management"]["stop_loss_percent"],
            take_profit_percent=self.config["risk_management"]["take_profit_percent"],
            max_positions=self.config["risk_management"]["max_positions"],
            max_daily_loss_percent=self.config["risk_management"]["max_daily_loss_percent"],
            position_size_percent=self.config["risk_management"]["position_size_percent"],
            min_balance_percent=self.config["risk_management"]["min_balance_percent"],
            leverage=exchange_cfg.get("leverage", 1)
        )
        self.risk_manager = RiskManager(risk_config)
        
        # 데이터 수집기 (스캐너 모드면 REST 전용 + 1분봉 OHLCV)
        scanner_cfg = self.config.get("scanner", {})
        use_ohlcv = self._scanner_enabled and scanner_cfg.get("use_ohlcv_for_signals", True)
        rest_interval = scanner_cfg.get("rest_interval", 15) if use_ohlcv else 60
        self.data_collector = DataCollector(
            exchange_name=exchange_name,
            symbols=symbols,
            testnet=testnet,
            use_rest_only=self._scanner_enabled,
            use_ohlcv_for_signals=use_ohlcv,
            rest_interval=rest_interval
        )
        
        # 데이터 정규화
        self.normalizer = DataNormalizer(exchange_name)
        
        # 가격 캐시 (정규화된 심볼로 초기화)
        self._price_cache: Dict[str, List[float]] = {}
        for s in symbols:
            normalized = self.data_collector._normalize_symbol(s)
            self._price_cache[normalized] = []
        
        # 시계열 저장소
        storage_config = self.config["storage"]["timeseries"]
        self.timeseries_store = TimeseriesStore(
            db_path=storage_config.get("db_path", "data/timeseries.db")
        )
        
        # 포지션 저장소
        position_config = self.config["storage"]["positions"]
        self.position_store = PositionStore(
            db_path=position_config.get("db_path", "data/positions.db")
        )
        
        # 주문 관리자
        self.order_manager = OrderManager(
            exchange_adapter=self.exchange,
            risk_manager=self.risk_manager,
            dry_run=self.config["mode"]["dry_run"]
        )
        
        # 신호 생성기 (AI 활성화 시 hybrid/primary 전략 사용)
        ai_config = self.config.get("ai", {})
        if ai_config.get("enabled"):
            strategy_name = ai_config.get("mode", "hybrid")
            if strategy_name == "primary":
                strategy_name = "gemini"
        else:
            strategy_name = self.config["strategies"]["default"]
        self.signal_generator = SignalGenerator(
            strategy_name=strategy_name,
            config=self.config
        )
        
        # 가격 캐시
        self._price_cache: Dict[str, List[float]] = {s: [] for s in symbols}
        
        # 최근 신호/AI 인사이트 (UI 표시용)
        self._last_signal_insights: Dict[str, dict] = {}
        
        # 스캐너 (동적 심볼)
        self._market_scanner: Optional[MarketScanner] = None
        self._symbols_to_monitor: List[str] = list(symbols)
        if self._scanner_enabled:
            quote = scanner_cfg.get("quote", "KRW" if exchange_name == "upbit" else "USDT")
            self._market_scanner = MarketScanner(
                exchange_name=exchange_name,
                quote=quote,
                max_symbols=scanner_cfg.get("max_symbols", 15),
                min_change_percent=scanner_cfg.get("min_change_percent", 2.0),
                min_volume_quote=scanner_cfg.get("min_volume_quote", 0),
                sort_by=scanner_cfg.get("sort_by", "change_abs"),
                testnet=testnet
            )
            logger.info(f"Market scanner enabled: dynamic symbols (quote={quote})")
        
        logger.info(f"Components initialized for {exchange_name}")
    
    def _setup_notifiers(self) -> None:
        """알림 채널 설정"""
        notif_config = self.config["monitoring"]["notifications"]
        
        # Telegram
        if notif_config.get("telegram", {}).get("enabled"):
            telegram_config = notif_config["telegram"]
            if telegram_config.get("bot_token") and telegram_config.get("chat_id"):
                self.notifier.add_channel(TelegramNotifier(
                    bot_token=telegram_config["bot_token"],
                    chat_id=telegram_config["chat_id"]
                ))
        
        # Slack
        if notif_config.get("slack", {}).get("enabled"):
            slack_config = notif_config["slack"]
            if slack_config.get("webhook_url"):
                self.notifier.add_channel(SlackNotifier(
                    webhook_url=slack_config["webhook_url"]
                ))
    
    async def _on_health_failure(self, results: Dict) -> None:
        """헬스체크 실패 시 콜백"""
        logger.error("Health check failed, initiating graceful shutdown...")
        await self._shutdown()
    
    async def start(self) -> None:
        """시작"""
        if self._running:
            logger.warning("Bot already running")
            return
        
        logger.info("=" * 50)
        logger.info("AutoCoinTrade Starting...")
        logger.info("=" * 50)
        
        self._running = True
        
        try:
            # 0. 스캐너 모드: 초기 시장 스캔
            if self._scanner_enabled and self._market_scanner:
                await self._run_scanner()
            
            # 1. 상태 복구
            await self._recover_state()
            
            # 2. 잔고 조회 및 설정
            await self._sync_balance()
            
            # 3. 헬스체크 시작
            self._register_health_checks()
            await self.health_check.start()
            
            # 4. 데이터 수집 시작
            await self.data_collector.start()
            if self.data_collector.use_ohlcv_for_signals:
                self.data_collector.register_ohlcv_update_callback(self._on_ohlcv_update)
            else:
                self.data_collector.register_ticker_callback(self._on_ticker)
            
            # 5. 메인 루프 시작
            await self._main_loop()
            
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            await self.notifier.error("Fatal Error", str(e))
            await self._shutdown()
    
    async def _recover_state(self) -> None:
        """상태 복구"""
        logger.info("Recovering state...")
        
        # 포지션 복구
        saved_positions = await self.position_store.load_positions_for_recovery()
        for pos_data in saved_positions:
            position = Position(
                symbol=pos_data["symbol"],
                side=pos_data["side"],
                entry_price=pos_data["entry_price"],
                current_price=pos_data["entry_price"],
                quantity=pos_data["quantity"],
                entry_time=datetime.fromisoformat(pos_data["entry_time"]),
                stop_loss=pos_data.get("stop_loss"),
                take_profit=pos_data.get("take_profit")
            )
            self.risk_manager.positions[position.symbol] = position
        
        # 미체결 주문 동기화
        await self.order_manager.sync_orders()
        
        logger.info(f"State recovered: {len(saved_positions)} positions")
    
    async def _run_scanner(self) -> None:
        """시장 스캔 실행 및 심볼 업데이트"""
        if not self._market_scanner:
            return
        try:
            result = await self._market_scanner.scan()
            if result.symbols:
                self._symbols_to_monitor = result.symbols
                self.data_collector.update_symbols(result.symbols)
                for s in result.symbols:
                    if s not in self._price_cache:
                        self._price_cache[s] = []
                logger.info(f"Scanner: monitoring {len(result.symbols)} symbols")
        except Exception as e:
            logger.error(f"Scanner error: {e}")
    
    async def _sync_balance(self) -> None:
        """잔고 동기화"""
        try:
            balances = await self.exchange.fetch_balance()
            
            # 총 잔고 계산
            total = sum(b.free for b in balances.values())
            self.risk_manager.set_balance(total)
            
            # 잔고 저장
            for currency, balance in balances.items():
                await self.position_store.save_balance(
                    currency, balance.total, balance.free, balance.used
                )
            
            quote_currency = self._symbols_to_monitor[0].split("/")[-1] if self._symbols_to_monitor else "USDT"
            logger.info(f"Balance synced: {total:.2f} {quote_currency}")
            
        except Exception as e:
            logger.error(f"Balance sync failed: {e}")
    
    def _register_health_checks(self) -> None:
        """헬스체크 등록"""
        # websocket: critical=False - REST 폴백으로 동작 가능
        self.health_check.register_check(
            "websocket",
            lambda: check_websocket_connection(self.data_collector.ws_client),
            critical=False
        )
        # exchange: critical=True - 거래소 API 실패 시에만 재시작
        self.health_check.register_check(
            "exchange",
            lambda: check_exchange_api(self.exchange)
        )
    
    async def _on_ohlcv_update(self, symbol: str, closes: list) -> None:
        """1분봉 OHLCV 업데이트 콜백 (단타용)"""
        try:
            if symbol not in self._price_cache:
                self._price_cache[symbol] = []
            self._price_cache[symbol] = list(closes)[-200:]  # 1분봉 close 시퀀스
            
            # 리스크 관리 업데이트
            if self.risk_manager.positions and closes:
                await self._update_risk_from_prices()
        except Exception as e:
            logger.error(f"OHLCV update callback error: {e}")
    
    async def _update_risk_from_prices(self) -> None:
        """가격 캐시 기반 리스크 업데이트"""
        try:
            prices = {s: (self._price_cache.get(s) or [0])[-1] for s in self.risk_manager.positions}
            to_close = self.risk_manager.update_positions(prices)
            for symbol in to_close:
                await self._close_position_for_risk(symbol)
        except Exception as e:
            logger.error(f"Risk update error: {e}")
    
    async def _on_ticker(self, ticker) -> None:
        """티커 업데이트 콜백"""
        try:
            # 가격 캐시 업데이트
            symbol = ticker.symbol
            if symbol in self._price_cache:
                self._price_cache[symbol].append(ticker.price)
                
                # 최대 200개 유지
                if len(self._price_cache[symbol]) > 200:
                    self._price_cache[symbol] = self._price_cache[symbol][-200:]
            
            # 리스크 관리 업데이트
            if self.risk_manager.positions:
                prices = {s: self._price_cache.get(s, [0])[-1] 
                         for s in self.risk_manager.positions.keys()}
                to_close = self.risk_manager.update_positions(prices)
                
                # 손절/익절 주문 실행
                for symbol in to_close:
                    await self._close_position_for_risk(symbol)
            
        except Exception as e:
            logger.error(f"Ticker callback error: {e}")
    
    async def _main_loop(self) -> None:
        """메인 루프"""
        logger.info("Main loop started")
        
        check_interval = self.config.get("execution", {}).get("check_interval", 15)
        scan_interval = self.config.get("scanner", {}).get("scan_interval", 300)
        last_scan = 0.0
        
        while self._running and not self._shutdown_requested:
            try:
                # 주기적 스캔 (스캐너 모드)
                now = time.monotonic()
                if self._scanner_enabled and self._market_scanner and (now - last_scan) >= scan_interval:
                    await self._run_scanner()
                    last_scan = now
                
                for symbol in self._symbols_to_monitor:
                    await self._check_and_trade(symbol)
                
                await asyncio.sleep(check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Main loop error: {e}", exc_info=True)
                await asyncio.sleep(5)
        
        logger.info("Main loop ended")
    
    async def _check_and_trade(self, symbol: str) -> None:
        """신호 체크 및 거래"""
        # 충분한 데이터 확인
        prices = self._price_cache.get(symbol, [])
        min_data_points = self.signal_generator.strategy.get_required_data_points()
        
        if len(prices) < min_data_points:
            logger.debug(f"Not enough data for {symbol}: {len(prices)}/{min_data_points}")
            return
        
        # 단타 변동률 필터 (저변동 코인 스킵)
        min_volatility = self.config.get("scanner", {}).get("min_volatility_percent", 0.1)
        if min_volatility > 0 and len(prices) >= 5:
            lookback = min(5, len(prices) - 1)
            change_pct = abs(prices[-1] - prices[-1 - lookback]) / (prices[-1 - lookback] or 1e-8) * 100
            if change_pct < min_volatility:
                logger.debug(f"Skip {symbol}: low volatility {change_pct:.2f}% < {min_volatility}%")
                return
        
        # 지표 계산 (AI 전략 시 컨텍스트에 사용)
        ind_cfg = self.config.get("indicators", {})
        indicators_data = self.signal_generator.calculate_indicators(
            prices,
            rsi_period=ind_cfg.get("rsi", {}).get("period", 14),
            macd_fast=ind_cfg.get("macd", {}).get("fast_period", 12),
            macd_slow=ind_cfg.get("macd", {}).get("slow_period", 26),
            macd_signal=ind_cfg.get("macd", {}).get("signal_period", 9),
            bb_period=ind_cfg.get("bollinger_bands", {}).get("period", 20),
            bb_std=ind_cfg.get("bollinger_bands", {}).get("std_dev", 2.0),
        )
        
        # 신호 생성 (AI 전략은 비동기)
        ai_enabled = self.config.get("ai", {}).get("enabled", False)
        if ai_enabled:
            signal = await self.signal_generator.generate_signal_async(
                symbol, prices, indicators_data
            )
        else:
            signal = self.signal_generator.generate_signal(
                symbol, prices, indicators_data
            )
        
        # 신호/AI 인사이트 저장 (UI 표시용)
        ctx = create_context_builder(self.config)
        indicators_summary = ctx.format_indicators(indicators_data)
        recent_prices_preview = ", ".join(
            f"{p:.2f}" for p in (prices[-30:] if len(prices) >= 30 else prices)
        )
        self._last_signal_insights[symbol] = {
            "symbol": symbol,
            "signal_type": signal.signal_type.value,
            "reason": signal.reason,
            "strength": signal.strength,
            "metadata": signal.metadata,
            "timestamp": datetime.now().isoformat(),
            "ai_input": {
                "indicators_summary": indicators_summary,
                "recent_prices": recent_prices_preview,
            },
        }
        # skip_reason은 미실행 시에만 설정됨
        
        if not signal.is_actionable:
            return
        
        # 리스크 체크
        block_reason = self.risk_manager.get_block_reason(symbol)
        if block_reason:
            logger.warning(f"Risk check failed for {symbol}: {block_reason}")
            self._last_signal_insights[symbol]["skip_reason"] = block_reason
            return
        
        # 현재가 조회
        ticker = self.data_collector.get_ticker(symbol)
        current_price = ticker.price if ticker else prices[-1]
        
        # 매수 신호
        if signal.is_buy:
            if self.exchange.is_futures and self.risk_manager.has_position(symbol):
                pos = self.risk_manager.get_position(symbol)
                if pos.side == "short":
                    await self._execute_close_short(symbol, signal, current_price)
                else:
                    logger.info(f"Already long for {symbol}")
            else:
                await self._execute_buy(symbol, signal, current_price)
        
        # 매도 신호
        elif signal.is_sell:
            if self.exchange.is_futures:
                # 선물: 포지션 없으면 숏 진입, 있으면 청산
                if self.risk_manager.has_position(symbol):
                    pos = self.risk_manager.get_position(symbol)
                    if pos.side == "long":
                        await self._execute_sell(symbol, signal, current_price)
                    else:
                        await self._execute_close_short(symbol, signal, current_price)
                else:
                    await self._execute_short(symbol, signal, current_price)
            else:
                # 현물: 롱 포지션만 청산
                await self._execute_sell(symbol, signal, current_price)
    
    async def _execute_buy(
        self,
        symbol: str,
        signal: TradingSignal,
        price: float
    ) -> None:
        """매수 실행 (롱 진입)"""
        if self.risk_manager.has_position(symbol):
            logger.info(f"Position already exists for {symbol}")
            if symbol in self._last_signal_insights:
                self._last_signal_insights[symbol]["skip_reason"] = "이미 포지션 보유"
            return
        
        # 선물: 레버리지 설정
        if self.exchange.is_futures:
            await self.exchange.set_leverage(symbol, self.exchange.leverage)
        
        # 잔고 계산 (선물: 레버리지 적용)
        balance = self.risk_manager.total_balance
        position_size = balance * (self.config["risk_management"]["position_size_percent"] / 100)
        if self.exchange.is_futures:
            position_size *= self.exchange.leverage
        amount = position_size / price
        
        # 최소 수량 확인
        min_amount = self.exchange.get_min_order_amount(symbol)
        if amount < min_amount:
            logger.warning(f"Amount {amount} below minimum {min_amount}")
            if symbol in self._last_signal_insights:
                self._last_signal_insights[symbol]["skip_reason"] = f"최소 수량 미달 ({amount:.6f} < {min_amount})"
            return
        
        # 주문 실행
        result = await self.order_manager.execute_buy(
            symbol=symbol,
            amount=amount,
            order_type=self.config["execution"]["order_type"],
            strategy=self.signal_generator.strategy_name
        )
        
        if result.success and result.order:
            # 포지션 추가
            position = Position(
                symbol=symbol,
                side="long",
                entry_price=result.order.price,
                current_price=result.order.price,
                quantity=result.order.filled
            )
            self.risk_manager.add_position(position)
            
            # 저장
            await self.position_store.save_position(position.to_dict())
            await self.position_store.log_trade(
                symbol=symbol,
                action="BUY",
                quantity=amount,
                price=result.order.price,
                strategy=self.signal_generator.strategy_name,
                metadata={"reason": signal.reason, **(signal.metadata or {})}
            )
            
            # 알림
            await self.notifier.notify_trade(
                action="BUY",
                symbol=symbol,
                price=result.order.price,
                amount=amount
            )
            
            logger.info(f"BUY executed: {symbol} {amount} @ {result.order.price}")
    
    async def _execute_sell(
        self,
        symbol: str,
        signal: TradingSignal,
        price: float
    ) -> None:
        """매도 실행"""
        if not self.risk_manager.has_position(symbol):
            return
        
        position = self.risk_manager.get_position(symbol)
        
        # 주문 실행
        result = await self.order_manager.execute_sell(
            symbol=symbol,
            amount=position.quantity,
            order_type=self.config["execution"]["order_type"],
            strategy=self.signal_generator.strategy_name
        )
        
        if result.success and result.order:
            # 포지션 제거
            realized_pnl = (result.order.price - position.entry_price) * position.quantity
            self.risk_manager.remove_position(symbol, realized_pnl)
            
            # 저장
            await self.position_store.close_position(symbol, realized_pnl)
            await self.position_store.log_trade(
                symbol=symbol,
                action="SELL",
                quantity=position.quantity,
                price=result.order.price,
                pnl=realized_pnl,
                strategy=self.signal_generator.strategy_name,
                metadata={"reason": signal.reason, **(signal.metadata or {})}
            )
            
            # 알림
            await self.notifier.notify_trade(
                action="SELL",
                symbol=symbol,
                price=result.order.price,
                amount=position.quantity
            )
            
            logger.info(f"SELL executed: {symbol} {position.quantity} @ {result.order.price}, PnL: {realized_pnl:.2f}")
    
    async def _execute_short(
        self,
        symbol: str,
        signal: TradingSignal,
        price: float
    ) -> None:
        """숏 진입 (binance 선물 전용)"""
        if not self.exchange.is_futures:
            return
        if self.risk_manager.has_position(symbol):
            logger.info(f"Position already exists for {symbol}")
            return
        
        await self.exchange.set_leverage(symbol, self.exchange.leverage)
        
        balance = self.risk_manager.total_balance
        position_size = balance * (self.config["risk_management"]["position_size_percent"] / 100)
        position_size *= self.exchange.leverage
        amount = position_size / price
        
        min_amount = self.exchange.get_min_order_amount(symbol)
        if amount < min_amount:
            logger.warning(f"Amount {amount} below minimum {min_amount}")
            return
        
        result = await self.order_manager.execute_sell(
            symbol=symbol,
            amount=amount,
            order_type=self.config["execution"]["order_type"],
            strategy=self.signal_generator.strategy_name
        )
        
        if result.success and result.order:
            position = Position(
                symbol=symbol,
                side="short",
                entry_price=result.order.price,
                current_price=result.order.price,
                quantity=result.order.filled
            )
            self.risk_manager.add_position(position)
            await self.position_store.save_position(position.to_dict())
            await self.position_store.log_trade(
                symbol=symbol,
                action="SHORT",
                quantity=amount,
                price=result.order.price,
                strategy=self.signal_generator.strategy_name,
                metadata={"reason": signal.reason, **(signal.metadata or {})}
            )
            await self.notifier.notify_trade(
                action="SHORT",
                symbol=symbol,
                price=result.order.price,
                amount=amount
            )
            logger.info(f"SHORT executed: {symbol} {amount} @ {result.order.price}")
    
    async def _execute_close_short(
        self,
        symbol: str,
        signal: TradingSignal,
        price: float
    ) -> None:
        """숏 청산 (binance 선물 전용)"""
        if not self.exchange.is_futures:
            return
        if not self.risk_manager.has_position(symbol):
            return
        
        position = self.risk_manager.get_position(symbol)
        if position.side != "short":
            return
        
        result = await self.order_manager.execute_buy(
            symbol=symbol,
            amount=position.quantity,
            order_type=self.config["execution"]["order_type"],
            strategy=self.signal_generator.strategy_name
        )
        
        if result.success and result.order:
            realized_pnl = (position.entry_price - result.order.price) * position.quantity
            self.risk_manager.remove_position(symbol, realized_pnl)
            await self.position_store.close_position(symbol, realized_pnl)
            await self.position_store.log_trade(
                symbol=symbol,
                action="CLOSE_SHORT",
                quantity=position.quantity,
                price=result.order.price,
                pnl=realized_pnl,
                strategy=self.signal_generator.strategy_name,
                metadata={"reason": signal.reason, **(signal.metadata or {})}
            )
            await self.notifier.notify_trade(
                action="CLOSE_SHORT",
                symbol=symbol,
                price=result.order.price,
                amount=position.quantity
            )
            logger.info(f"CLOSE SHORT: {symbol} {position.quantity} @ {result.order.price}, PnL: {realized_pnl:.2f}")
    
    async def _close_position_for_risk(self, symbol: str) -> None:
        """리스크 관리용 포지션 종료 (롱/숏 모두 처리)"""
        position = self.risk_manager.get_position(symbol)
        if not position:
            return
        if position.side == "short":
            # 숏 청산: 매수 (reduce_only)
            result = await self.order_manager.execute_buy(
                symbol=symbol,
                amount=position.quantity,
                order_type=self.config["execution"]["order_type"],
                strategy="risk_management",
                reduce_only=True
            )
        else:
            # 롱 청산: 매도
            result = await self.order_manager.execute_sell(
                symbol=symbol,
                amount=position.quantity,
                order_type=self.config["execution"]["order_type"],
                strategy="risk_management"
            )
        if result.success and result.order:
            if position.side == "long":
                realized_pnl = (result.order.price - position.entry_price) * position.quantity
            else:
                realized_pnl = (position.entry_price - result.order.price) * position.quantity
            self.risk_manager.remove_position(symbol, realized_pnl)
            await self.position_store.close_position(symbol, realized_pnl)
            await self.position_store.log_trade(
                symbol=symbol,
                action="CLOSE_SHORT" if position.side == "short" else "SELL",
                quantity=position.quantity,
                price=result.order.price,
                pnl=realized_pnl,
                strategy="risk_management",
                metadata={"reason": "Risk management"}
            )
            logger.info(f"Position closed ({position.side}): {symbol}, PnL: {realized_pnl:.2f}")
    
    async def _shutdown(self) -> None:
        """GracefulShutdown"""
        if self._shutdown_requested:
            return
        
        self._shutdown_requested = True
        logger.info("Initiating graceful shutdown...")
        
        try:
            # 1. 새 주문 중단
            self._running = False
            
            # 2. 헬스체크 중지
            await self.health_check.stop()
            
            # 3. 데이터 수집 중지
            await self.data_collector.stop()
            
            # 4. 열린 주문 취소
            await self.order_manager.cancel_all_orders()
            
            # 5. 포지션 저장
            for symbol, position in self.risk_manager.positions.items():
                await self.position_store.save_position(position.to_dict())
            
            logger.info("Graceful shutdown completed")
            await self.notifier.notify_shutdown()
            
        except Exception as e:
            logger.error(f"Shutdown error: {e}")
        
        finally:
            # 저장소 정리
            self.timeseries_store.close()
            self.position_store.close()
            
            logger.info("Shutdown complete")
            # sys.exit 제거: 웹 모드에서 uvicorn과 이벤트 루프를 함께 종료시키지 않음
    
    def stop(self) -> None:
        """중지 요청"""
        asyncio.create_task(self._shutdown())


# =============================================================================
# Main Entry Point
# =============================================================================

async def main():
    """메인 함수"""
    bot = None
    
    try:
        bot = TradingBot()
        
        # SIGTERM 핸들러
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            if bot:
                bot.stop()
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        # 시작
        await bot.start()
        
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        if bot:
            await bot._shutdown()
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
