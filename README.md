# FastAPI Beans ResNet18 동기·비동기 평가 프로젝트

콩잎 이미지 3클래스 분류 서비스입니다 (`angular_leaf_spot`, `bean_rust`, `healthy`).
- 모델: `microsoft/resnet-18` Head-only Fine-tuning (batch 16, epoch 6)
- API: FastAPI 동기·비동기
- 큐: Redis + Celery
- 작업 기록: PostgreSQL

## 1. 환경

- Windows 11 + WSL2 Ubuntu, Python 가상환경 `.venv`
  - 작성 환경: Ubuntu 26.04.1 LTS, Python 3.14.4
- Docker Desktop (WSL 통합 켜기) / Docker Compose
- Docker로 실행: Redis `127.0.0.1:6379`, PostgreSQL `127.0.0.1:5432`
- `.venv`에서 실행: FastAPI (Uvicorn), Celery Worker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- `uvicorn` 실행 시 `no pq wrapper available` 오류가 나면 `pip install "psycopg[binary]>=3.2,<4"`를 실행합니다.

## 2. 데이터 준비

- 제공 원본 `validation/`의 각 클래스 이미지를 코드 실행용 `dataset/val/`에 배치합니다.
- `dataset/train/`, `dataset/val/`, `dataset/test/` 모두 같은 3클래스 폴더 구조를 씁니다.

```text
dataset/
├── train/{angular_leaf_spot, bean_rust, healthy}/   # 1,034장
├── val/{angular_leaf_spot, bean_rust, healthy}/     # 133장
└── test/{angular_leaf_spot, bean_rust, healthy}/    # 128장 (학습에 사용하지 않음)
```

- `dataset/`은 Git에 포함하지 않습니다 (`.gitignore`). 데이터셋의 공개·배포 권한을 확인한 뒤 공유합니다.

## 3. 모델 준비

```bash
source .venv/bin/activate
python -m training.finetune --data dataset --output artifacts/beans_resnet18_bs16_ep6 --epochs 6 --batch-size 16 --freeze-backbone
```

- 최종 모델 설정: Head-only, batch_size 16, epochs 6 (Best Val 0.8346, Test 0.8750)
  - batch 8·epoch 3, batch 16·epoch 3과 Validation으로 비교해 선택했습니다 ([reports/01_FineTuning_결과보고서.md](reports/01_FineTuning_결과보고서.md)).
- 모델은 `artifacts/beans_resnet18_bs16_ep6/`에 저장됩니다: `config.json`, `model.safetensors`, `preprocessor_config.json`, `training_report.json`
- `--output`을 생략하면 기본값 `artifacts/beans_resnet18`에 저장되어 그 폴더를 덮어씁니다. 실험마다 `--output`을 다르게 지정합니다.
- `--freeze-backbone`을 빼면 Full Fine-tuning이 됩니다.
- `.env` 설정 (`.env.example`을 복사해서 사용)

```bash
cp .env.example .env
# .env 안에서 MODEL_PATH를 최종 모델로 변경
MODEL_PATH=artifacts/beans_resnet18_bs16_ep6
```

- `.env.example`의 기본값은 `artifacts/beans_resnet18`이므로 위와 같이 바꿔야 합니다.
- (선택) Test 평가: `python -m training.evaluate_test --model artifacts/beans_resnet18_bs16_ep6`
  - Test는 최종 모델에 1회만 실행합니다.
  - 이 스크립트는 별도로 추가한 것입니다.
  - 결과는 `reports/data/`, `reports/figures/`에 저장됩니다.

## 4. 구동 순서

터미널 3개를 사용하고, 모두 `~/fastapi_resnet18`에서 실행합니다.

1. Docker 서비스 (Redis, PostgreSQL만)
   ```bash
   docker compose up -d redis postgres
   docker compose exec redis redis-cli ping        # PONG
   docker compose exec postgres pg_isready -U vision_user -d vision_db
   ```
   - `compose.yaml`에는 web, worker, trainer 서비스도 있습니다. 이 프로젝트에서는 FastAPI와 Worker를 `.venv`에서 실행합니다.
   - 서비스 이름 없이 `docker compose up -d`를 실행하면 web 컨테이너가 8000번 포트를 차지합니다.
2. 터미널 A: Celery Worker
   ```bash
   source .venv/bin/activate
   celery -A ai.celery_app:celery_app worker --loglevel=info --pool=solo --concurrency=1
   ```
3. 터미널 B: FastAPI
   ```bash
   source .venv/bin/activate
   uvicorn main:app --host 127.0.0.1 --port 8000
   ```
4. 브라우저
   - 동기 화면: http://127.0.0.1:8000/vision/sync/
   - 비동기 화면: http://127.0.0.1:8000/vision/async/
   - API 문서: http://127.0.0.1:8000/docs

종료: 터미널 A·B에서 `Ctrl+C`, Docker는 `docker compose stop redis postgres`
- `docker compose down -v`는 DB와 Redis 데이터를 삭제하니 주의합니다.

## 5. 확인할 응답

- `GET /health`: `{"status":"ok"}`
- 동기 `POST /api/sync/classify/` (multipart 필드 `image`)
  - 200, `model_id`, `top1`, `all`, `inference_ms`, `cache_hit`
  - 같은 이미지를 600초 안에 다시 보내면 `cache_hit: true`
- 비동기 `POST /api/async/classify/`: 202, `job_id`, `check_url`
- 비동기 `GET /api/async/jobs/{job_id}/`: `status`가 `queued` → `running` → `done`
- PostgreSQL `image_jobs`에 비동기 작업이 기록됩니다. 동기 요청은 기록되지 않습니다.

```bash
curl -F "image=@dataset/test/bean_rust/bean_rust_test.0.jpg" http://127.0.0.1:8000/api/sync/classify/
docker compose exec postgres psql -U vision_user -d vision_db -c "SELECT id, status, top1_label, top1_score FROM image_jobs ORDER BY id DESC LIMIT 5;"
```

- 모의 테스트 (가짜 모델, 임시 SQLite 사용)
  ```bash
  python -m pytest -q test_api.py
  ```

## 6. 결과물 위치

- 개별 보고서: `reports/01_*.md` ~ `reports/08_*.md` (종합: [reports/08_최종기술보고서.md](reports/08_최종기술보고서.md))
- 측정 원본: `reports/data/timings.csv`, `reports/data/test_metrics_beans_resnet18.json`
- 실행 증빙
  - 캡처: `reports/evidence/*.png`
  - 응답 원본: `reports/evidence/raw/`
- 그래프: `reports/figures/`
- 학습 결과: `artifacts/beans_resnet18_bs16_ep6/training_report.json` (비교 실험: `artifacts/beans_resnet18/`, `artifacts/beans_resnet18_bs16/`)

## 7. 제출 및 보안

- `.env`, 비밀번호, API 키는 GitHub에 올리지 않습니다 (`.gitignore`에 `.env` 포함). 설정 예시는 `.env.example`을 사용합니다.
- `compose.yaml`의 PostgreSQL 계정(`vision_user` / `vision_pass`)은 로컬 실습용입니다. 외부에 공개하는 환경에서는 바꿔야 합니다.
- Git에 포함하지 않는 항목 (`.gitignore`)
  - `dataset/`
  - `artifacts/` (모델 가중치, 약 45MB)
  - `media/jobs/` (업로드 이미지)
  - `.venv/`
- 위 항목은 허용된 제출 경로로 따로 전달합니다. 받는 사람은 2장(데이터)과 3장(모델)의 위치에 그대로 배치하면 됩니다.
