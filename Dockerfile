# =============================================================================
# AutoCoinTrade - Dockerfile
# =============================================================================
# 24시간 연속 운용을 위한 Docker 설정

FROM python:3.11-slim

# 환경 설정
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 작업 디렉토리
WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 코드 복사
COPY src/ ./src/
COPY config/ ./config/

# 데이터 디렉토리 생성
RUN mkdir -p /app/data /app/logs

# 헬스체크 (단순 프로세스 체크)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# 포트
EXPOSE 8000

# 사용자 설정 (보안)
RUN useradd -m -u 1000 trader && \
    chown -R trader:trader /app
USER trader

# 실행
CMD ["python", "-m", "src.main"]
