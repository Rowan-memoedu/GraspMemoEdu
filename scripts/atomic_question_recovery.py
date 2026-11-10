"""Local batch recovery and resource bounds; never edits learning candidates."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from scripts import atomic_question_trial as trial


@contextmanager
def run_lock(root):
    """An OS lock releases on process exit, including a crash."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.production.lock').open('a+b') as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def checkpoint(path, value):
    """Only replace aggregate status, retaining each previous receipt by hash."""
    if path.exists():
        prior = trial.read(path)
        if prior == value:
            return
        archive = path.parent / 'result-history' / (trial.digest(prior) + '.json')
        if not archive.exists():
            trial.save(archive, prior)
        temporary = path.with_name(path.name + '.pending')
        trial.save(temporary, value)
        os.replace(temporary, path)
    else:
        trial.save(path, value)


def available_memory_mb():
    if os.name != 'nt':
        return None
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    *[(name, ctypes.c_ulonglong) for name in
                      ('total', 'available', 'total_page', 'available_page',
                       'total_virtual', 'available_virtual', 'extended')]]
    state = Memory(); state.length = ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise OSError('Cannot inspect available memory')
    return min(state.available, state.available_page) // (1024 * 1024)


def worker_capacity(requested, available=None):
    # Reserve space for the desktop and other tasks. A worker starts several
    # existing Node/Codex subprocesses, so bound the entire worker, not a thread.
    return requested if available is None else max(0, min(requested, (available - 3072) // 512))


def dispatch(items, worker, workers):
    remaining = iter(items)
    active = set()
    exhausted = False
    blocked_since = None
    # Fix a safe startup ceiling before processes have allocated their memory.
    ceiling = worker_capacity(workers, available_memory_mb())
    print(f'worker_limit={workers} initial_capacity={ceiling}', flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while active or not exhausted:
            free = available_memory_mb()
            if not active:
                ceiling = worker_capacity(workers, free)
            capacity = min(ceiling, len(active) + worker_capacity(workers, free))
            while not exhausted and len(active) < capacity:
                try:
                    item = next(remaining)
                except StopIteration:
                    exhausted = True
                    break
                active.add(pool.submit(worker, item))
            if active:
                blocked_since = None
                done, active = wait(active, timeout=5, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
            elif not exhausted:
                blocked_since = blocked_since or time.monotonic()
                if time.monotonic() - blocked_since > 300:
                    raise RuntimeError('Insufficient free memory; completed point receipts are retained')
                print(f'waiting_for_memory available_mb={free}', flush=True)
                time.sleep(5)


def recover_response(work, phase, client, diagnostics_root=None):
    """Recover a saved answer only with matching input and completed-turn evidence.

    A process exit error is not by itself proof that inference failed. Conversely,
    an answer without a completed turn must never be promoted to success.
    """
    response = work / (phase + '.response.json')
    request = work / (phase + '.request.json')
    started = work / (phase + '.started.json')
    if response.exists() or not request.exists() or not started.exists():
        return
    requested = trial.read(request)
    prompt = '\n\n'.join(m['content'] for m in requested['messages'] if m['role'] == 'user')
    instructions = '\n\n'.join(m['content'] for m in requested['messages'] if m['role'] in ('system', 'developer'))
    expected = hashlib.sha256(prompt.encode()).hexdigest()
    start = trial.read(started)['started_at']
    root = diagnostics_root or Path(os.environ.get('AI_PRODUCER_CODEX_WORK_DIR',
                                                   'D:/Caches/ProgressiveReading/codex-exec'))
    matches = []
    for path in root.glob('call-*/failure.json'):
        try:
            failure = trial.read(path)
            if (failure.get('user_payload_sha256') != expected or failure.get('model') != client.model
                    or failure.get('failed_at', '') < start[:19]):
                continue
            actual_instructions = (path.parent / 'instructions.md').read_text(encoding='utf-8')
            if not actual_instructions.endswith(instructions):
                continue
            matches.append((path, failure))
        except (OSError, ValueError):
            continue
    evidence = []
    for path, failure in matches:
        summary = failure.get('event_summary', [])
        complete = any(e['type'] == 'turn.completed' for e in summary)
        invalid = any(e['type'] == 'turn.failed' or
                      e['type'] == 'item.completed' and e.get('item_type') not in ('agent_message', 'reasoning', 'error')
                      for e in summary)
        output = path.parent / 'response.txt'
        evidence.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'error': failure['error'], 'completed_turn': complete})
        if complete and not invalid and output.exists() and output.read_text(encoding='utf-8').strip():
            content = output.read_text(encoding='utf-8')
            trial.save(response, {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                'provider': 'codex_exec', 'model': client.model, 'producer_identity': client.identity,
                'usage': {}, 'transport': {'completed_event': True, 'completion_evidence': 'preserved_codex_turn_completed',
                    'recovered_from': str(output), 'response_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                    'failure_evidence': evidence[-1]}})
            break
    receipt = work / (phase + '.reconciliation.json')
    if not receipt.exists():
        trial.save(receipt, {'request_sha256': hashlib.sha256(request.read_bytes()).hexdigest(),
                            'matches': evidence, 'response_recovered': response.exists()})
