"""Optional lossless transport encoding of already-validated bank PNGs."""
import hashlib
import io
import json
from pathlib import Path
import sys

from PIL import Image


def digest(data):
    return hashlib.sha256(data).hexdigest()


def optimize(records, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for row in records:
        original = Path(row['source']).read_bytes()
        if digest(original) != row['sha256']:
            raise ValueError('Source image changed during compilation')
        with Image.open(io.BytesIO(original)) as image:
            if image.format != 'PNG' or image.size != (row['width'], row['height']):
                raise ValueError('Image dimensions differ from validated source')
            rgba = image.convert('RGBA')
            pixels = rgba.tobytes()
            metadata = {key: image.info[key] for key in ('icc_profile', 'exif') if key in image.info}
            # Preserve 16-bit source precision; optimize only 8-bit-or-less PNGs.
            if original[24] > 8:
                encoded, mime = original, 'image/png'
            else:
                stream = io.BytesIO()
                rgba.save(stream, format='WEBP', lossless=True, exact=True, method=6, **metadata)
                candidate = stream.getvalue()
                with Image.open(io.BytesIO(candidate)) as decoded:
                    if decoded.size != image.size or decoded.convert('RGBA').tobytes() != pixels:
                        raise ValueError('Lossless image round-trip changed pixels')
                encoded, mime = (candidate, 'image/webp') if len(candidate) < len(original) else (original, 'image/png')
        suffix = '.webp' if mime == 'image/webp' else '.png'
        target = output / (digest(encoded) + suffix)
        if target.exists():
            if target.read_bytes() != encoded:
                raise ValueError('Existing derived image changed')
        else:
            target.write_bytes(encoded)
        results.append({'name': row['name'], 'source_sha256': row['sha256'],
                        'sha256': digest(encoded), 'pixels_sha256': digest(pixels),
                        'width': row['width'], 'height': row['height'],
                        'path': str(target.resolve()), 'mime': mime, 'size': len(encoded),
                        'source_size': len(original), 'pixels_equal': True})
    report = json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    receipt = output / (digest(report) + '.json')
    if receipt.exists() and receipt.read_bytes() != report:
        raise ValueError('Existing image receipt changed')
    if not receipt.exists():
        receipt.write_bytes(report)
    return results


if __name__ == '__main__':
    print(json.dumps(optimize(json.load(sys.stdin), sys.argv[1]), ensure_ascii=False))
