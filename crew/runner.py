"""Smallest reliable script controller for unattended runs.

One runner process owns one run for its lifetime through a nonblocking lock.
It drives the existing tick path, waits without inventing approvals, and
records honest progress through the authoritative state store.
"""
from __future__ import annotations

import fcntl
import math
import os
from pathlib import Path
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from crew.resources import check_resources
from crew.runtime import Runtime

RUNNER_LOCK = '.runner.lock'


def observe_ownership(run_dir) -> str:
    """Read-only liveness probe for the per-run controller lock.

    Returns 'active' when a process holds the lock, 'free' when no process holds
    it, and 'unknown' when the probe cannot tell. It never writes state and it
    releases an acquired lock immediately, so an active owner is not disturbed.
    """
    path = Path(run_dir) / RUNNER_LOCK
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 'free'
    except OSError:
        return 'unknown'
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 'active'
        fcntl.flock(fd, fcntl.LOCK_UN)
        return 'free'
    finally:
        os.close(fd)


class RunnerError(ValueError):
    """The script controller cannot own or drive this run."""


class _RunnerStop(Exception):
    def __init__(self, signum: int):
        super().__init__(f'signal {signum}')
        self.signum = signum


MIN_POLL_SECONDS = 0.1
MAX_POLL_SECONDS = 60.0


