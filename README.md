# AutoCoinTrade - 실시간 자동 코인 트레이딩 봇

24시간 연속 구동이 가능한 실시간 자동 코인 트레이딩 봇입니다. WebSocket 기반 실시간 시세 수집, 기술적 지표 기반 신호 생성, 리스크 관리, 장애 복구 기능을 제공합니다.

## 🚀 주요 기능

### 실시간 데이터 수집
- **WebSocket** 스트림을 통한 실시간 호가/체결 데이터 수신
- **REST API 폴백** - WebSocket 끊김 시 자동 전환
- **거래소 지원**: Binance, Upbit (확장 가능)

### 기술적 분석
- **RSI** (Relative Strength Index)
- **MACD** (Moving Average Convergence Divergence)
- **볼린저밴드** (Bollinger Bands)
- **이동평균선** (SMA, EMA)

### 시장 스캐너 (동적 심볼)
- **급등락 코인 자동 탐지** - 고정 심볼 대신 시장 전체 스캔
- 24h 변동률·거래량 기반 상위 N개 선정
- `config.yaml`의 `scanner.enabled: true`로 활성화

### 거래 전략
- RSI 기반 매수/매도 신호
- MACD 골든/데드크로스 신호
- 볼린저밴드 터치 신호
- **단타(Scalping)** - 빠른 RSI(7) + 짧은 MACD(6,13,5), RSI+MACD 동시 신호 시 진입
- 복합 전략 (다중 지표 조합)

### 리스크 관리
- 손절/익절 자동 실행
- 최대 동시 포지션 제한
- 일일 손실 한도
- 잔고 최소 유지 비율

### 24시간 연속 운용
- **Docker** 컨테이너 지원
- **systemd** 서비스 지원
- 장애 시 자동 재연결 (지수 백오프)
- Graceful Shutdown 지원

## 📁 프로젝트 구조

```
autoCoinTrade/
├── config/
│   ├── config.yaml          # 메인 설정 파일
│   └── .env.example         # 환경변수 템플릿
├── src/
│   ├── data/                # 데이터 수집
│   │   ├── collector.py     # 데이터 수집기
│   │   ├── websocket_client.py
│   │   └── normalizer.py
│   ├── analysis/            # 기술적 분석
│   │   ├── indicators.py    # 지표 계산
│   │   └── signal_generator.py  # 신호 생성
│   ├── execution/           # 주문 실행
│   │   ├── order_manager.py
│   │   ├── exchange_adapter.py
│   │   └── risk_manager.py
│   ├── storage/             # 데이터 저장
│   │   ├── timeseries.py
│   │   └── position_store.py
│   ├── monitoring/          # 모니터링
│   │   ├── logger.py
│   │   ├── health_check.py
│   │   └── notifier.py
│   ├── scanner/           # 시장 스캐너 (동적 심볼)
│   │   └── market_scanner.py
│   ├── main.py             # 메인 진입점
│   └── web/                # 웹 UI (로컬 전용)
│       ├── app.py
│       ├── routes.py
│       └── static/
├── systemd/
│   └── autocointrade.service
├── tests/
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## 🛠️ 설치 및 설정

### 1. 환경 요구사항

- Python 3.11+
- pip

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정

```bash
# .env.example을 .env로 복사
cp config/.env.example config/.env

# 실제 값 입력
# 거래소 API 키
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret

# Telegram 알림 (선택)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. 설정 파일 편집

`config/config.yaml` 파일을 수정하여 거래소, 전략, 리스크 설정을 조정합니다.

```yaml
exchange:
  name: binance
  testnet: true  # 실제 거래 시 false로 변경
  symbols:
    - BTC/USDT
    - ETH/USDT

strategies:
  enabled:
    - rsi
    - macd
    - scalping
  default: rsi          # 단타: scalping
  scalping:             # 단타 전략 설정
    rsi:
      period: 7
      oversold: 25
      overbought: 75
    macd:
      fast_period: 6
      slow_period: 13
      signal_period: 5
    require_both: true  # RSI+MACD 동시 신호 시에만 진입

risk_management:
  stop_loss_percent: -5.0
  take_profit_percent: 10.0
  max_positions: 3
```

## 🚀 실행

### 웹 UI로 실행 (로컬 전용)

봇 상태를 웹에서 확인하고 제어할 수 있습니다. `127.0.0.1`에서만 접속 가능합니다.

```bash
python -m src.web
# 또는 포트 지정
python -m src.web --port 8080
```

브라우저에서 **http://127.0.0.1:8080** 접속 후 다음을 확인할 수 있습니다:
- 봇 상태 (실행 중/중지)
- 실시간 가격
- 포지션
- 최근 거래 내역
- 잔고/통계

### 시뮬레이션 모드 (권장)

```bash
# DRY_RUN=true로 실행
python -m src.main
```

### 실제 거래 모드

```yaml
# config.yaml에서 설정
mode:
  dry_run: false  # 실제 주문 실행
```

### Docker로 실행

```bash
# 빌드
docker build -t autocointrade .

# 실행
docker-compose up -d

# 로그 확인
docker logs -f autocointrade
```

