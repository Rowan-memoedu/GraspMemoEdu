import importlib.util
from pathlib import Path
import sys
import threading


class ExistingJudge:
    """Keep credentials and the existing judge's prompt entirely on the server."""

    def __init__(self, project: Path):
        self.project = Path(project)
        self._judge = None
        self._lock = threading.Lock()

    def __call__(self, question, reference_answer, student_answer):
        try:
            with self._lock:
                if self._judge is None:
                    path = self.project / 'math_answer_judge.py'
                    spec = importlib.util.spec_from_file_location('math_answer_judge', path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[spec.name] = module
                    spec.loader.exec_module(module)
                    self._judge = module.AnswerJudge(module.JudgeConfig.from_environment())
            result = self._judge.judge(question, reference_answer, student_answer)
            # Do not return raw output, configuration or API exception details.
            return {'status': result.status, 'correct': result.correct,
                    'reason': result.reason if result.status == 'judged' else '判题服务暂时不可用，请重试；本次未计入作答。'}
        except Exception:
            return {'status': 'error', 'correct': None,
                    'reason': '判题服务暂时不可用，请检查本机配置或稍后重试；本次未计入作答。'}
