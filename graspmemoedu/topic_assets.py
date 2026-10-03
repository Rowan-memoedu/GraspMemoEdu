"""Shared validation for local Topic PNG assets and portable backup blobs."""
import hashlib
from pathlib import Path, PurePosixPath
import re
import struct
import zlib

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
IMAGE_PATTERN = re.compile(r'!\[([^\]\n]*)\]\(([^)\n]+)\)')


def canonical_asset_path(name):
    if (not isinstance(name, str) or not name or len(name) > 1024
            or re.search(r'[\\:%?#<>"|*\x00-\x1f]', name)
            or PurePosixPath(name).is_absolute() or PurePosixPath(name).suffix.lower() != '.png'):
        raise ValueError('Unsupported relative PNG asset path')
    for part in name.split('/'):
        if (not part or part in ('.', '..') or part != part.strip() or part.endswith('.')
                or re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part, re.I)):
            raise ValueError('Unsafe PNG asset path component')
    return name


def read_local_asset(root, name):
    name = canonical_asset_path(name)
    root = Path(root).resolve()
    current = root
    for part in name.split('/'):
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('PNG asset links are not allowed')
    if not current.resolve().is_relative_to(root) or not current.is_file():
        raise ValueError('PNG asset leaves source directory or is not a file')
    if current.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('PNG asset exceeds size limit')
    return current.read_bytes()


def validate_png(raw):
    if (not isinstance(raw, bytes) or len(raw) < 45 or len(raw) > MAX_IMAGE_BYTES
            or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[8:16] != b'\x00\x00\x00\rIHDR'):
        raise ValueError('Invalid PNG header or size')
    width, height = struct.unpack('>II', raw[16:24])
    if not width or not height or max(width, height) > 10000 or width * height > 20000000:
        raise ValueError('PNG dimensions exceed limit')
    offset, compressed, ended = 8, [], False
    while offset + 12 <= len(raw):
        size = struct.unpack('>I', raw[offset:offset + 4])[0]
        end = offset + size + 12
        if end > len(raw):
            raise ValueError('Truncated PNG chunk')
        kind = raw[offset + 4:offset + 8]
        if zlib.crc32(raw[offset + 4:end - 4]) != struct.unpack('>I', raw[end - 4:end])[0]:
            raise ValueError('PNG checksum mismatch')
        if kind == b'IDAT':
            compressed.append(raw[offset + 8:end - 4])
        offset = end
        if kind == b'IEND':
            ended = size == 0 and offset == len(raw)
            break
    if not ended or not compressed:
        raise ValueError('Incomplete PNG')
    decoder = zlib.decompressobj()
    expanded = decoder.decompress(b''.join(compressed), 160 * 1024 * 1024 + 1)
    if len(expanded) > 160 * 1024 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid or oversized PNG compressed data')
    return width, height


def validate_source_assets(source_text, records):
    if not isinstance(source_text, str):
        raise ValueError('PNG source must be Markdown text')
    from .topic_format import source_text as normalized_source_text
    paths = [m.group(2) for m in IMAGE_PATTERN.finditer(normalized_source_text(source_text))]
    for name in paths:
        canonical_asset_path(name)
    expected = [record['path'] for record in records]
    if set(paths) != set(expected):
        raise ValueError('Source image references differ from asset inventory')
    for record in records:
        if 'occurrences' in record and record['occurrences'] != paths.count(record['path']):
            raise ValueError('Source image occurrence count mismatch')


def validate_assets(records, read, source=None):
    if not isinstance(records, list) or len(records) > 600:
        raise ValueError('Invalid PNG asset inventory')
    result, total, folded = {}, 0, set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid PNG asset metadata')
        name = canonical_asset_path(record.get('path'))
        if name.casefold() in folded:
            raise ValueError('Duplicate PNG asset path')
        folded.add(name.casefold())
        if (record.get('mime') != 'image/png' or type(record.get('size')) is not int
                or not 0 < record['size'] <= MAX_IMAGE_BYTES
                or not isinstance(record.get('sha256'), str)
                or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise ValueError('Invalid PNG asset metadata')
        total += record['size']
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Total PNG assets exceed package limit')
        raw = read(name)
        width, height = validate_png(raw)
        if len(raw) != record['size'] or hashlib.sha256(raw).hexdigest() != record['sha256']:
            raise ValueError('PNG asset hash or size mismatch')
        if ('width' in record and record['width'] != width) or ('height' in record and record['height'] != height):
            raise ValueError('PNG asset dimensions differ from metadata')
        result[name] = raw
    if source is not None:
        validate_source_assets(source, records)
    return result
