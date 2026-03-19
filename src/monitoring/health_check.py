"""
헬스체크 모듈
프로세스 상태 모니터링 및 자동 복구
"""
import asyncio
import time
from enum import Enum
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .logger import get_logger


logger = get_logger("health_check")


class HealthStatus(Enum):
    """헬스 상태枚举"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"


@dataclass
class HealthCheckResult:
    """헬스체크 결과"""
    component: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }


class HealthCheck:
    """
    헬스체크 관리자
    24시간 연속 운용을 위한 프로세스 모니터링
    """
    
    def __init__(
        self,
        interval: int = 30,
        failure_threshold: int = 3,
        on_failure_callback: Optional[Callable] = None
    ):
        """
        Args:
            interval: 체크 간격 (초)
            failure_threshold: 재시작 임계값 (연속 실패 횟수)
            on_failure_callback: 실패 시 콜백 함수
        """
        self.interval = interval
        self.failure_threshold = failure_threshold
        self.on_failure_callback = on_failure_callback
        
        self._checks: Dict[str, Callable] = {}
        self._failure_counts: Dict[str, int] = {}
        self._last_results: Dict[str, HealthCheckResult] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._start_time: Optional[datetime] = None
        self._restart_count = 0
    
    def register_check(
        self,
        name: str,
        check_func: Callable,
        critical: bool = True
    ) -> None:
        """
        헬스체크 함수 등록
        
        Args:
            name: 체크 이름
            check_func: 비동기 체크 함수 (HealthCheckResult 반환)
            critical: True면 실패 시 재시작 트리거, False면 로그만 (REST 폴백 등)
        """
        self._checks[name] = check_func
        self._failure_counts[name] = 0
        if not hasattr(self, "_critical_checks"):
            self._critical_checks: Dict[str, bool] = {}
        self._critical_checks[name] = critical
        logger.info(f"Registered health check: {name} (critical={critical})")
    
    async def check_component(self, name: str) -> HealthCheckResult:
        """단일 컴포넌트 체크"""
        if name not in self._checks:
            return HealthCheckResult(
                component=name,
                status=HealthStatus.CRITICAL,
                message="Check not registered"
            )
        
        start_time = time.perf_counter()
        check_func = self._checks[name]
        
        try:
            if asyncio.iscoroutinefunction(check_func):
                result = await check_func()
            else:
                result = check_func()
            
            # check_func가 coroutine을 반환하는 경우 (lambda: async_func() 등)
            if asyncio.iscoroutine(result):
                result = await result
            
            if isinstance(result, HealthCheckResult):
                return result
            elif isinstance(result, dict):
                return HealthCheckResult(**result)
            elif isinstance(result, bool):
                return HealthCheckResult(
                    component=name,
                    status=HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY,
                    latency_ms=(time.perf_counter() - start_time) * 1000
                )
            else:
                return result
                
        except Exception as e:
            latency = (time.perf_counter() - start_time) * 1000
            logger.error(f"Health check failed for {name}: {e}")
            return HealthCheckResult(
                component=name,
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                latency_ms=latency
            )
    
    async def run_all_checks(self) -> Dict[str, HealthCheckResult]:
        """모든 체크 실행"""
        results = {}
        for name in self._checks:
            results[name] = await self.check_component(name)
            self._last_results[name] = results[name]
            
            # 실패 횟수 업데이트
            if results[name].status in [HealthStatus.UNHEALTHY, HealthStatus.CRITICAL]:
                self._failure_counts[name] += 1
            else:
                self._failure_counts[name] = 0
        
        return results
    
    def get_overall_status(self, results: Dict[str, HealthCheckResult]) -> HealthStatus:
        """전체 상태 판단"""
        if not results:
            return HealthStatus.UNHEALTHY
        
        statuses = [r.status for r in results.values()]
        
        if any(s == HealthStatus.CRITICAL for s in statuses):
            return HealthStatus.CRITICAL
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.DEGRADED
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    def should_restart(self, results: Dict[str, HealthCheckResult]) -> bool:
        """재시작 필요 여부 판단 (critical 체크만)"""
        critical = getattr(self, "_critical_checks", {})
        for name, result in results.items():
            if not critical.get(name, True):  # non-critical은 무시
                continue
            if result.status in [HealthStatus.UNHEALTHY, HealthStatus.CRITICAL]:
                if self._failure_counts[name] >= self.failure_threshold:
                    return True
        return False
    
    async def _monitor_loop(self) -> None:
        """모니터링 루프"""
        logger.info(f"Health check monitor started (interval: {self.interval}s)")
        
        while self._running:
            try:
                # 모든 체크 실행
                results = await self.run_all_checks()
                overall_status = self.get_overall_status(results)
                
                # 로그 출력
                if overall_status != HealthStatus.HEALTHY:
                    logger.warning(
                        f"Health status: {overall_status.value}",
                        extra={"details": {k: v.to_dict() for k, v in results.items()}}
                    )
                
                # 재시작 판단
                if self.should_restart(results):
                    self._restart_count += 1
                    logger.error(
                        f"Restart threshold exceeded ({self._restart_count} times). "
                        "Triggering restart..."
                    )
                    
                    # 콜백 실행
                    if self.on_failure_callback:
                        try:
                            await self.on_failure_callback(results)
                        except Exception as e:
                            logger.error(f"Failure callback error: {e}")
                    
                    # Graceful shutdown 후 재시작 신호
                    raise RestartRequiredError("Health check failed")
                
                # 대기
                await asyncio.sleep(self.interval)
                
            except RestartRequiredError:
                raise
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(self.interval)
    
    async def start(self) -> None:
        """모니터링 시작"""
        if self._running:
            logger.warning("Health check already running")
            return
        
        self._running = True
        self._start_time = datetime.now()
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Health check started")
    
    async def stop(self) -> None:
        """모니터링 중지"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health check stopped")
    
    def get_status(self) -> Dict:
        """현재 상태 반환"""
        return {
            "running": self._running,
            "uptime": (datetime.now() - self._start_time).total_seconds() if self._start_time else 0,
            "restart_count": self._restart_count,
            "checks": {k: {
                "registered": True,
                "failure_count": self._failure_counts.get(k, 0),
                "last_result": self._last_results.get(k).to_dict() if self._last_results.get(k) else None
            } for k in self._checks.keys()},
            "overall_status": self.get_overall_status(self._last_results).value
        }


