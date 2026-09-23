# DR_to_Insta

DR(PersonalDailyReport, 개인 데일리 브리핑)이 매일 만드는 **AI 뉴스**를 한국어 카드뉴스로 바꿔
인스타그램에 **하루 4회(KST 07/12/18/23시)** 자동 게시하는 파이프라인.
구조는 [AInstagram](../AInstagram)을 따른다: GitHub Actions cron + 저장소에 커밋하는 SQLite + R2 + Graph API.
AInstagram과 달리 **검수 없이 자동 게시**하고, 결과만 텔레그램으로 알린다.

## 전체 흐름

```
[DR, 매일 08시대 KST] AI 뉴스 6건 생성 -> Neon DB (BriefingSection.AI_NEWS)
                                   │ 읽기 전용 조회
[GitHub Actions, 슬롯 20분 전 기동] ▼
 1. 후보: 최근 2일치 DR 뉴스 중 아직 처리 안 한 것, 최신 배치부터
 2. 선별: LLM이 AI 관련성 / 최근 게시와 같은 소식인지 / 임팩트(1~10)를 평가 -> 임팩트 순
         + 임베딩 유사도로 최근 게시물과 한 번 더 중복 확인
 3. 폴백: 후보가 없으면 (DR 실패, DB 장애, 전부 사용/중복) 실시간 웹 검색으로
         가장 임팩트 있는 AI 뉴스를 찾아 같은 형식으로 정리
 4. 생성: 한국어 카드뉴스 문구(커버 제목 + 본문 슬라이드 2~5장) + 본문(제목/기사/출처/태그)
         -> AI 배경 + 템플릿 합성 -> R2 업로드
 5. 정각까지 대기 -> 인스타 캐러셀 발행 -> 이력 저장 -> 텔레그램 알림
```

### 슬롯과 DR 배치

DR은 매일 08:00~08:59 KST 사이에 돈다 (Vercel Hobby cron은 시 단위 정밀도). 그래서 D일 DR 뉴스는
**D일 12/18/23시 + D+1일 07시**에 쓰인다. 임팩트가 높은 뉴스부터 쓰므로 좋은 뉴스가 낮 시간대에 먼저 나간다.
DR이 늦게 돈 날(예: 13시)에도 다음 슬롯부터 자동으로 새 배치를 쓴다 - 미리 만들어 쌓지 않고 슬롯 시점에
생성하기 때문.

### 중복 게시 방지

- **슬롯 단위**: 슬롯 키(`2026-09-24T12:00`)로 이미 게시했으면 건너뜀 -> 워크플로우가 두 번 돌아도 한 번만 게시.
- **뉴스 단위**: 게시했거나, 부적합으로 건너뛰었거나, 실패 한도(2회)를 넘긴 뉴스는 `news_log`에 남아 다시 후보가 안 됨.
- **소식 단위**: DR은 같은 소식을 며칠에 걸쳐 다시 다루는 일이 있어서 (예: SK하이닉스-인텔 협력설 5일간 3회),
  최근 10일 게시 제목과 LLM이 의미 비교 + 임베딩 유사도 0.85 이상이면 건너뜀.
- `media_publish` 단계 실패는 실제로는 게시됐을 수 있어서 같은 뉴스로 재시도하지 않는다 (AInstagram에서 겪은 문제).

## 설정 (한 번만)

### 1. 새 인스타그램 계정 토큰

AInstagram과 같은 Meta 앱을 그대로 쓰면 된다.
1. 새 계정을 Business 또는 Creator 계정으로 전환
2. https://developers.facebook.com 의 기존 앱 > Instagram > **API setup with Instagram Login**
3. **Generate access tokens** > **Add an Instagram Account** -> 새 계정으로 로그인/승인 -> 토큰 복사
4. `https://graph.instagram.com/me?fields=id,username&access_token=<토큰>` 으로 계정 ID 확인
5. `IG_ACCESS_TOKEN`, `IG_BUSINESS_ACCOUNT_ID`에 등록, `config/config.yaml`의 `instagram.account_name`에 핸들 기입

> 장기 토큰은 60일마다 만료된다. 만료되면 게시가 실패하고 텔레그램으로 "토큰/권한 문제" 알림이 온다.

### 2. DR DB 읽기 전용 계정 (권장)

이 서비스는 DR DB에 **읽기만** 한다 (코드에서도 read-only 트랜잭션으로 연결). DR의 소유자 계정 URL을
그대로 써도 동작하지만, 공개 저장소의 Actions에 넣는 값이므로 SELECT 권한만 있는 role을 따로 만드는 걸 권장한다.
Neon 콘솔의 Roles 메뉴로 만든 role은 `neon_superuser` 권한을 받으므로, **SQL Editor에서 SQL로** 만든다:

```sql
-- 비밀번호는 충분히 길게 (Neon은 SQL로 만드는 role에 강한 비밀번호를 요구함, 예: openssl rand -base64 24)
CREATE ROLE drinsta_reader WITH LOGIN PASSWORD '<긴 랜덤 비밀번호>';
GRANT USAGE ON SCHEMA public TO drinsta_reader;
GRANT SELECT ON "BriefingRun", "BriefingSection" TO drinsta_reader;
```

기존 `DATABASE_URL`에서 사용자/비밀번호만 `drinsta_reader`/새 비밀번호로 바꾼 값을 `DR_DATABASE_URL`로 쓴다.

### 3. R2 / Telegram / OpenAI

AInstagram 값을 그대로 재사용하면 된다. R2 키는 `config.yaml`의 `image.storage_prefix`(`dr-insta/`) 아래로
분리되고, 텔레그램 알림은 `[계정명]` 머리말로 구분된다. 텔레그램 값이 없으면 알림 없이 동작한다.

