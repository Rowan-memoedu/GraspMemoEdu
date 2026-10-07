"""Private, immutable Q&A images with the conversation's current authorization."""
import base64
import binascii
from contextlib import closing
import hashlib
import io
import re
import uuid
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from .service import DomainError, now

MAX_BYTES = 2 * 1024 * 1024
IMAGE_REF = re.compile(r'help-image:([0-9a-f]{32})')


class HelpImageUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_id: str = Field(min_length=1, max_length=128)
    is_main: bool = Field(default=False, strict=True)
    data: str = Field(min_length=1, max_length=MAX_BYTES * 4 // 3 + 8)


def normalize(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise DomainError('invalid_help_image', '图片过大，请使用小于 2 MB 的图片。', 422)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG', 'JPEG', 'WEBP') or max(image.size) > 2048:
                raise ValueError('Invalid format or dimensions')
            image.load()
            # Re-encode pixels only: discard EXIF, metadata, trailing data and animation.
            mode = 'RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB'
            clean = Image.new(mode, image.size)
            clean.paste(image.convert(mode))
            output = io.BytesIO()
            clean.save(output, format='PNG', optimize=True)
            content = output.getvalue()
            if len(content) > MAX_BYTES:
                raise ValueError('Normalized image too large')
            return content
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise DomainError('invalid_help_image', '无法读取图片，请使用 PNG、JPEG 或 WebP 图片。', 422)


def archive_images(con, data_dir):
    """Use only rows from the same database snapshot as the backup."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='help_images'").fetchone():
        return {}
    result = {}
    for ident, digest in con.execute('SELECT id,sha256 FROM help_images'):
        if not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('Invalid help image identity')
        name = 'help-images/' + ident + '.png'
        raw = (Path(data_dir) / name).read_bytes()
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Help image differs from its saved hash')
        result[name] = raw
    return result


class HelpImages:
    def __init__(self, store):
        self.store = store
        self.root = store.access_store.data_dir / 'help-images'
        with store.access_store._transaction() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS help_images (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL, is_main INTEGER NOT NULL CHECK(is_main IN (0,1)),
                author_id TEXT NOT NULL REFERENCES access_principals(learner_id),
                sha256 TEXT NOT NULL, created_at TEXT NOT NULL)''')

    def authorize(self, con, access, report, is_main):
        return (self.store.main_threads if is_main else self.store.threads).authorized(con, access, report)

    def upload(self, access, values):
        with self.store.access_store._transaction() as con:
            self.authorize(con, access, values['report_id'], values['is_main'])
            try:
                raw = base64.b64decode(values['data'], validate=True)
            except (ValueError, binascii.Error):
                raise DomainError('invalid_help_image', '无法读取图片，请重新添加。', 422)
            raw = normalize(raw)
            ident = uuid.uuid4().hex
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / (ident + '.png')).open('xb') as file:
                file.write(raw)
            con.execute('INSERT INTO help_images VALUES (?,?,?,?,?,?)',
                (ident, values['report_id'], int(values['is_main']), access['learner_id'], hashlib.sha256(raw).hexdigest(), now()))
        return {'id': ident, 'markdown': '![](help-image:' + ident + ')'}

    def get(self, access, ident):
        self.store._viewer(access)
        with closing(self.store.access_store._connect()) as con:
            row = con.execute('SELECT * FROM help_images WHERE id=?', (ident,)).fetchone()
            if not row:
                raise DomainError('unknown_help_image', '未找到此图片。', 404)
            self.authorize(con, access, row['report_id'], row['is_main'])
            try:
                raw = (self.root / (ident + '.png')).read_bytes()
            except OSError:
                raise DomainError('invalid_help_image', '图片暂时无法读取。', 409)
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise DomainError('invalid_help_image', '图片暂时无法读取。', 409)
            return {'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')}

    def validate_message(self, con, text, report, is_main):
        for ident in set(IMAGE_REF.findall(text)):
            if not con.execute('SELECT 1 FROM help_images WHERE id=? AND report_id=? AND is_main=?',
                    (ident, report, int(is_main))).fetchone():
                raise DomainError('unknown_help_image', '此图片不属于当前对话，请重新添加。', 422)
