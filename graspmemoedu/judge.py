import importlib.util
import os
from pathlib import Path
import sys
import threading


def independent_settings():
    """Only dedicated judge variables; live user settings override inherited values."""
    names = ('GRASPMEMOEDU_JUDGE_API_KEY', 'GRASPMEMOEDU_JUDGE_BASE_URL', 'GRASPMEMOEDU_JUDGE_MODEL')
    values = {name: os.environ.get(name, '') for name in names}
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                for name in names:
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                        values[name] = value if isinstance(value, str) else ''
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
    if any(not values[name].strip() for name in names):
        raise ValueError('Dedicated judge environment is incomplete')
    return dict(api_key=values[names[0]].strip(), base_url=values[names[1]].strip(), model=values[names[2]].strip())


class ExistingJudge:
    """Keep credentials and the existing judge's prompt entirely on the server."""

    def __init__(self, project: Path):
        self.project = Path(project)
        self._module = None
        self._lock = threading.Lock()

    def __call__(self, question, reference_answer, student_answer):
        try:
            with self._lock:
                if self._module is None:
                    path = self.project / 'math_answer_judge.py'
                    spec = importlib.util.spec_from_file_location('math_answer_judge', path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    spec.loader.exec_module(module)
                    self._module = module
                # Never use from_environment(): it falls back to Codex outsourcing.
                # Read each time so edits in Windows user variables need no restart.
                judge = self._module.AnswerJudge(self._module.JudgeConfig(**independent_settings()))
            result = judge.judge(question, reference_answer, student_answer)
            # Do not return raw output, configuration or API exception details.
            return {'status': result.status, 'correct': result.correct,
                    'reason': result.reason if result.status == 'judged' else '判题服务暂时不可用，请重试；本次未计入作答。'}
        except Exception:
            return {'status': 'error', 'correct': None,
                    'reason': '判题服务暂时不可用，请检查本机配置或稍后重试；本次未计入作答。'}