### 4. GitHub Secrets

```bash
gh secret set OPENAI_API_KEY
gh secret set DR_DATABASE_URL
gh secret set IG_BUSINESS_ACCOUNT_ID
gh secret set IG_ACCESS_TOKEN
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
gh secret set R2_ACCOUNT_ID
gh secret set R2_ACCESS_KEY_ID
gh secret set R2_SECRET_ACCESS_KEY
gh secret set R2_BUCKET_NAME
gh secret set R2_PUBLIC_BASE_URL
```

등록 후 Actions 탭 > "슬롯 게시" > Run workflow에서 `dry_run`을 켜고 먼저 돌려보면, 생성된 이미지/캡션이
실행 결과의 `preview` 아티팩트로 올라온다.

## 로컬 개발

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # 값 채우기
pytest -q
```

게시하지 않고 결과만 보기 (인스타/R2/DB/텔레그램 반영 없음, `data/preview/<슬롯>/`에 이미지·캡션·선별 근거 저장):

```bash
python scripts/run_slot.py --dry-run --now                                  # 실제 DR 데이터로
python scripts/run_slot.py --dry-run --now --plain-images                   # 이미지 비용 없이 문구/레이아웃만
python scripts/run_slot.py --dry-run --now --force-fallback --plain-images  # 실시간 검색 폴백 경로
```

`meta.json`에 어떤 뉴스가 몇 점으로 평가됐고 무엇이 왜 건너뛰어졌는지 남는다 - 선별 기준을 조정할 때 참고.

## 커스터마이징 ([config/config.yaml](config/config.yaml))

| 항목 | 설명 |
|---|---|
| `posting.times` | 게시 시각. 바꾸면 `.github/workflows/publish.yml`의 cron도 수정 (KST는 서머타임이 없어서 한 번 맞추면 계속 맞음) |
| `posting.body_slides` | 커버 뒤 본문 슬라이드 수 범위 |
| `source.min_impact` | 이 점수 미만 DR 뉴스는 게시하지 않음 (폴백 뉴스에는 적용 안 함) |
| `source.dr_lookback_days` | 며칠 전 DR 배치까지 후보로 볼지 |
| `dedup.*` | 중복 판단 기간/임베딩 임계값 |
| `content.fixed_hashtags` | 매 게시물 공통 해시태그 (뉴스별 태그 뒤에 붙음) |
| `models.*` | 작성/선별/검색/이미지 모델 |
| `image.brand.primary_color` | 포인트 색 (AInstagram과 구분되게 주황) |

톤/독자층은 [prompts.py](src/drinsta/content/prompts.py)의 `WRITER_SYSTEM`에서 바꾼다.
현재 기준: AI 관심층(직장인·학생·개발자), 전문용어는 첫 등장 시 풀어쓰기, 슬라이드는 평서문, 본문은 '~습니다'체,
DR에 없는 사실은 추가 금지.

## DR과의 계약

DR의 `BriefingSection(sectionType=AI_NEWS).contentJson`을 직접 읽는다
([dr_source.py](src/drinsta/sources/dr_source.py)).

- 사용 필드: `title`, `summary`, `whyItMatters`, `researcherView`, `keyFacts`, `sourceName`, `sourceUrl`, `publishedDate`
- `engineerView`는 DR 사용자 개인을 향한 조언이라 쓰지 않는다.
- `keyFacts`/`source*`는 2026-09-24에 DR에 추가된 필드라 그 전 데이터엔 없다 (없으면 출처 표기 없이 게시).
- DR 쪽 JSON 구조가 바뀌어 필수 필드(`title`/`summary`)를 못 읽으면 그 항목은 버려지고 폴백으로 넘어간다.

## 프로젝트 구조

```
src/drinsta/
  pipeline.py          # 슬롯 하나 처리 (선별 -> 생성 -> 업로드 -> 대기 -> 발행 -> 기록)
  slots.py             # KST 슬롯 계산
  news.py              # NewsItem (DR/폴백 공통 형식)
  sources/dr_source.py # DR DB 읽기 전용 조회
  content/
    selector.py        # 후보 선별 + 중복 제거 + 폴백
    llm_client.py      # OpenAI (랭킹/작성/웹검색/임베딩)
    prompts.py         # 프롬프트 (톤 조정은 여기)
    post_builder.py    # 슬라이드 정리, 해시태그, 캡션 조립 (2,200자 제한)
    caption.py, dedup.py            # AInstagram에서 가져옴
  images/              # AInstagram에서 가져온 템플릿/배경 생성/R2 업로드
  publish/instagram_client.py       # AInstagram에서 가져옴
  notify/telegram.py   # 결과 알림
scripts/run_slot.py    # 진입점
.github/workflows/publish.yml
```

`caption.py`, `dedup.py`, `images/`, `instagram_client.py`는 AInstagram에서 복사했다. 두 저장소가 독립적으로
배포되기 때문에 공유 패키지로 빼지 않았다 - 한쪽에서 버그를 고치면 다른 쪽도 확인할 것.

## 예상 비용 (게시물 1건)

- 선별(gpt-5.4-mini) + 작성(gpt-5.4) + 임베딩: 수 센트 수준
- 이미지: 게시물당 3~6장 × gpt-image-2 low (AInstagram과 같은 설정)
- 폴백이 발동한 슬롯만 웹 검색 1회 추가 (DR의 AI 뉴스 섹션 1회 실측이 약 $0.18이므로 그 이하)
- GitHub Actions: 공개 저장소면 무료. 비공개면 실행당 약 25분(정각 대기 포함) × 하루 4회