@dataclass(frozen=True)
class RunnerLimits:
    """Practical poll bounds in seconds. NaN, infinity, and extremes are rejected."""

    min_delay: float = 1.0
    max_delay: float = 10.0

    def __post_init__(self):
        for name in ('min_delay', 'max_delay'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number of seconds')
            if not MIN_POLL_SECONDS <= value <= MAX_POLL_SECONDS:
                raise ValueError(f'{name} must be between {MIN_POLL_SECONDS} and {MAX_POLL_SECONDS} seconds')
        if self.min_delay > self.max_delay:
            raise ValueError('min_delay must not exceed max_delay')


class Runner:
    """Foreground script controller. One instance owns the run lock."""

    def __init__(self, run_dir, *, limits=None, herdr=None, resource_check=check_resources,
                 sleep=time.sleep, clock=time.monotonic):
        self.runtime = Runtime(run_dir)
        self.limits = limits or RunnerLimits()
        self.herdr = herdr
        self.resource_check = resource_check
        self.sleep = sleep
        self.clock = clock

    def run(self, *, max_iterations=None):
        """Drive the run until a terminal state. Return (summary, exit code)."""
        if self.runtime.store.read_state().get('controller') != 'script':
            raise RunnerError('run is not script-controlled; use the legacy orchestrator path')
        with self._ownership():
            return self._run_loop(max_iterations=max_iterations)

    @contextmanager
    def _ownership(self):
        path = self.runtime.path / RUNNER_LOCK
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RunnerError('a runner already owns this run') from None
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @contextmanager
    def _signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        originals = {}

        def handler(signum, frame):
            raise _RunnerStop(signum)

        for signum in (signal.SIGTERM, signal.SIGINT):
            originals[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)
        try:
            yield
        finally:
            for signum, original in originals.items():
                signal.signal(signum, original)

    def _run_loop(self, *, max_iterations):
        previous_role = os.environ.get('CREW_ROLE')
        os.environ['CREW_ROLE'] = 'orchestrator'
        last = {}
        try:
            with self._signals():
                # Start, poll, sleep, and evaluation failures all exit the same
                # way: record failed and never claim completion.
                try:
                    self._mark_start()
                    delay = self.limits.min_delay
                    fingerprint = None
                    iterations = 0
                    last_reason = 'iteration limit reached'
                    while True:
                        if max_iterations is not None and iterations >= max_iterations:
                            return self._finalize(last, last_reason, 124, status='waiting')
                        iterations += 1
                        result = self.runtime.tick(controller='script', herdr=self.herdr,
                                                   resource_check=self.resource_check)
                        last = result
                        state = self.runtime.store.read_state()
                        verdict, reason = self._evaluate(result, state)
                        finished = self._finished(verdict, result, reason)
                        if finished is not None:
                            return finished
                        last_reason = reason
                        self._record_progress(result, iterations, reason)
                        current = self._fingerprint(result)
                        if current != fingerprint:
                            delay = self.limits.min_delay
                        fingerprint = current
                        self.sleep(delay)
                        delay = min(delay * 2, self.limits.max_delay)
                except _RunnerStop:
                    raise
                except Exception as error:
                    return self._fail(error, last)
        except _RunnerStop as stop:
            return self._finalize(last, f'signal {stop.signum}', 128 + stop.signum, status='stopped')
        finally:
            if previous_role is None:
                os.environ.pop('CREW_ROLE', None)
            else:
                os.environ['CREW_ROLE'] = previous_role

    def _finished(self, verdict, result, reason):
        """Map a terminal verdict to its honest exit record. A nonterminal verdict returns None."""
        if verdict == 'complete':
            return self._finalize(result, reason, 0, status='complete')
        if verdict == 'failed':
            return self._finalize(result, reason, 1, status='failed')
        if verdict == 'stopped':
            return self._finalize(result, reason, 0, status='stopped')
        return None

    def _fail(self, error, last):
        """Record a failure without claiming completion, even if state is unwritable."""
        try:
            return self._finalize(last, 'runner error: ' + str(error), 1, status='failed', error=str(error))
        except Exception:
            summary = {'run_id': self.runtime.store.run_dir.name,
                       'runner': {'status': 'failed', 'reason': 'runner error: ' + str(error)},
                       'flows': {}, 'tasks': {}, 'cleanup': {}}
            return summary, 1

    def _evaluate(self, result, state):
        if state.get('stop_requested') or state.get('close_requested'):
            return 'stopped', 'stop or close requested'
        flows = result.get('flows') or {}
        tasks = result.get('tasks') or {}
        items = state.get('items') or []
        if any(task.get('status') == 'failed' for task in tasks.values()):
            return 'failed', 'one or more workers failed'
        # Complete only with one status for every run item; a partial map never completes.
        if items and set(flows) == set(items) and all(flows[key] == 'complete' for key in items):
            return 'complete', 'every flow is complete'
        if state.get('pause_requested') or any(task.get('pause_requested') for task in tasks.values()):
            return 'continue', 'paused; waiting for resume'
        if any(status in ('user_decision', 'escalation') for status in flows.values()):
            return 'continue', 'waiting for an explicit user decision'
        waits = sorted({task['resource_wait'] for task in tasks.values() if task.get('resource_wait')})
        if waits:
            return 'continue', 'dispatch delayed: ' + '; '.join(waits[:3])
        if any(status == 'blocked' for status in flows.values()):
            return 'continue', 'blocked; waiting for reconciliation or a decision'
        return 'continue', 'waiting for worker progress'

    def _fingerprint(self, result):
        tasks = result.get('tasks') or {}
        flows = result.get('flows') or {}
        return (tuple(sorted((key, value.get('status')) for key, value in tasks.items())),
                tuple(sorted(flows.items())))

    def _mark_start(self):
        now = time.time_ns()

        def update(state):
            previous = state.get('runner') or {}
            crashed = previous.get('status') == 'running'
            state['runner'] = {
                **previous,
                'pid': os.getpid(), 'controller': 'script', 'mode': 'script',
                'status': 'running', 'started_at_ns': now, 'iterations': 0,
                'last_flows': {}, 'errors': list(previous.get('errors') or []),
                'restarts': int(previous.get('restarts') or 0) + (1 if crashed else 0),
                'recovered_crashed_runner': crashed,
            }
            state['runner'].pop('exit_code', None)
            state['runner'].pop('finished_at_ns', None)
            return state
        self.runtime.store.update_state(update)

    def _record_progress(self, result, iterations, reason):
        def update(state):
            state.setdefault('runner', {})
            state['runner'].update({'status': 'running', 'iterations': iterations, 'reason': reason,
                                    'last_flows': result.get('flows', {}), 'updated_at_ns': time.time_ns()})
            return state
        self.runtime.store.update_state(update)

    def _finalize(self, result, reason, code, *, status, error=None):
        def update(state):
            state.setdefault('runner', {})
            errors = list(state['runner'].get('errors') or [])
            if error is not None:
                errors = (errors + [error])[-10:]
            state['runner'].update({'status': status, 'reason': reason, 'exit_code': code,
                                    'errors': errors, 'finished_at_ns': time.time_ns()})
            return state
        final = self.runtime.store.update_state(update)
        summary = {
            'run_id': final.get('run_id'),
            'runner': final.get('runner', {}),
            'flows': (result or {}).get('flows', {}),
            'tasks': (result or {}).get('tasks', {}),
            'cleanup': (result or {}).get('cleanup', {}),
        }
        return summary, code
