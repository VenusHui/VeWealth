"""磁盘余量观测与部署闸门测试（VEW-67）。

两块被测对象：

1. ``app.core.capacity`` —— 进程内采样 / 状态跃迁告警（纯单元测试，采样函数注入）。
2. ``.github/scripts/disk_headroom_check.sh`` —— 部署流水线里的那条闸门。

第 2 块用 pytest 测 shell 脚本看着不常规，但这条闸门**是本次交付的投递通道本身**
（后端模块只到日志为止，仓库内没有日志采集 / 告警系统）。它跑在远端 zsh 里、由 YAML
调用，而 YAML 没有任何自动化覆盖 —— VEW-68 已经证明过「只在远端 shell 里跑一次的
逻辑」会静默失效，所以这里用子进程把每个分支都钉一遍。
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from app.core.capacity import (
    EVENT_LOW,
    EVENT_RECOVERED,
    EVENT_UNREADABLE,
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARNING,
    CapacityMonitor,
    parse_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / ".github" / "scripts" / "disk_headroom_check.sh"
GB = 1024**3


def _usage(free_gb: float, total_gb: float = 40.0):
    """构造一个 shutil.disk_usage 风格的假读数。"""
    total = int(total_gb * GB)
    free = int(free_gb * GB)
    return (total, total - free, free)


def _failing_usage(_path: str):
    raise FileNotFoundError(2, "No such file or directory")


class CapacityMonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = CapacityMonitor(
            paths=["/"],
            warn_gb=10.0,
            critical_gb=5.0,
            usage_fn=lambda _p: _usage(30.0),
        )

    # ------------------------------------------------------------------
    # 分档
    # ------------------------------------------------------------------

    def test_classify_boundaries(self):
        self.assertEqual(self.monitor._classify(30.0), STATUS_OK)
        self.assertEqual(self.monitor._classify(10.0), STATUS_OK)  # 等于阈值不算越线
        self.assertEqual(self.monitor._classify(9.99), STATUS_WARNING)
        self.assertEqual(self.monitor._classify(5.0), STATUS_WARNING)
        self.assertEqual(self.monitor._classify(4.99), STATUS_CRITICAL)

    def test_sample_reports_usage_fields(self):
        self.monitor = CapacityMonitor(
            paths=["/"], warn_gb=10.0, critical_gb=5.0, usage_fn=lambda _p: _usage(15.0)
        )
        snap = self.monitor.sample()
        disk = snap["disks"][0]
        self.assertEqual(snap["status"], STATUS_OK)
        self.assertEqual(disk["path"], "/")
        self.assertAlmostEqual(disk["free_gb"], 15.0, places=1)
        self.assertAlmostEqual(disk["total_gb"], 40.0, places=1)
        self.assertEqual(snap["thresholds"], {"warn_gb": 10.0, "critical_gb": 5.0})

    def test_sample_is_pure(self):
        """HTTP 读路径不该写事件流（GET 无副作用）。"""
        self.monitor = CapacityMonitor(
            paths=["/"], warn_gb=10.0, critical_gb=5.0, usage_fn=lambda _p: _usage(1.0)
        )
        self.monitor.sample()
        self.assertEqual(self.monitor.events_recent(), [])
        self.assertEqual(self.monitor.snapshot()["disks"], [])

    # ------------------------------------------------------------------
    # 状态跃迁
    # ------------------------------------------------------------------

    def test_transition_emits_once_per_change(self):
        readings = iter(
            [_usage(30.0), _usage(7.0), _usage(6.0), _usage(3.0), _usage(3.5)]
        )
        self.monitor = CapacityMonitor(
            paths=["/"],
            warn_gb=10.0,
            critical_gb=5.0,
            usage_fn=lambda _p: next(readings),
        )

        self.monitor.check()  # ok：无事件
        self.assertEqual(self.monitor.events_recent(), [])

        self.monitor.check()  # ok → warning
        self.monitor.check()  # warning 保持：不重复告警
        events = self.monitor.events_recent()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EVENT_LOW)
        self.assertEqual(events[0]["level"], "WARNING")

        self.monitor.check()  # warning → critical：升级为 ERROR
        self.monitor.check()  # critical 保持
        events = self.monitor.events_recent()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], EVENT_LOW)
        self.assertEqual(events[0]["level"], "ERROR")

    def test_recovery_event_and_status_since(self):
        readings = iter([_usage(3.0), _usage(3.5), _usage(30.0)])
        self.monitor = CapacityMonitor(
            paths=["/"],
            warn_gb=10.0,
            critical_gb=5.0,
            usage_fn=lambda _p: next(readings),
        )
        self.monitor.check()
        first_since = self.monitor.snapshot()["disks"][0]["status_since"]
        self.monitor.check()  # 状态未变 → 沿用跃迁时间
        self.assertEqual(
            self.monitor.snapshot()["disks"][0]["status_since"], first_since
        )
        self.monitor.check()  # critical → ok
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["event_type"], EVENT_RECOVERED)
        self.assertEqual(events[0]["level"], "INFO")
        self.assertEqual(self.monitor.snapshot()["status"], STATUS_OK)

    def test_unreadable_path_is_unknown_not_ok(self):
        self.monitor = CapacityMonitor(
            paths=["/nope"], warn_gb=10.0, critical_gb=5.0, usage_fn=_failing_usage
        )
        snap = self.monitor.check()
        disk = snap["disks"][0]
        self.assertEqual(disk["status"], STATUS_UNKNOWN)
        self.assertIn("FileNotFoundError", disk["error"])
        events = self.monitor.events_recent()
        self.assertEqual(events[0]["event_type"], EVENT_UNREADABLE)
        # 「没测到」不能读成「没问题」
        self.assertEqual(self.monitor.overall_status(), STATUS_UNKNOWN)

    def test_overall_status_is_worst_of(self):
        usage = {"/": _usage(30.0), "/data": _usage(2.0)}
        self.monitor = CapacityMonitor(
            paths=["/", "/data"],
            warn_gb=10.0,
            critical_gb=5.0,
            usage_fn=lambda p: usage[p],
        )
        self.assertEqual(self.monitor.check()["status"], STATUS_CRITICAL)

    def test_event_buffer_is_bounded(self):
        readings = [_usage(1.0), _usage(30.0)] * 10
        it = iter(readings)
        self.monitor = CapacityMonitor(
            paths=["/"],
            warn_gb=10.0,
            critical_gb=5.0,
            event_limit=3,
            usage_fn=lambda _p: next(it),
        )
        for _ in range(len(readings)):
            self.monitor.check()
        self.assertLessEqual(len(self.monitor.events_recent(limit=100)), 3)

    def test_parse_paths(self):
        self.assertEqual(parse_paths("/"), ["/"])
        self.assertEqual(parse_paths(" / , /data ,,/ "), ["/", "/data"])
        self.assertEqual(parse_paths(""), ["/"])
        self.assertEqual(parse_paths(None), ["/"])


class HealthEndpointTests(unittest.TestCase):
    def test_capacity_endpoint_shape(self):
        from app.routers.health import health_capacity

        payload = health_capacity(events=5)
        self.assertIn(payload["status"], {STATUS_OK, STATUS_WARNING, STATUS_CRITICAL})
        self.assertTrue(payload["disks"])
        self.assertIn("events", payload)
        self.assertIn("thresholds", payload)

    def test_overall_health_exposes_capacity_without_folding_status(self):
        """容量是并列维度，不并入 status（源健康与盘余量处置不同）。"""
        from app.routers.health import overall_health

        payload = overall_health(refresh=False)
        self.assertIn("capacity", payload)
        self.assertIn("disks", payload["capacity"])
        self.assertIn("status", payload)


class DiskHeadroomGateTests(unittest.TestCase):
    """部署闸门脚本的分支（VEW-67 的投递通道）。"""

    def _run(self, *args, env=None, cwd=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        return subprocess.run(
            ["bash", str(GATE_SCRIPT), *args],
            capture_output=True,
            text=True,
            env=full_env,
            cwd=cwd or REPO_ROOT,
        )

    def test_script_exists_and_is_executable(self):
        self.assertTrue(GATE_SCRIPT.is_file(), f"闸门脚本缺失: {GATE_SCRIPT}")

    def test_healthy_headroom_passes(self):
        proc = self._run("post-prune", "--free-gb", "30", "--env-file", "/nonexistent")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("::error::", proc.stdout)

    def test_warning_headroom_does_not_fail(self):
        proc = self._run("post-prune", "--free-gb", "7", "--env-file", "/nonexistent")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("::warning::", proc.stdout)

    def test_critical_headroom_fails_post_prune(self):
        proc = self._run("post-prune", "--free-gb", "3", "--env-file", "/nonexistent")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("::error::", proc.stdout)

    def test_critical_headroom_does_not_block_pre_deploy(self):
        """部署前只报数：那时旧容器还在跑，不该挡掉一次可能救急的发布。"""
        proc = self._run("pre-deploy", "--free-gb", "3", "--env-file", "/nonexistent")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("::error::", proc.stdout)

    def test_prune_failure_alone_fails_post_prune(self):
        """VEW-68 的静默失败：封顶失败此前只有一行日志，现在要有人知道。"""
        proc = self._run(
            "post-prune",
            "--free-gb",
            "30",
            "--env-file",
            "/nonexistent",
            env={"PRUNE_FAILED": "1"},
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("::error::", proc.stdout)

    def test_prune_failure_ignored_before_prune(self):
        proc = self._run(
            "pre-deploy",
            "--free-gb",
            "30",
            "--env-file",
            "/nonexistent",
            env={"PRUNE_FAILED": "1"},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("::error::", proc.stdout)

    def test_thresholds_read_from_env_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".prod.env"
            env_file.write_text(
                "ENV=prod\n"
                "DISK_HEADROOM_WARN_GB=20\n"
                'DISK_HEADROOM_CRITICAL_GB="12"\n'
                "# DISK_HEADROOM_WARN_GB=99\n",
                encoding="utf-8",
            )
            # 15 GB：按默认阈值是 ok，按 env 文件的 20GB 警告阈值应转 warning
            proc = self._run(
                "post-prune", "--free-gb", "15", "--env-file", str(env_file)
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("::warning::", proc.stdout)
            # 11 GB：低于 env 文件的 12GB 临界阈值 → 失败
            proc = self._run(
                "post-prune", "--free-gb", "11", "--env-file", str(env_file)
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_cli_threshold_overrides_env_file(self):
        proc = self._run(
            "post-prune",
            "--free-gb",
            "15",
            "--env-file",
            "/nonexistent",
            "--critical-gb",
            "20",
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_unknown_arg_is_rejected(self):
        proc = self._run("post-prune", "--bogus")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_real_df_path_never_crashes(self):
        """不传 --free-gb 时走真实 df；只要求脚本能给出结论，不断言具体读数。"""
        proc = self._run("check", "--path", "/", "--env-file", "/nonexistent")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("可用", proc.stdout)

    def test_deploy_workflows_call_the_gate(self):
        """闸门必须真的挂在部署路径上 —— 脚本再好，没人调用就等于没有。"""
        for name in ("ci-cd.yml", "manual-deploy.yml"):
            text = (REPO_ROOT / ".github" / "workflows" / name).read_text(
                encoding="utf-8"
            )
            self.assertIn("disk_headroom_check.sh pre-deploy", text, name)
            self.assertIn("disk_headroom_check.sh post-prune", text, name)


if __name__ == "__main__":
    unittest.main()
