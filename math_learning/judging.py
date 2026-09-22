"""The existing judge contract shared by Lesson and review submissions."""
from .question_types import deterministic_verdict, semantic_arguments


def judge_answer(judge, question, answer):
    # Call this outside any database transaction.
    try:
        deterministic = deterministic_verdict(question, answer)
        if deterministic is not None:
            return deterministic
        if judge is None:
            raise RuntimeError('Judge not configured')
        verdict = judge(*semantic_arguments(question, answer))
        if not isinstance(verdict, dict):
            verdict = {'status': verdict.status, 'correct': verdict.correct, 'reason': verdict.reason}
        if (verdict.get('status') == 'judged' and type(verdict.get('correct')) is bool
                and isinstance(verdict.get('reason'), str)):
            return verdict['correct'], verdict['reason'][:2000]
    except Exception:
        pass
    return None, '判题服务暂时不可用，请重试；本次未计入作答。'
