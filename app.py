from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl


class TaskStatus(str, Enum):
    idle = "idle"
    running = "running"
    paused = "paused"
    success = "success"
    failed = "failed"


@dataclass
class TicketTask:
    task_id: str
    site_url: str
    event_name: str
    selector_buy_button: str
    min_interval_ms: int = 800
    max_interval_ms: int = 2500
    current_interval_ms: int = 1800
    status: TaskStatus = TaskStatus.idle
    attempts: int = 0
    last_result: str = "not started"
    risk_score: int = 0
    created_at: float = field(default_factory=time.time)


class CreateTaskRequest(BaseModel):
    site_url: HttpUrl
    event_name: str
    selector_buy_button: str


class FrequencySignal(BaseModel):
    latency_ms: int
    blocked: bool
    captcha: bool
    success: bool


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, TicketTask] = {}
        self._workers: dict[str, asyncio.Task[Any]] = {}

    def create_task(self, payload: CreateTaskRequest) -> TicketTask:
        task = TicketTask(
            task_id=uuid.uuid4().hex[:10],
            site_url=str(payload.site_url),
            event_name=payload.event_name,
            selector_buy_button=payload.selector_buy_button,
        )
        self.tasks[task.task_id] = task
        return task

    def _compute_next_interval(self, task: TicketTask, signal: FrequencySignal) -> int:
        interval = task.current_interval_ms
        if signal.success:
            task.risk_score = max(0, task.risk_score - 15)
            return max(task.min_interval_ms, interval - 120)

        if signal.blocked or signal.captcha:
            task.risk_score = min(100, task.risk_score + 25)
            return min(task.max_interval_ms, int(interval * 1.8))

        if signal.latency_ms > 2500:
            task.risk_score = min(100, task.risk_score + 10)
            return min(task.max_interval_ms, interval + 250)

        task.risk_score = max(0, task.risk_score - 2)
        return max(task.min_interval_ms, interval - 40)

    async def _simulate_one_attempt(self, task: TicketTask) -> FrequencySignal:
        latency = random.randint(150, 3200)
        roll = random.random()
        blocked = roll < 0.05
        captcha = 0.05 <= roll < 0.11
        success = roll > 0.985
        await asyncio.sleep(latency / 1000)
        return FrequencySignal(
            latency_ms=latency,
            blocked=blocked,
            captcha=captcha,
            success=success,
        )

    async def _worker(self, task_id: str) -> None:
        task = self.tasks[task_id]
        task.status = TaskStatus.running
        while task.status == TaskStatus.running:
            task.attempts += 1
            signal = await self._simulate_one_attempt(task)
            task.current_interval_ms = self._compute_next_interval(task, signal)

            if signal.success:
                task.status = TaskStatus.success
                task.last_result = "抢票成功（模拟）"
                break
            if signal.captcha:
                task.status = TaskStatus.paused
                task.last_result = "触发验证码，请人工接管"
                break
            if signal.blocked:
                task.last_result = "触发频率防护，自动降频重试"
            else:
                task.last_result = f"未成功，延迟 {signal.latency_ms}ms"

            jitter = random.randint(-100, 180)
            sleep_ms = max(task.min_interval_ms, task.current_interval_ms + jitter)
            await asyncio.sleep(sleep_ms / 1000)

    def start(self, task_id: str) -> TicketTask:
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        if task.status == TaskStatus.running:
            return task
        task.status = TaskStatus.running
        self._workers[task_id] = asyncio.create_task(self._worker(task_id))
        return task

    def pause(self, task_id: str) -> TicketTask:
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        task.status = TaskStatus.paused
        worker = self._workers.get(task_id)
        if worker and not worker.done():
            worker.cancel()
        task.last_result = "任务已暂停"
        return task


app = FastAPI(title="Give Me One Ticket - Visual Prototype")
app.mount("/static", StaticFiles(directory="static"), name="static")
manager = TaskManager()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    with open("static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/tasks")
async def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    task = manager.create_task(payload)
    return {"task_id": task.task_id, "status": task.status}


@app.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str) -> dict[str, Any]:
    try:
        task = manager.start(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    return {"task_id": task.task_id, "status": task.status}


@app.post("/api/tasks/{task_id}/pause")
async def pause_task(task_id: str) -> dict[str, Any]:
    try:
        task = manager.pause(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    return {"task_id": task.task_id, "status": task.status}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    task = manager.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "task_id": task.task_id,
        "site_url": task.site_url,
        "event_name": task.event_name,
        "status": task.status,
        "attempts": task.attempts,
        "current_interval_ms": task.current_interval_ms,
        "risk_score": task.risk_score,
        "last_result": task.last_result,
    }
