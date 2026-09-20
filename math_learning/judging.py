"""The existing judge contract shared by Lesson and review submissions."""


def judge_answer(judge, question, answer):
    # Call this outside any database transaction.
    try:
        if judge is None:
            raise RuntimeError('Judge not configured')
        verdict = judge(question['question'] + '\n作答约束：' + question['constraints'],
                        question['reference_answer'], answer)
        if not isinstance(verdict, dict):
            verdict = {'status': verdict.status, 'correct': verdict.correct, 'reason': verdict.reason}
        if (verdict.get('status') == 'judged' and type(verdict.get('correct')) is bool
                and isinstance(verdict.get('reason'), str)):
            return verdict['correct'], verdict['reason'][:2000]
    except Exception:
        pass
    return None, '判题服务暂时不可用，请重试；本次未计入作答。'
