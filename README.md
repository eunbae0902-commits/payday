# PayDay Fund Bot

$500으로 시작해 $10,000을 목표로 **Robinhood Crypto 공식 API**에서 스스로 매매하는 자동 펀드매니저입니다.

> 돈은 대표님 계좌에 그대로 있습니다. 봇은 대표님이 발급한 API 키로만 주문을 넣고, 출금 권한은 없습니다.

## 구조

| 모듈 | 역할 |
|---|---|
| `fundbot/robinhood.py` | 공식 Crypto Trading API 클라이언트 (Ed25519 서명, 시세·잔고·시장가 주문) |
| `fundbot/strategy.py` | 추세추종: EMA 24/72 크로스 + EMA 200 추세 필터 + ATR 트레일링 스톱 |
| `fundbot/engine.py` | 운용 엔진: tick마다 시세 기록 → 리스크 점검 → 매수/매도 |
| `fundbot/broker.py` | `PaperBroker`(모의) / `LiveBroker`(실거래) |
| `fundbot/backtest.py` | 같은 엔진으로 과거 데이터 백테스트 |
| `fundbot/odds.py` | 목표 달성 확률 몬테카를로 시뮬레이션 |

## 리스크 안전장치 (기본값)

1. **1회 손실 한도 2%**: 손절선에 걸려도 자산의 2%만 잃도록 수량을 계산합니다.
2. **종목 비중 상한 60%**: 한 종목에 몰빵하지 않습니다.
3. **최대낙폭 -30% 정지**: 고점 대비 30% 빠지면 전량 매도하고 멈춥니다. `reset` 명령 전까지 다시 시작하지 않습니다.
4. **일일 손실 -6%**: 그날은 신규 진입을 하지 않습니다.
5. **목표 달성 시 정지**: 자산이 $10,000에 닿으면 전량 매도해 수익을 확정합니다.
6. **레버리지·선물 없음**: 현물 시장가 주문만 넣습니다. 대표님의 2025년 12월 원칙(선물 중단)과 같은 방향입니다.
7. **기본 모드는 paper**: live 모드는 `--i-understand-the-risk` 플래그를 붙여야만 실행됩니다.

## 시작 순서

```bash
pip install -r requirements.txt
cp config.example.toml config.toml

# 1) Robinhood 앱 → Settings → Crypto → API Trading에서 키 발급
#    (Ed25519 공개키를 등록하고, 발급된 API 키와 base64 개인키를 환경변수로)
export RH_API_KEY="rh-api-..."
export RH_PRIVATE_KEY="base64-private-key"

# 2) 모의 운용 (실제 시세, 가상 체결). 최소 4주 권장
python -m fundbot run --interval 300

# 3) 상태 확인
python -m fundbot status

# 4) 검증 후 실거래: config.toml에서 mode = "live"
python -m fundbot run --i-understand-the-risk
```

서버나 NAS에서 상시 실행하는 것을 권장합니다. cron으로 돌릴 때는 `run --once`를 5분마다 실행하면 됩니다.

### 백테스트 / 확률 시뮬레이션

```bash
python -m fundbot backtest prices.csv          # 열: timestamp,symbol,close (시간봉)
python -m fundbot odds --ret 0.30 --vol 0.60 --years 5
```

## $500 → $10,000(20배)의 현실

`odds` 시뮬레이션 결과입니다(1만 회, $500 시작).

| 전략 성격 | 연수익 / 변동성 | 낙폭 정지 | 10년 내 목표 달성 확률 | 달성 시 소요 |
|---|---|---|---|---|
| 공격형 (코인 풀베팅 수준) | 60% / 90% | -50% | **5%** | 약 2년 |
| 중립형 | 30% / 60% | -30% | **0.3%** | 약 2.6년 |
| 이 봇의 설계 목표 | 25% / 30% | -50% | **19%** | 약 8.5년 |
| 매우 우수한 추세추종 | 40% / 35% | -50% | **62%** | 약 7년 |

핵심은 세 가지입니다.
- 20배를 빠르게 노리면(변동성 확대) 달성 확률이 오히려 **떨어집니다**. 대부분 중간에 큰 낙폭을 맞기 때문입니다.
- 현실적인 경로는 **변동성을 억제하면서 5~8년 복리**를 쌓는 것입니다.
- 위 수치는 기하 브라운 운동을 가정한 모델값입니다. 수익을 보장하지 않습니다.

## 테스트

```bash
python -m pytest -q
```
