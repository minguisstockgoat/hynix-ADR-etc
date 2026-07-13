# SK하이닉스 본주 ⇄ ADR 괴리율 대시보드

SK하이닉스 **본주(KOSPI 000660)** 와 **나스닥 ADR(SKHYV)** 의 가격 괴리율(프리미엄/디스카운트)을
GitHub Pages에서 준실시간으로 보여주는 정적 대시보드입니다.

- **본주**: `000660` — **네이버 금융에서 KRX + NXT 통합** 수집. 정규장(09:00~15:30)엔 KRX 실시간가,
  장 시작 전(08:00~) 및 마감 후(~20:00)엔 **NXT 시간외가**를 유효가로 사용. (네이버 실패 시 Yahoo `000660.KS` 폴백)
- **ADR**: `SKHYV` (USD, Nasdaq NGM) — OTC 티커 `HXSCL` 은 Yahoo에서 조회되지 않아 나스닥 라인을 사용
- **비율**: 1 ADR = 보통주 **0.1주** (10 ADR = 1주). UI에서 조정 가능
- **환율**: `USD/KRW` — 페이지에서 **분단위 실시간**(Yahoo `KRW=X`, 프록시 경유) 우선, 실패 시 fxratesapi→er-api→스냅샷 폴백

## 괴리율 정의

```
ADR 이론가(USD) = 본주(KRW) × 비율 ÷ (USD/KRW)
괴리율(%)       = (ADR 실제가 − ADR 이론가) ÷ ADR 이론가 × 100
```

- 양(+) → ADR이 본주 대비 **프리미엄** (한국식 빨강)
- 음(−) → ADR이 본주 대비 **디스카운트** (한국식 파랑)

## 동작 구조 (정적 호스팅에서 시세를 얻는 법)

브라우저에서 한국 주식·미국 OTC/ADR 시세를 직접 받으면 **CORS 차단**에 막힙니다(테스트로 확인).
그래서 서버사이드 파이프라인을 둡니다.

```
GitHub Actions(약 5분 주기)
  └─ scripts/fetch_data.py  →  Yahoo Finance에서 3개 시세 수집·괴리율 계산
       └─ data.json 갱신·커밋
            └─ index.html 이 same-origin으로 data.json 로드 (CORS 문제 없음)
                 └─ 페이지는 60초마다 최신 스냅샷 재조회 + 일 기준환율 참고 표시
```

> ⚠️ **왜 "실시간"이 아니라 "준실시간"인가**
> - 무료 정적 호스팅에는 서버·웹소켓이 없어 초 단위 스트리밍이 불가능합니다.
> - GitHub 스케줄은 최소 5분이며 부하 시 더 지연됩니다 → 약 **5분 지연**의 준실시간.
> - **KOSPI와 미국장은 시간대가 겹치지 않습니다.** 어느 순간이든 한쪽 시세는 직전 장 종가이며,
>   각 값의 시각·장상태를 카드에 표시합니다.

## 배포 방법 (GitHub Pages)

> git 저장소·초기 커밋·`origin` 원격(**토큰 없음**)은 이미 준비돼 있습니다.
> 🔐 **토큰은 어떤 파일에도 저장하지 말고, 아래 push 단계에서 비밀번호로 직접 입력**하세요.
> (Windows 자격증명 관리자에 암호화 저장되고 `.git/config` 등 파일에는 남지 않습니다.)

1. **GitHub에서 빈 저장소 생성** — `minguisstockgoat/hynix-adr-dashboard`, **Public 권장**
   (Public이면 Actions 무제한·Pages 무료). README·.gitignore 등 초기화 파일은 **추가하지 마세요**.

2. **푸시** (이 폴더에서):
   ```bash
   git push -u origin main
   ```
   - Username: `minguisstockgoat`
   - Password: **Fine-grained PAT 붙여넣기** (계정 비밀번호 아님)
   - 필요한 PAT 권한: 대상 저장소에 **Contents: Read and write**, **Workflows: Read and write**

3. **Pages 활성화** — Settings → Pages → Source `Deploy from a branch`,
   Branch `main` / `/(root)` → Save → `https://minguisstockgoat.github.io/hynix-adr-dashboard/`

4. **Actions 쓰기 권한** — Settings → Actions → General → Workflow permissions →
   `Read and write permissions` → Save (워크플로우가 `data.json`을 커밋하려면 필요)

5. **첫 수집 실행** — Actions 탭 → `update-data` → `Run workflow` 로 1회 수동 실행.
   이후 스케줄에 따라 자동 갱신됩니다.

## 커스터마이즈

- **종목/환율 심볼·비율**: [`scripts/fetch_data.py`](scripts/fetch_data.py) 상단 `COMMON_SYM`, `ADR_SYM`, `FX_SYM`, `RATIO`
- **비율 즉석 변경**: 대시보드의 "ADR 비율" 카드에서 값 입력 (계산 즉시 반영)
- **수집 주기**: [`.github/workflows/update.yml`](.github/workflows/update.yml) 의 `cron`
  (예: 부담되면 `*/10`, `*/15` 로 완화)
- **히스토리 보관량**: `fetch_data.py` 의 `MAX_HISTORY` (기본 3000포인트)

## 로컬 실행/테스트

```bash
python scripts/fetch_data.py         # data.json 생성/갱신
python -m http.server 8123           # http://localhost:8123 에서 확인 (file:// 는 CORS로 data.json 로드 불가)
```

## 알아둘 점

- Yahoo Finance 비공식 엔드포인트를 사용하므로 심볼 변경·레이트리밋 가능성이 있습니다.
  실패 시 직전 `data.json` 값을 재사용하고 카드에 `재사용`으로 표시합니다.
- 저장소가 **60일간 무활동**이면 GitHub이 스케줄 워크플로우를 자동 비활성화합니다(재활성화 필요).
- 본 페이지는 **정보 제공용**이며 투자 자문이 아닙니다. 시세는 지연·오차가 있을 수 있습니다.