### systemd로 실행 (Linux)

```bash
# 서비스 파일 설치
sudo cp systemd/autocointrade.service /etc/systemd/system/

# 활성화
sudo systemctl daemon-reload
sudo systemctl enable autocointrade
sudo systemctl start autocointrade

# 상태 확인
sudo systemctl status autocointrade
```

## ⚙️ 설정 가이드

### 거래소 선택

```yaml
exchange:
  name: binance    # binance, upbit, bithumb
  testnet: true    # 테스트넷 사용 여부
```

### 바이낸스 선물 모드 (숏 포지션 + 레버리지)

바이낸스만 지원. `mode: futures`로 설정 시 USDT 선물 거래, 숏 포지션, 레버리지 사용 가능.

```yaml
exchange:
  name: binance
  mode: futures    # spot(현물) | futures(USDT선물)
  leverage: 10     # 레버리지 1~125 (futures일 때만)
  symbols:
    - BTC/USDT
    - ETH/USDT
```

- **롱**: BUY 신호 시 매수
- **숏**: SELL 신호 시 매도(숏 진입), BUY 신호 시 매수(숏 청산)

### 전략 선택

```yaml
strategies:
  default: rsi     # rsi, macd, bollinger, scalping, combined
```

### 단타(Scalping) 모드

단타 전략은 빠른 RSI(7)와 짧은 MACD(6,13,5)를 사용하며, 두 지표가 동시에 신호할 때만 진입합니다.

```yaml
strategies:
  default: scalping
  cooldown_minutes: 1    # 단타는 쿨다운 짧게 권장
  scalping:
    rsi:
      period: 7
      oversold: 25
      overbought: 75
    macd:
      fast_period: 6
      slow_period: 13
      signal_period: 5
    require_both: true   # false면 RSI 또는 MACD 하나만 있어도 진입

risk_management:
  stop_loss_percent: -1.0    # 단타: 좁은 손절
  take_profit_percent: 1.5   # 단타: 작은 익절
```

AI 하이브리드와 함께 사용 시 `strategies.default: scalping`으로 설정하면 규칙 기반으로 단타 신호를 생성하고 AI가 검증합니다.

### 시장 스캐너 (동적 심볼)

`scanner.enabled: true`로 설정하면 config의 고정 심볼 대신 **시장 전체를 스캔**하여 급등락 코인을 자동 선정합니다.

```yaml
scanner:
  enabled: true           # 동적 스캔 활성화
  scan_interval: 300      # 스캔 간격 (초), 5분 권장
  max_symbols: 15         # 선정할 최대 심볼 수
  min_change_percent: 2.0 # 최소 24h 변동률 (%)
  sort_by: change_abs     # change_abs(급등락), change_up(상승), volume(거래량)
  quote: KRW              # 업비트: KRW, 바이낸스: USDT
```

스캐너 모드에서는 WebSocket 대신 REST로 시세를 수집합니다 (동적 심볼 변경에 대응).

### 리스크 관리

```yaml
risk_management:
  stop_loss_percent: -5.0     # 손절 (%)
  take_profit_percent: 10.0   # 익절 (%)
  max_positions: 3            # 최대 동시 포지션
  max_daily_loss_percent: -3.0 # 일일 최대 손실 (%)
  position_size_percent: 10    # 1회 주문 금액 (%)
```

### 알림 설정

```yaml
monitoring:
  notifications:
    telegram:
      enabled: true
      bot_token: "YOUR_BOT_TOKEN"
      chat_id: "YOUR_CHAT_ID"
    slack:
      enabled: false
      webhook_url: "YOUR_WEBHOOK_URL"
```

## 📊 모니터링

### 로그 확인

```bash
# Docker
docker logs -f autocointrade

# systemd
journalctl -u autocointrade -f

# 파일
tail -f logs/trading.log
```

### 헬스체크

기본 제공 헬스체크가 자동으로 프로세스 상태를 모니터링합니다.
연속 3회 실패 시 자동 재시작됩니다.

## 🔧 문제 해결

### WebSocket 연결 실패
- 네트워크 연결 확인
- 거래소 API 제한 확인
- 테스트넷 사용 시 testnet URL 확인

### 주문이 실행되지 않음
- 잔고 확인 (`dry_run` 모드 확인)
- API 키 권한 확인 (주문 권한 필요)
- 일일 손실 한도 도달 여부 확인

### 데이터 누락
- 시계열 DB (`data/timeseries.db`) 확인
- 디스크 공간 확인

## ⚠️ 주의사항

1. **테스트넷 우선**: 실제 자금을 사용하기 전 반드시 테스트넷에서 충분히 테스트하세요.
2. **리스크 관리**: 처음에는 conservative한 설정(`dry_run: true`, 작은 `position_size_percent`)으로 시작하세요.
3. **API 키 보안**: API 키를 코드에 하드코딩하지 마세요. 환경변수만 사용하세요.
4. **모니터링**: 정기적으로 로그와 잔고를 확인하세요.

## 📝 라이선스

MIT License

## 🤝 기여

Pull Request 환영합니다. 중대한 변경 전에 Issue에서 논의해 주세요.
