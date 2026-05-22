from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
except ImportError:  # runtime validation in api
    Browser = BrowserContext = Page = Any  # type: ignore
    async_playwright = None


class TaskStatus(str, Enum):
    idle = "idle"
    login_required = "login_required"
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
    refresh_selector: str | None = None
    min_interval_ms: int = 800
    max_interval_ms: int = 3000
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
    refresh_selector: str | None = None


class SessionState(str, Enum):
    init = "init"
    login_pending = "login_pending"
    ready = "ready"


@dataclass
class BrowserSession:
    session_id: str
    site_url: str
    state: SessionState = SessionState.init
    playwright: Any = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    page: Page | None = None


class SessionRequest(BaseModel):
    site_url: HttpUrl


class StartTaskRequest(BaseModel):
    session_id: str


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, TicketTask] = {}
        self.sessions: dict[str, BrowserSession] = {}
        self._workers: dict[str, asyncio.Task[Any]] = {}

    def create_task(self, payload: CreateTaskRequest) -> TicketTask:
        task = TicketTask(
            task_id=uuid.uuid4().hex[:10],
            site_url=str(payload.site_url),
            event_name=payload.event_name,
            selector_buy_button=payload.selector_buy_button,
            refresh_selector=payload.refresh_selector,
        )
        self.tasks[task.task_id] = task
        return task

    async def create_session(self, payload: SessionRequest) -> BrowserSession:
        if async_playwright is None:
            raise RuntimeError("playwright not installed")

        session = BrowserSession(session_id=uuid.uuid4().hex[:10], site_url=str(payload.site_url))
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=False, slow_mo=80)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(session.site_url)

        session.playwright = p
        session.browser = browser
        session.context = context
        session.page = page
        session.state = SessionState.login_pending
        self.sessions[session.session_id] = session
        return session

    async def mark_session_ready(self, session_id: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        if not session.page:
            raise RuntimeError("session page not available")
        session.state = SessionState.ready
        return session

    async def close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if not session:
            return
        if session.context:
            await session.context.close()
        if session.browser:
            await session.browser.close()
        if session.playwright:
            await session.playwright.stop()

    def _compute_next_interval(self, task: TicketTask, blocked: bool, captcha: bool, success: bool, latency_ms: int) -> int:
        interval = task.current_interval_ms
        if success:
            task.risk_score = max(0, task.risk_score - 15)
            return max(task.min_interval_ms, interval - 120)
        if blocked or captcha:
            task.risk_score = min(100, task.risk_score + 25)
            return min(task.max_interval_ms, int(interval * 1.8))
        if latency_ms > 2500:
            task.risk_score = min(100, task.risk_score + 10)
            return min(task.max_interval_ms, interval + 250)
        task.risk_score = max(0, task.risk_score - 2)
        return max(task.min_interval_ms, interval - 40)

    async def _attempt_real_purchase(self, task: TicketTask, session: BrowserSession) -> tuple[bool, bool, bool, int, str]:
        assert session.page is not None
        page = session.page
        start = time.perf_counter()
        blocked = False
        captcha = False
        success = False
        msg = ""

        if await page.locator("text=验证码").count() > 0 or await page.locator("text=滑块").count() > 0:
            captcha = True
            msg = "触发验证码，请人工处理"
        else:
            buy_btn = page.locator(task.selector_buy_button).first
            if await buy_btn.count() == 0:
                msg = "未找到购买按钮"
            else:
                disabled = await buy_btn.get_attribute("disabled")
                aria_disabled = await buy_btn.get_attribute("aria-disabled")
                if disabled is not None or aria_disabled == "true":
                    if task.refresh_selector:
                        refresh = page.locator(task.refresh_selector).first
                        if await refresh.count() > 0:
                            await refresh.click(timeout=3000)
                            msg = "按钮不可点，已刷新重试"
                        else:
                            await page.reload(wait_until="domcontentloaded")
                            msg = "按钮不可点，已重载页面"
                    else:
                        await page.reload(wait_until="domcontentloaded")
                        msg = "按钮不可点，已重载页面"
                else:
                    await buy_btn.click(timeout=5000)
                    await page.wait_for_timeout(1200)
                    content = await page.content()
                    success = any(k in content for k in ["提交订单", "确认订单", "支付", "订单详情"])
                    blocked = any(k in content for k in ["访问过于频繁", "稍后再试", "429", "系统繁忙"])
                    msg = "已点击购买，检测到成功页特征" if success else "已点击购买，等待后续确认"

        latency = int((time.perf_counter() - start) * 1000)
        return blocked, captcha, success, latency, msg

    async def _worker(self, task_id: str, session_id: str) -> None:
        task = self.tasks[task_id]
        session = self.sessions[session_id]
        task.status = TaskStatus.running

        while task.status == TaskStatus.running:
            task.attempts += 1
            blocked, captcha, success, latency, message = await self._attempt_real_purchase(task, session)
            task.current_interval_ms = self._compute_next_interval(task, blocked, captcha, success, latency)
            task.last_result = f"{message}（{latency}ms）"

            if success:
                task.status = TaskStatus.success
                break
            if captcha:
                task.status = TaskStatus.paused
                break

            jitter = random.randint(-120, 180)
            sleep_ms = max(task.min_interval_ms, task.current_interval_ms + jitter)
            await asyncio.sleep(sleep_ms / 1000)

    def start(self, task_id: str, session_id: str) -> TicketTask:
        task = self.tasks.get(task_id)
        session = self.sessions.get(session_id)
        if not task:
            raise KeyError("task")
        if not session or session.state != SessionState.ready:
            raise RuntimeError("session not ready")
        if task.status == TaskStatus.running:
            return task
        self._workers[task_id] = asyncio.create_task(self._worker(task_id, session_id))
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


app = FastAPI(title="Give Me One Ticket")
app.mount("/static", StaticFiles(directory="static"), name="static")
manager = TaskManager()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.post("/api/sessions")
async def create_session(payload: SessionRequest) -> dict[str, Any]:
    try:
        session = await manager.create_session(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id": session.session_id, "state": session.state}


@app.post("/api/sessions/{session_id}/ready")
async def session_ready(session_id: str) -> dict[str, Any]:
    try:
        session = await manager.mark_session_ready(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return {"session_id": session.session_id, "state": session.state}


@app.delete("/api/sessions/{session_id}")
async def close_session(session_id: str) -> dict[str, str]:
    await manager.close_session(session_id)
    return {"result": "closed"}


@app.post("/api/tasks")
async def create_task(payload: CreateTaskRequest) -> dict[str, Any]:
    task = manager.create_task(payload)
    return {"task_id": task.task_id, "status": task.status}


@app.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str, payload: StartTaskRequest) -> dict[str, Any]:
    try:
        task = manager.start(task_id, payload.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
