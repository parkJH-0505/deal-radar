# deal-radar

설립 5~6년 이하, 외부투자 없이 조용히 잘 크는 국내 회사를 **공공 행정데이터**로 매일 포착하는 딜소싱 레이더.

언론·투자 DB에는 흔적이 없어도, 돈을 벌면 행정기록에는 반드시 흔적이 남는다는 전제에서 출발한다.

| 흔적 | 소스 | 의미 |
|---|---|---|
| 처음 감사보고서 제출 | OpenDART (외부감사 공시, 비상장) | 자산·매출·인원 기준을 넘음 → 대략 100억 고개 |
| 신규 제조 인허가 (기존 사업자) | 행안부 인허가 조회서비스 | 공장 증설 |
| 가입자 수·고지금액 추이 | 국민연금 월간 전체 CSV (2023.6~ 매월) | 젊은 법인의 12개월 고용 성장, 1인당 보수 수준 |

## 하루 흐름

```
05:07 KST  GitHub Actions (daily.yml)   수집 → 회사 묶기 → 규칙 점수 → data/ 커밋
07:30 KST  Claude 예약 작업               data/candidates/latest.json 판단·반박 검증
08:30 KST  전달                           Notion 딜 레이더 DB + 아침 다이제스트
```

## 구조

```
config/theses.yaml        판별 기준·가중치 (피드백 반영해 계속 수정)
radar/sources/dart.py     첫 외감 탐지, 회사정보, 감사보고서 손익 추출(추정)
radar/sources/localdata.py 인허가 증설·다거점 신호
radar/sources/nps_monthly.py 국민연금 월간 CSV 자동 수집 → 젊은 법인 고용 패널·스캔
radar/sources/nps.py      국민연금 API (최신 월만 제공 → 패널 밖 후보 보강용)
data/nps/months/          월별 젊은 법인 집계 (YYYYMM.tsv.gz)
radar/score.py            점수 + 사유 문자열
radar/pipeline.py         일일 오케스트레이션
data/candidates/          날짜별 후보 (latest.json = 판단 대기열)
data/watchlist.json       한 번이라도 잡힌 회사의 점수 이력
data/runs/                실행 로그 (사람이 읽는 용도)
data/state/               DART 감사 이력 인덱스, 조회 캐시
```

## 설정

Repository → Settings → Secrets and variables → Actions

- `DATA_GO_KR_KEY` 공공데이터포털 일반 인증키 (국민연금, 식품제조가공업 활용신청 필요)
- `DART_KEY` OpenDART 인증키

## 실행

- `probe` 워크플로우: 각 API 응답 구조를 키 마스킹 상태로 출력 (필드명 실측용)
- `daily` 워크플로우: 매일 자동 실행, 수동 실행 시 날짜 지정 가능
- 로컬: `python -m radar daily --date 2026-11-05`, 테스트: `pytest -q`

## 한계 (의도적으로 명시)

- 감사보고서 손익은 정규식 추출이라 `confidence: low`. 판단 단계에서 원문으로 재확인한다.
- 인허가에는 사업자번호가 없어 상호로 묶는다. 동명이사는 판단 단계에서 걸러낸다.
- 국민연금은 사업자번호 앞 6자리만 공개, 3인 이상 법인 위주. 회사 단위는 '앞 6자리 + 정규화 상호'로 묶는다.
- 국민연금 적용일은 설립일의 대리지표다 (기존 회사가 새로 가입하면 젊어 보인다 → DART·판단 단계에서 교차 확인).
- 건설현장 임시 사업장('/일용/', '신축공사 중' 등)은 자동 제외한다.
- 외부투자 이력은 공공데이터에 없다. 판단 단계에서 웹으로 확인한다.
