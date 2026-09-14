'''
GBit Shell - Job control

Tracks child processes started by the shell so they can be listed
(`jobs`), brought back (`fg`), continued in the background (`bg`),
signalled (`kill`) and waited for (`wait`).

POSIX gets the full experience: children run in their own process
group, Ctrl+Z suspends them, and the terminal is handed over to the
foreground job. On Windows only background jobs (`&`) are supported,
because the platform has no notion of process suspension by signal.
'''
import os
import sys
import time
import signal
import subprocess
from typing import Dict, List, Optional, Tuple

POSIX = os.name != "nt"

RUNNING = "running"
STOPPED = "stopped"
DONE = "done"
FAILED = "failed"

FINISHED_STATES = (DONE, FAILED)


# ======================================================================
# Process spawning helpers
# ======================================================================

RESET_IN_CHILD = ("SIGTSTP", "SIGTTOU", "SIGTTIN", "SIGINT", "SIGQUIT")


def _child_setup() -> None:
    """Runs in the child between fork and exec (POSIX only).

    Two things matter here:
      * the child gets its own process group, so signals can target it;
      * signals the shell ignores are reset to their default action --
        an ignored SIGTSTP is inherited through exec, which would make
        Ctrl+Z silently do nothing.
    """
    os.setpgrp()
    for name in RESET_IN_CHILD:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):
            pass


def popen_kwargs(background: bool = False) -> dict:
    """Extra Popen arguments that put the child in its own process group.

    A separate group is what makes it possible to signal the whole child
    tree (npm -> node, for example) instead of just the direct child.

    On Windows, CREATE_NEW_PROCESS_GROUP is ONLY used for background jobs.
    Foreground jobs must NOT have that flag, otherwise Ctrl+C (SIGINT)
    cannot reach the child — the flag creates a new group that is immune
    to CTRL_C_EVENT.  Background jobs instead get CREATE_NEW_PROCESS_GROUP
    and we use CTRL_BREAK_EVENT to interrupt them.
    """
    if POSIX:
        return {"preexec_fn": _child_setup}
    # Windows: foreground = same group as parent (so Ctrl+C works)
    #           background = new group (so we can signal via CTRL_BREAK_EVENT)
    if background:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags}
    return {"creationflags": 0}


def _proc_creationflags(proc: subprocess.Popen) -> int:
    """Return the creationflags stored on the Popen object (Windows)."""
    return getattr(proc, '_creationflags', 0) or 0


def group_of(proc: subprocess.Popen) -> Optional[int]:
    """Process-group id of a child, when the platform has one."""
    if not POSIX:
        return None
    try:
        return os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return None


