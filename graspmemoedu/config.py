from dataclasses import dataclass, field
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get('GRASPMEMOEDU_DATA', 'D:/AppData/GraspMemoEdu')))
    course_path: Path | None = None
    catalog_path: Path | None = None
    guide_path: Path | None = None
    judge_project: Path = field(default_factory=lambda: Path(os.environ.get('GRASPMEMOEDU_JUDGE_PROJECT', 'D:/Projects/MathAnswerJudge')))
    host: str = '127.0.0.1'
    port: int = 8767
    allowed_origins: tuple[str, ...] = ()
    public_api_origin: str | None = None
    instance_id: str = field(default_factory=lambda: secrets.token_hex(16))

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.judge_project = Path(self.judge_project)
        self.course_path = Path(self.course_path or self.data_dir / 'courses/digits-to-letters.json')
        self.catalog_path = Path(self.catalog_path or self.data_dir / 'catalog.json')
        self.guide_path = Path(self.guide_path or self.data_dir / 'guide/guide.md')
        if self.host != '127.0.0.1':
            raise ValueError('This release only binds to 127.0.0.1')
        if not 1024 <= self.port <= 65535:
            raise ValueError('Invalid local port')
        origins = [*self.allowed_origins]
        if self.public_api_origin is not None:
            origins.append(self.public_api_origin)
        for origin in origins:
            if not isinstance(origin, str) or origin != origin.strip():
                raise ValueError('Origins must be exact HTTPS origins')
            url = urlsplit(origin)
            if (url.scheme != 'https' or not url.hostname or url.path or url.query
                    or url.fragment or url.username is not None or url.password is not None or '*' in origin
                    or '\\' in origin or any(character.isspace() for character in origin)
                    or origin != f'https://{url.netloc}' or url.netloc.endswith(':')):
                raise ValueError('Remote origins must be exact HTTPS origins without paths')
            try:
                url.port
            except ValueError:
                raise ValueError('Invalid origin port')

    @classmethod
    def load(cls, data_dir=None):
        root = Path(data_dir or os.environ.get('GRASPMEMOEDU_DATA', 'D:/AppData/GraspMemoEdu'))
        path = root / 'settings.json'
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        allowed = {'course_path', 'catalog_path', 'guide_path', 'judge_project', 'port', 'allowed_origins', 'public_api_origin'}
        if set(data) - allowed:
            raise ValueError('Unknown local settings field')
        return cls(data_dir=root, **data)
