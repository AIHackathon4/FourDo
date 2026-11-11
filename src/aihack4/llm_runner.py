"""평가 스크립트용 LLM 호출기: 응답을 파일에 캐시하고 여러 건을 병렬로 부른다."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

MAX_ATTEMPTS = 2


class Cache:
    def __init__(self, path: Path):
        self.path, self.lock, self.items = path, threading.Lock(), {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                self.items[record["key"]] = record

    def get(self, key: str) -> dict | None:
        return self.items.get(key)

    def put(self, key: str, raw: dict, cost: float) -> None:
        record = {"key": key, "raw": raw, "cost": cost, "at": datetime.now().isoformat(timespec="seconds")}
        with self.lock:
            self.items[key] = record
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")


def cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Runner:
    """job: name, key_text(캐시 키가 될 모델·프롬프트·입력 전체), call(→ (raw, cost))."""

    def __init__(self, cache: Cache):
        self.cache, self.lock = cache, threading.Lock()
        self.new_calls, self.cached_calls, self.new_cost, self.errors = 0, 0, 0.0, []

    def run(self, job: dict) -> dict | None:
        key = cache_key(job["key_text"])
        if hit := self.cache.get(key):
            with self.lock:
                self.cached_calls += 1
            return hit["raw"]
        call: Callable[[], tuple[dict, float]] = job["call"]
        last_error = ""
        for _ in range(MAX_ATTEMPTS):
            try:
                raw, cost = call()
            except Exception as error:  # 한 건 실패가 전체 평가를 멈추지 않게 기록만 한다.
                last_error = f"{type(error).__name__}: {error}"
                continue
            self.cache.put(key, raw, cost)
            with self.lock:
                self.new_calls += 1
                self.new_cost += cost
            return raw
        with self.lock:
            self.errors.append({"job": job["name"], "error": last_error})
        return None

    def run_all(self, jobs: list[dict], workers: int) -> list[dict | None]:
        with ThreadPoolExecutor(workers) as pool:
            return list(pool.map(self.run, jobs))

    def stats(self) -> dict:
        return {"new_calls": self.new_calls, "cached_calls": self.cached_calls,
                "new_cost_usd": round(self.new_cost, 4), "errors": len(self.errors)}