class RestartRequiredError(Exception):
    """재시작 필요 예외"""
    pass


# =============================================================================
# 기본 헬스체크 함수들
# =============================================================================

async def check_websocket_connection(client) -> HealthCheckResult:
    """WebSocket 연결 상태 체크"""
    import time
    start = time.perf_counter()
    
    if client is None:
        return HealthCheckResult(
            component="websocket",
            status=HealthStatus.CRITICAL,
            message="WebSocket client not initialized",
            latency_ms=0
        )
    
    val = getattr(client, "is_connected", lambda: False)
    is_connected = val() if callable(val) else bool(val)
    latency = (time.perf_counter() - start) * 1000
    
    return HealthCheckResult(
        component="websocket",
        status=HealthStatus.HEALTHY if is_connected else HealthStatus.UNHEALTHY,
        message="Connected" if is_connected else "Disconnected",
        latency_ms=latency
    )


async def check_exchange_api(exchange) -> HealthCheckResult:
    """거래소 API 연결 체크"""
    import time
    start = time.perf_counter()
    
    if exchange is None:
        return HealthCheckResult(
            component="exchange_api",
            status=HealthStatus.CRITICAL,
            message="Exchange not initialized",
            latency_ms=0
        )
    
    try:
        # 거래소별 기본 심볼 (업비트: BTC/KRW, 기타: BTC/USDT)
        exchange_name = getattr(exchange, 'exchange_name', 'binance')
        symbol = "BTC/KRW" if exchange_name == "upbit" else "BTC/USDT"
        
        fetch_ticker = getattr(exchange, 'fetch_ticker', None)
        if fetch_ticker is None:
            raise AttributeError("Exchange has no fetch_ticker method")
        
        # async 함수인지 확인
        if asyncio.iscoroutinefunction(fetch_ticker):
            await fetch_ticker(symbol)
        else:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: fetch_ticker(symbol))
            
        latency = (time.perf_counter() - start) * 1000
        return HealthCheckResult(
            component="exchange_api",
            status=HealthStatus.HEALTHY,
            message="API responding",
            latency_ms=latency
        )
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return HealthCheckResult(
            component="exchange_api",
            status=HealthStatus.UNHEALTHY,
            message=str(e),
            latency_ms=latency
        )
