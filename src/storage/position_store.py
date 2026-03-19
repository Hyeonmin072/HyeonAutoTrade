"""
포지션 저장소 모듈
포지션/잔고 상태 영속성 및 복구
"""
import asyncio
import sqlite3
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
from dataclasses import asdict
import json

from ..monitoring.logger import get_logger


logger = get_logger("position_store")


class PositionStore:
    """
    포지션 저장소
    SQLite 기반 포지션, 주문, 잔고 상태 저장 및 복구
    """
    
    def __init__(self, db_path: str = "data/positions.db"):
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
        
        # 포지션 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                entry_time TEXT NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                unrealized_pnl REAL DEFAULT 0,
                unrealized_pnl_percent REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 주문 히스토리 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                type TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL,
                amount REAL NOT NULL,
                filled REAL DEFAULT 0,
                status TEXT NOT NULL,
                fee REAL DEFAULT 0,
                fee_currency TEXT,
                timestamp TEXT NOT NULL,
                metadata TEXT
            )
        """)
        
        # 잔고 히스토리 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                currency TEXT NOT NULL,
                total REAL NOT NULL,
                free REAL NOT NULL,
                used REAL NOT NULL,
                timestamp TEXT NOT NULL,
                note TEXT
            )
        """)
        
        # 트레이드 로그 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                pnl REAL,
                timestamp TEXT NOT NULL,
                strategy TEXT,
                metadata TEXT
            )
        """)
        
        # 인덱스
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_symbol ON order_history(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_balance_timestamp ON balance_history(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trade_symbol ON trade_log(symbol)")
        
        conn.commit()
        logger.info(f"PositionStore initialized: {self.db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """연결 반환"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    # =====================================================================
    # Position Methods
    # =====================================================================
    
    async def save_position(self, position: Dict) -> bool:
        """포지션 저장"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO positions 
                    (symbol, side, entry_price, quantity, entry_time, 
                     stop_loss, take_profit, unrealized_pnl, unrealized_pnl_percent,
                     status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position.get("symbol"),
                    position.get("side"),
                    position.get("entry_price"),
                    position.get("quantity"),
                    position.get("entry_time"),
                    position.get("stop_loss"),
                    position.get("take_profit"),
                    position.get("unrealized_pnl", 0),
                    position.get("unrealized_pnl_percent", 0),
                    position.get("status", "open"),
                    datetime.now().isoformat()
                ))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to save position: {e}")
                return False
    
    async def get_position(self, symbol: str) -> Optional[Dict]:
        """포지션 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM positions WHERE symbol = ? AND status = 'open'",
                (symbol,)
            )
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    
    async def get_all_positions(self) -> List[Dict]:
        """모든 포지션 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM positions WHERE status = 'open'")
            rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
    
    async def close_position(self, symbol: str, pnl: float = 0) -> bool:
        """포지션 종료"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    UPDATE positions 
                    SET status = 'closed', 
                        unrealized_pnl = ?,
                        updated_at = ?
                    WHERE symbol = ? AND status = 'open'
                """, (pnl, datetime.now().isoformat(), symbol))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Failed to close position: {e}")
                return False
    
    async def update_position_pnl(
        self,
        symbol: str,
        unrealized_pnl: float,
        unrealized_pnl_percent: float
    ) -> bool:
        """포지션 손익 업데이트"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    UPDATE positions 
                    SET unrealized_pnl = ?,
                        unrealized_pnl_percent = ?,
                        updated_at = ?
                    WHERE symbol = ? AND status = 'open'
                """, (unrealized_pnl, unrealized_pnl_percent, datetime.now().isoformat(), symbol))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Failed to update position PnL: {e}")
                return False
    
    # =====================================================================
    # Order Methods
    # =====================================================================
    
    async def save_order(self, order: Dict) -> bool:
        """주문 저장"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO order_history 
                    (order_id, symbol, type, side, price, amount, filled, 
                     status, fee, fee_currency, timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order.get("id"),
                    order.get("symbol"),
                    order.get("type"),
                    order.get("side"),
                    order.get("price"),
                    order.get("amount"),
                    order.get("filled", 0),
                    order.get("status"),
                    order.get("fee", 0),
                    order.get("fee_currency"),
                    order.get("timestamp"),
                    json.dumps(order.get("metadata", {}))
                ))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to save order: {e}")
                return False
    
    async def get_order(self, order_id: str) -> Optional[Dict]:
        """주문 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM order_history WHERE order_id = ?",
                (order_id,)
            )
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    
    async def get_open_orders(self) -> List[Dict]:
        """미체결 주문 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM order_history 
                WHERE status = 'open' OR status = 'pending'
                ORDER BY timestamp DESC
            """)
            rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
    
    async def get_orders_by_symbol(self, symbol: str, limit: int = 100) -> List[Dict]:
        """심볼별 주문 이력"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM order_history 
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol, limit))
            rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
    
    # =====================================================================
    # Balance Methods
    # =====================================================================
    
    async def save_balance(
        self,
        currency: str,
        total: float,
        free: float,
        used: float,
        note: Optional[str] = None
    ) -> bool:
        """잔고 저장"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT INTO balance_history 
                    (currency, total, free, used, timestamp, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (currency, total, free, used, datetime.now().isoformat(), note))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to save balance: {e}")
                return False
    
    async def get_latest_balance(self, currency: str) -> Optional[Dict]:
        """최근 잔고 조회"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM balance_history 
                WHERE currency = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (currency,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    
    # =====================================================================
    # Trade Log Methods
    # =====================================================================
    
    async def log_trade(
        self,
        symbol: str,
        action: str,
        quantity: float,
        price: float,
        pnl: Optional[float] = None,
        strategy: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """트레이드 로그"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT INTO trade_log 
                    (symbol, action, quantity, price, pnl, timestamp, strategy, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, action, quantity, price, pnl,
                    datetime.now().isoformat(), strategy,
                    json.dumps(metadata) if metadata else None
                ))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to log trade: {e}")
                return False
    
    async def get_trade_history(
        self,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """트레이드 히스토리"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if symbol:
                cursor.execute("""
                    SELECT * FROM trade_log 
                    WHERE symbol = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (symbol, limit))
            else:
                cursor.execute("""
                    SELECT * FROM trade_log 
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
            
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        d["metadata"] = {}
                result.append(d)
            return result
    
    # =====================================================================
    # Recovery Methods
    # =====================================================================
    
    async def load_positions_for_recovery(self) -> List[Dict]:
        """재시작 시 포지션 복구"""
        positions = await self.get_all_positions()
        logger.info(f"Loaded {len(positions)} positions for recovery")
        return positions
    
    async def load_open_orders_for_recovery(self) -> List[Dict]:
        """재시작 시 미체결 주문 복구"""
        orders = await self.get_open_orders()
        logger.info(f"Loaded {len(orders)} open orders for recovery")
        return orders
    
    # =====================================================================
    # Stats Methods
    # =====================================================================
    
    async def get_stats(self) -> Dict:
        """통계 반환"""
        async with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            stats = {}
            
            # 포지션 수
            cursor.execute("SELECT COUNT(*) as count FROM positions WHERE status = 'open'")
            stats["open_positions"] = cursor.fetchone()["count"]
            
            # 주문 수
            cursor.execute("SELECT COUNT(*) as count FROM order_history")
            stats["total_orders"] = cursor.fetchone()["count"]
            
            # 트레이드 수
            cursor.execute("SELECT COUNT(*) as count FROM trade_log")
            stats["total_trades"] = cursor.fetchone()["count"]
            
            # 총 손익
            cursor.execute("SELECT SUM(pnl) as total_pnl FROM trade_log WHERE pnl IS NOT NULL")
            stats["total_pnl"] = cursor.fetchone()["total_pnl"] or 0
            
            return stats
    
    def close(self) -> None:
        """연결 종료"""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
