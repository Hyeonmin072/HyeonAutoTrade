"""
시계열 데이터 저장소
시세, 캔들, 지표 데이터 영속성
"""
import asyncio
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
import json

from ..monitoring.logger import get_logger


logger = get_logger("timeseries_store")


@dataclass
class PriceData:
    """가격 데이터"""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1m"
    
    def to_tuple(self) -> Tuple:
        return (
            self.symbol,
            self.timestamp.isoformat(),
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.timeframe
        )


class TimeseriesStore:
    """
    시계열 데이터 저장소
    SQLite 기반 시세 데이터 저장 및 조회
    """
    
    def __init__(self, db_path: str = "data/timeseries.db"):
        """
        Args:
            db_path: 데이터베이스 경로
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        
        # 초기화
        self._init_db()
    
    def _init_db(self) -> None:
        """데이터베이스 초기화"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # 가격 데이터 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                timeframe TEXT DEFAULT '1m',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timestamp, timeframe)
            )
        """)
        
        # 인덱스 생성
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_symbol_time 
            ON price_data(symbol, timestamp)
        """)
        
        # 지표 데이터 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indicator_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                indicator_name TEXT NOT NULL,
                value REAL NOT NULL,
                metadata TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, timestamp, indicator_name)
            )
        """)
        
        conn.commit()
        logger.info(f"TimeseriesStore initialized: {self.db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """연결 반환"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    async def save_price(self, price_data: PriceData) -> None:
        """가격 데이터 저장"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO price_data 
                    (symbol, timestamp, open, high, low, close, volume, timeframe)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, price_data.to_tuple())
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to save price: {e}")
    
    async def save_prices_batch(self, price_data_list: List[PriceData]) -> int:
        """배치 저장"""
        if not price_data_list:
            return 0
        
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                data_tuples = [pd.to_tuple() for pd in price_data_list]
                cursor.executemany("""
                    INSERT OR REPLACE INTO price_data 
                    (symbol, timestamp, open, high, low, close, volume, timeframe)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, data_tuples)
                conn.commit()
                return len(data_tuples)
            except Exception as e:
                logger.error(f"Failed to batch save prices: {e}")
                return 0
    
    async def get_prices(
        self,
        symbol: str,
        timeframe: str = "1m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[PriceData]:
        """가격 데이터 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = "SELECT * FROM price_data WHERE symbol = ? AND timeframe = ?"
            params: List[Any] = [symbol, timeframe]
            
            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time.isoformat())
            
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time.isoformat())
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [
                PriceData(
                    symbol=row["symbol"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    timeframe=row["timeframe"]
                )
                for row in rows
            ]
    
    async def get_latest_price(self, symbol: str, timeframe: str = "1m") -> Optional[PriceData]:
        """최근 가격 조회"""
        prices = await self.get_prices(symbol, timeframe, limit=1)
        return prices[0] if prices else None
    
    async def get_closes(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100
    ) -> List[float]:
        """종가 리스트 조회"""
        prices = await self.get_prices(symbol, timeframe, limit=limit)
        return [p.close for p in reversed(prices)]
    
    async def save_indicator(
        self,
        symbol: str,
        indicator_name: str,
        value: float,
        timestamp: Optional[datetime] = None,
        metadata: Optional[Dict] = None
    ) -> None:
        """지표 데이터 저장"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            ts = timestamp or datetime.now()
            metadata_json = json.dumps(metadata) if metadata else None
            
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO indicator_data 
                    (symbol, timestamp, indicator_name, value, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """, (symbol, ts.isoformat(), indicator_name, value, metadata_json))
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to save indicator: {e}")
    
    async def get_indicators(
        self,
        symbol: str,
        indicator_name: str,
        limit: int = 100
    ) -> List[Dict]:
        """지표 데이터 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM indicator_data 
                WHERE symbol = ? AND indicator_name = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, indicator_name, limit))
            
            rows = cursor.fetchall()
            
            return [
                {
                    "symbol": row["symbol"],
                    "timestamp": row["timestamp"],
                    "indicator_name": row["indicator_name"],
                    "value": row["value"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {}
                }
                for row in rows
            ]
    
    async def delete_old_data(self, days: int = 30) -> int:
        """오래된 데이터 삭제"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            
            cursor.execute("DELETE FROM price_data WHERE timestamp < ?", (cutoff,))
            price_deleted = cursor.rowcount
            
            cursor.execute("DELETE FROM indicator_data WHERE timestamp < ?", (cutoff,))
            indicator_deleted = cursor.rowcount
            
            conn.commit()
            
            total = price_deleted + indicator_deleted
            if total > 0:
                logger.info(f"Deleted {total} old records")
            
            return total
    
    async def get_stats(self) -> Dict:
        """통계 반환"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) as count FROM price_data")
            price_count = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM indicator_data")
            indicator_count = cursor.fetchone()["count"]
            
            cursor.execute("""
                SELECT COUNT(DISTINCT symbol) as count FROM price_data
            """)
            symbol_count = cursor.fetchone()["count"]
            
            return {
                "price_records": price_count,
                "indicator_records": indicator_count,
                "unique_symbols": symbol_count,
                "db_path": str(self.db_path)
            }
    
    def close(self) -> None:
        """연결 종료"""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
