"""Dependency-free, flushed progress lines for PowerShell and redirected logs."""
import sys
import time


def duration(seconds):
    if seconds is None:
        return '--:--:--'
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


class Progress:
    def __init__(self, label, total, completed=0, errors=0, every=1, stream=None, clock=None):
        if not 0 <= errors <= completed <= total or every < 1:
            raise ValueError('Invalid progress counts')
        self.label, self.total = label, total
        self.completed = self.initial = completed
        self.errors, self.every = errors, every
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock if clock is not None else time.monotonic
        self.started = self.clock()
        self.current = None
        self.emit('READY' if completed < total else 'ALL_SAVED')

    def emit(self, status):
        elapsed = max(0, self.clock() - self.started)
        newly_saved = self.completed - self.initial
        eta = (elapsed / newly_saved * (self.total-self.completed)) if newly_saved else None
        if self.completed == self.total:
            eta = 0
        percent = 100*self.completed/self.total if self.total else 100
        print(f'{self.label} | {self.completed}/{self.total} ({percent:.1f}%)'
              f' | current={self.current or "-"} | {status}'
              f' | errors={self.errors} | resumed={self.initial}'
              f' | elapsed={duration(elapsed)} | ETA~{duration(eta)}',
              file=self.stream, flush=True)

    def start(self, identifier):
        if self.current is not None or self.completed >= self.total:
            raise ValueError('Progress item already active or total exhausted')
        self.current = str(identifier)
        if self.completed % self.every == 0:
            self.emit('RUNNING')

    def finish(self, status='SAVED', error=False):
        if self.current is None:
            raise ValueError('No active progress item')
        self.completed += 1
        self.errors += int(bool(error))
        if error or self.completed % self.every == 0 or self.completed == self.total:
            self.emit(status)
        self.current = None
