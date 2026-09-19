from dataclasses import dataclass, field
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get('MATH_LEARNING_DATA', 'D:/AppData/MathLearningWeb')))
    course_path: Path | None = None
    judge_project: Path = field(default_factory=lambda: Path(os.environ.get('MATH_JUDGE_PROJECT', 'D:/Projects/MathAnswerJudge')))
    host: str = '127.0.0.1'
    port: int = 8767
    allowed_origins: tuple[str, ...] = ()
    instance_id: str = field(default_factory=lambda: secrets.token_hex(16))

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.judge_project = Path(self.judge_project)
        self.course_path = Path(self.course_path or self.data_dir / 'courses/digits-to-letters.json')
        if self.host != '127.0.0.1':
            raise ValueError('This release only binds to 127.0.0.1')
        if not 1024 <= self.port <= 65535:
            raise ValueError('Invalid local port')
        for origin in self.allowed_origins:
            url = urlsplit(origin)
            if (url.scheme != 'https' or not url.hostname or url.path or url.query
                    or url.fragment or url.username or url.password or '*' in origin):
                raise ValueError('Remote origins must be exact HTTPS origins without paths')

    @classmethod
    def load(cls, data_dir=None):
        root = Path(data_dir or os.environ.get('MATH_LEARNING_DATA', 'D:/AppData/MathLearningWeb'))
        path = root / 'settings.json'
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        allowed = {'course_path', 'judge_project', 'port', 'allowed_origins'}
        if set(data) - allowed:
            raise ValueError('Unknown local settings field')
        return cls(data_dir=root, **data)
