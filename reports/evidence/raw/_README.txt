수집 일시: 2026-11-04 17:12~17:13 (KST), 실행: WSL curl / .venv python(httpx)
서버: uvicorn main:app --host 127.0.0.1 --port 8000, celery --pool=solo --concurrency=1 (Jaden 터미널)
W01_warmup_sync: FastAPI 프로세스 첫 요청 (모델 로드 포함) inference_ms 2253.55
T03/T04 job 3: Worker 프로세스 첫 작업 (모델 로드 포함) inference_ms 2215.86
T09: bean_rust_test.0.jpg 는 T02_sync_bean_rust 에서 먼저 MISS(17.57ms) 로 처리됨 -> T09 1st/2nd 는 HIT
T11: 6,000,000 B 랜덤 바이트 파일 -> 413
성능 원본: reports/data/timings.csv (비동기 final_ms 는 50ms 간격 폴링 기준이라 폴링 간격 영향 포함)
Redis/DB 데이터는 삭제하지 않음. 이 테스트로 image_jobs id 3~13 생성.