def send(proc: subprocess.Popen, sig) -> bool:
    """Send a signal to the child's whole group, falling back to the pid."""
    if proc.poll() is not None:
        return False
    if POSIX:
        pgid = group_of(proc)
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
                return True
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            os.kill(proc.pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False
    # Windows: signals are limited.  SIGINT maps to CTRL_C_EVENT which
    # only reaches processes in the same console group.  For background
    # jobs (CREATE_NEW_PROCESS_GROUP) we must use CTRL_BREAK_EVENT
    # instead — the caller (interrupt()) is responsible for picking
    # the right signal; we just deliver it.
    try:
        proc.send_signal(sig)
        return True
    except (OSError, ValueError):
        try:
            proc.terminate()
            return True
        except OSError:
            return False


def interrupt(proc: subprocess.Popen) -> bool:
    """Ask a foreground child (and its whole tree) to stop, Ctrl+C style.

    On Windows, foreground jobs (same process group) receive SIGINT fine.
    Background jobs (CREATE_NEW_PROCESS_GROUP) need CTRL_BREAK_EVENT.
    """
    if not POSIX:
        creationflags = _proc_creationflags(proc)
        cnpg = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creationflags & cnpg:
            # Background job in its own group — SIGINT won't reach it;
            # use CTRL_BREAK_EVENT instead.
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", 1)
            return send(proc, ctrl_break)
        # Foreground job shares the parent's console group;
        # normal SIGINT / CTRL_C_EVENT works.
        return send(proc, signal.SIGINT)
    return send(proc, signal.SIGINT)


def kill_tree(proc: subprocess.Popen) -> bool:
    """Force-kill a child and every process it spawned (npm -> node ...)."""
    if proc.poll() is not None:
        return False
    if POSIX:
        if send(proc, signal.SIGKILL):
            return True
        try:
            proc.kill()
            return True
        except OSError:
            return False
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        try:
            proc.kill()
            return True
        except OSError:
            return False




# ======================================================================
# Terminal ownership (POSIX foreground jobs)
# ======================================================================

def _tty_fd() -> Optional[int]:
    if not POSIX:
        return None
    for fd in (0, 1, 2):
        try:
            if os.isatty(fd):
                return fd
        except OSError:
            continue
    return None


def give_terminal_to(proc: subprocess.Popen) -> Optional[int]:
    """Hand the controlling terminal to a child; return the old owner."""
    fd = _tty_fd()
    if fd is None:
        return None
    pgid = group_of(proc)
    if pgid is None:
        return None
    try:
        previous = os.tcgetpgrp(fd)
    except OSError:
        return None
    handler = signal.getsignal(signal.SIGTTOU)
    try:
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(fd, pgid)
    except (OSError, ValueError):
        return None
    finally:
        try:
            signal.signal(signal.SIGTTOU, handler)
        except (OSError, ValueError):
            pass
    return previous


def reclaim_terminal(previous: Optional[int]) -> None:
    """Give the terminal back to the shell after a foreground job."""
    if previous is None:
        return
    fd = _tty_fd()
    if fd is None:
        return
    handler = signal.getsignal(signal.SIGTTOU)
    try:
        signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.tcsetpgrp(fd, previous)
    except (OSError, ValueError):
        pass
    finally:
        try:
            signal.signal(signal.SIGTTOU, handler)
        except (OSError, ValueError):
            pass


def prepare_shell_signals() -> None:
    """Make the shell itself immune to terminal job-control signals.

    Without this, Ctrl+Z at the prompt would suspend GBit Shell instead of
    the program it is running.
    """
    if not POSIX:
        return
    for name in ("SIGTSTP", "SIGTTOU", "SIGTTIN"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, signal.SIG_IGN)
        except (OSError, ValueError):
            pass


def claim_terminal() -> bool:
    """Try to make the shell's process group own the terminal."""
    fd = _tty_fd()
    if fd is None:
        return False
    try:
        pgid = os.getpgrp()
        if os.tcgetpgrp(fd) != pgid:
            os.tcsetpgrp(fd, pgid)
        return True
    except (OSError, ValueError):
        return False


# ======================================================================
# Waiting while allowing Ctrl+Z
# ======================================================================

def wait_allow_stop(proc: subprocess.Popen) -> Tuple[str, int]:
    """Wait for a child, but return as soon as it is *stopped*.

    Returns ("stopped", signal_number) or ("exited", exit_code).
    Plain Popen.wait() would block until the process really ends, which
    is exactly the wrong behaviour for Ctrl+Z.
    """
    if not POSIX:
        return "exited", proc.wait()

    while True:
        try:
            pid, status = os.waitpid(proc.pid, os.WUNTRACED)
        except ChildProcessError:
            # Someone else reaped it (or it was already collected)
            code = proc.poll()
            return "exited", 0 if code is None else code
        except InterruptedError:
            continue

        if os.WIFSTOPPED(status):
            return "stopped", os.WSTOPSIG(status)

        if os.WIFSIGNALED(status):
            code = -os.WTERMSIG(status)
        elif os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
        else:
            code = 0

        # Popen never saw the exit, so tell it what happened.
        proc.returncode = code
        return "exited", code


# ======================================================================
# Job / JobManager
# ======================================================================

class Job:
    """One child process tracked by the shell."""

    def __init__(self, jid: int, proc: subprocess.Popen, command: str,
                 background: bool = False, state: str = RUNNING):
        self.id = jid
        self.proc = proc
        self.command = command.strip()
        self.background = background
        self.state = state
        self.exit_code: Optional[int] = None
        self.started = time.time()
        self.finished: Optional[float] = None
        self.reported = False

    # ----------------------------------------------------------------
    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def elapsed(self) -> float:
        end = self.finished or time.time()
        return max(0.0, end - self.started)

    def is_active(self) -> bool:
        return self.state in (RUNNING, STOPPED)

    def mark_finished(self, code: Optional[int]) -> None:
        self.exit_code = 0 if code is None else code
        self.state = DONE if self.exit_code == 0 else FAILED
        self.finished = time.time()

    def __repr__(self) -> str:
        return f"<Job {self.id} {self.state} pid={self.pid} {self.command!r}>"


class JobManager:
    """Registry of jobs, addressable by %n, %+, %- or command prefix."""

    def __init__(self):
        self.jobs: Dict[int, Job] = {}
        self.current: Optional[int] = None
        self.previous: Optional[int] = None
        self._next_id = 1

    # ----------------------------------------------------------------
    def add(self, proc: subprocess.Popen, command: str,
            background: bool = False, state: str = RUNNING) -> Job:
        job = Job(self._next_id, proc, command, background=background,
                  state=state)
        self.jobs[job.id] = job
        self._next_id += 1
        self.promote(job)
        return job

    def promote(self, job: Job) -> None:
        """Make `job` the current one (%+), pushing the old one to %-."""
        if self.current != job.id:
            self.previous = self.current
            self.current = job.id

    # ----------------------------------------------------------------
    def all(self, include_finished: bool = True) -> List[Job]:
        ordered = [self.jobs[key] for key in sorted(self.jobs)]
        if include_finished:
            return ordered
        return [job for job in ordered if job.is_active()]

    def active(self) -> List[Job]:
        return self.all(include_finished=False)

    def has_stopped(self) -> bool:
        return any(job.state == STOPPED for job in self.jobs.values())

    def stopped(self) -> List[Job]:
        return [job for job in self.all() if job.state == STOPPED]

    # ----------------------------------------------------------------
    def get(self, spec: Optional[str] = None) -> Optional[Job]:
        """Resolve a job spec: None/%+ current, %- previous, %n, n, prefix."""
        if spec is None or spec in ("", "%", "%+", "%%"):
            return self.jobs.get(self.current) if self.current else None
        if spec == "%-":
            return self.jobs.get(self.previous) if self.previous else None

        token = spec[1:] if spec.startswith("%") else spec
        if token.isdigit():
            return self.jobs.get(int(token))

        # Command prefix match, most recent first
        for job in reversed(self.all()):
            if job.command.startswith(token):
                return job
        for job in reversed(self.all()):
            if token in job.command:
                return job
        return None

    # ----------------------------------------------------------------
    def poll(self) -> List[Job]:
        """Check every job; return the ones that finished just now."""
        finished: List[Job] = []
        for job in self.all():
            if job.state != RUNNING:
                continue
            code = job.proc.poll()
            if code is not None:
                job.mark_finished(code)
                finished.append(job)
        return finished

    def mark_stopped(self, job: Job) -> None:
        job.state = STOPPED
        job.background = True
        self.promote(job)

    def mark_running(self, job: Job, background: bool = True) -> None:
        job.state = RUNNING
        job.background = background
        self.promote(job)

    # ----------------------------------------------------------------
    def prune(self) -> None:
        """Forget jobs that finished and were already reported."""
        for jid in [k for k, job in self.jobs.items()
                    if job.state in FINISHED_STATES and job.reported]:
            del self.jobs[jid]
        if self.current not in self.jobs:
            self.current = None
        if self.previous not in self.jobs:
            self.previous = None
        if self.current is None and self.jobs:
            self.current = max(self.jobs)
        if self.previous == self.current:
            self.previous = None

    # ----------------------------------------------------------------
    def forget(self, job: Job) -> bool:
        """Drop a job from the table without touching the process."""
        if job.id not in self.jobs:
            return False
        del self.jobs[job.id]
        if self.current == job.id:
            self.current = None
        if self.previous == job.id:
            self.previous = None
        if self.current is None and self.jobs:
            self.current = max(self.jobs)
        if self.previous == self.current:
            self.previous = None
        return True

    # ----------------------------------------------------------------
    def resume(self, job: Job, background: bool = True) -> bool:
        """Continue a stopped job (SIGCONT)."""
        if job.proc.poll() is not None:
            job.mark_finished(job.proc.returncode)
            return False
        if POSIX and job.state == STOPPED:
            if not send(job.proc, signal.SIGCONT):
                return False
        self.mark_running(job, background=background)
        return True

    def signal_job(self, job: Job, sig) -> bool:
        return send(job.proc, sig)

    def suspend(self, job: Job) -> bool:
        """Stop a running job (SIGTSTP). POSIX only."""
        if not POSIX or job.proc.poll() is not None:
            return False
        if not send(job.proc, signal.SIGTSTP):
            return False
        self.mark_stopped(job)
        return True

    # ----------------------------------------------------------------
    def terminate_all(self, grace: float = 2.0) -> None:
        """Politely stop every live job, then force what refuses to die."""
        live = [job for job in self.all() if job.proc.poll() is None]
        for job in live:
            if POSIX and job.state == STOPPED:
                send(job.proc, signal.SIGCONT)
            send(job.proc, signal.SIGTERM if POSIX else signal.SIGTERM)

        deadline = time.time() + grace
        for job in live:
            remaining = max(0.0, deadline - time.time())
            try:
                job.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    job.proc.kill()
                    job.proc.wait(timeout=1)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            except OSError:
                pass
            job.mark_finished(job.proc.poll())

    # ----------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.jobs)

    def __iter__(self):
        return iter(self.all())
