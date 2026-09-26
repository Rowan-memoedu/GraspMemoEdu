"""Shared Lesson/review queue rules, independent of judging and FSRS."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PracticePolicy:
    version: str
    targets: tuple[int, ...]
    ratings: tuple[int, ...]

    def __post_init__(self):
        if (not self.version or len(self.targets) != len(self.ratings) or not self.targets
                or any(type(n) is not int or n < 1 for n in self.targets)
                or any(a >= b for a, b in zip(self.targets, self.targets[1:]))
                or any(type(n) is not int or n not in (1, 2, 3, 4) for n in self.ratings)):
            raise ValueError('Invalid practice policy')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(value['version'], tuple(value['targets']), tuple(value['ratings']))


LESSON_POLICY = PracticePolicy('lesson-v1', (2, 3, 5, 7), (4, 3, 2, 1))
REVIEW_POLICY = PracticePolicy('review-v1', (1, 3, 5), (4, 2, 1))


def apply_practice_result(attempt, correct, policy):
    """Update counts before testing completion; return (must_pause, rating)."""
    if type(correct) is not bool:
        raise ValueError('Only a successful boolean judgement may advance practice')
    attempt['practice_answered_count'] += 1
    if not correct:
        attempt['practice_error_count'] += 1
    errors = attempt['practice_error_count']
    attempt['practice_target_count'] = policy.targets[min(errors, len(policy.targets) - 1)]
    if errors >= len(policy.targets):
        return True, None
    if attempt['practice_answered_count'] >= attempt['practice_target_count']:
        return False, policy.ratings[errors]
    return False, None
