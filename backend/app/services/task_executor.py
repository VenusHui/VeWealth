"""有界后台任务执行器 — 选股与回测共享的有界 daemon worker 池。

背景：screener_service 与 backtest job_manager 此前各“一任务一 daemon thread”，
数量无上限、刷新/重启即丢，且无法真正取消。这里用一个有界线程池统一承载所有
重后台任务：并发受 `MAX_TASK_WORKERS` 限制，任务间通过独立 CancelToken 隔离，
可随时请求取消。任务函数签名为 `fn(cancel_token, *args, **kwargs)`。

采用 daemon worker 线程：进程退出时自动终止，避免长任务阻塞重启/优雅关闭，
与“后台任务不阻塞应用生命周期”的既有约定保持一致。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger("vewealth.task_executor")


class CancelToken:
    """给单个后台任务注入的取消句柄。

    任务在长循环中定期调用 is_cancelled()；外部通过 request_cancel() 主动取消。
    与数据库里的 cancel_requested 双保险：同一任务可能同时收到两者，任一触发即停。
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def request_cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class BackgroundTaskExecutor:
    """有界 daemon worker 池 + 按 job_id 隔离的取消事件注册表。"""

    # 每 worker 最多可积压的任务数，超出即拒绝提交，防止内存无限增长。
    _PENDING_MULTIPLIER = 4

    def __init__(self, max_workers: int | None = None) -> None:
        self._max_workers = max(1, max_workers or settings.MAX_TASK_WORKERS)
        self._queue: queue.Queue[
            tuple[CancelToken, str, Callable[..., Any], tuple, dict]
        ] = queue.Queue()
        self._tokens: dict[str, CancelToken] = {}
        self._lock = threading.Lock()
        self._start_workers()

    def _start_workers(self) -> None:
        for i in range(self._max_workers):
            thread = threading.Thread(
                target=self._worker,
                name=f"vwe-task-{i}",
                daemon=True,
            )
            thread.start()

    def _worker(self) -> None:
        while True:
            token, job_id, fn, args, kwargs = self._queue.get()
            try:
                fn(token, *args, **kwargs)
            except Exception:
                logger.exception("后台任务 %s 执行异常", job_id)
            finally:
                self._queue.task_done()
                with self._lock:
                    # 仅移除属于本次提交的句柄，避免误删后续同 id 的新句柄
                    if self._tokens.get(job_id) is token:
                        self._tokens.pop(job_id, None)

    def submit(
        self,
        job_id: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> CancelToken:
        """提交一个后台任务。

        任务将以 `fn(cancel_token, *args, **kwargs)` 被调用。同一 job_id 重复提交
        会复用其取消句柄。待处理任务数超过上限时抛 ValueError，防止队列无界增长。
        """
        pending_cap = self._max_workers * self._PENDING_MULTIPLIER
        if self._queue.qsize() >= pending_cap:
            raise ValueError(
                f"后台任务队列已满（pending >= {pending_cap}），请稍后重试"
            )

        with self._lock:
            token = self._tokens.get(job_id)
            if token is None:
                token = CancelToken()
                self._tokens[job_id] = token

        self._queue.put((token, job_id, fn, args, kwargs))
        return token

    def cancel(self, job_id: str) -> bool:
        """请求取消指定任务；返回该任务是否已被登记（存在可取消句柄）。"""
        with self._lock:
            token = self._tokens.get(job_id)
        if token is None:
            return False
        token.request_cancel()
        return True


# 全局单例，供 screener / backtest 共用
background_task_executor = BackgroundTaskExecutor()
