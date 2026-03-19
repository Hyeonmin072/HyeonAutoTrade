"""
로깅 설정 모듈
로테이션, 구조화 로깅, 다중 출력 핸들러 제공
"""
import sys
import logging
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler
from pythonjsonlogger import jsonlogger

from loguru import logger


class JsonFormatter(jsonlogger.JsonFormatter):
    """JSON 포맷 로거"""
    
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["timestamp"] = self.formatTime(record)
        log_record.pop("levelname", None)
        log_record.pop("name", None)


def setup_logger(
    name: str = "autocointrade",
    log_file: Optional[str] = None,
    log_level: str = "INFO",
    max_size_mb: int = 100,
    backup_count: int = 5,
    json_format: bool = False
) -> logging.Logger:
    """
    로거 설정 함수
    
    Args:
        name: 로거 이름
        log_file: 로그 파일 경로 (None이면 파일 로그 비활성화)
        log_level: 로그 레벨
        max_size_mb: 최대 로그 파일 크기 (MB)
        backup_count: 백업 파일 수
        json_format: JSON 포맷 사용 여부
    
    Returns:
        설정된 로거 인스턴스
    """
    # Loguru 기본 설정 제거
    logger.remove()
    
    # 표준 출력 포맷
    stdout_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    
    # 콘솔 핸들러 추가
    logger.add(
        sys.stdout,
        format=stdout_format,
        level=log_level,
        colorize=True,
        backtrace=True,
        diagnose=True
    )
    
    # 파일 핸들러 추가
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        if json_format:
            # JSON 파일 포맷
            file_format = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}"
            logger.add(
                log_path,
                format=file_format,
                level=log_level,
                rotation=f"{max_size_mb} MB",
                retention=backup_count,
                compression="zip",
                serialize=True
            )
        else:
            # 일반 텍스트 포맷
            file_format = (
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
                "{level: <8} | "
                "{name}:{function}:{line} | "
                "{message}"
            )
            logger.add(
                log_path,
                format=file_format,
                level=log_level,
                rotation=f"{max_size_mb} MB",
                retention=backup_count,
                compression="zip",
                enqueue=True  # 멀티프로세스 안전
            )
    
    # 표준 로깅과의 통합을 위한 Logger 어댑터 반환
    return logger


def get_logger(name: str) -> logger:
    """
    로거 인스턴스获取 함수
    
    Args:
        name: 로거 이름
    
    Returns:
        Loguru logger 인스턴스
    """
    return logger.bind(name=name)


class LoggerMixin:
    """로깅 기능이 필요한 클래스를 위한 Mixin"""
    
    @property
    def logger(self):
        """클래스 이름으로 바인딩된 로거 반환"""
        return get_logger(self.__class__.__module__ + "." + self.__class__.__name__)


def setup_prometheus_logging() -> None:
    """Prometheus 메트릭과 연동하기 위한 로그 핸들러 설정"""
    # 필요시 Prometheus 로그 핸들러 추가
    pass
