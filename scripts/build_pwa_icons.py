"""Render the existing GraspMemoEdu G mark as opaque, installable PNG icons."""
from pathlib import Path
from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parents[1] / 'web'


def render(size, maskable=False):
    scale = 4
    image = Image.new('RGB', (size * scale, size * scale), '#faf8f3')
    draw = ImageDraw.Draw(image)
    # Center the existing 40-unit mark inside the maskable icon's safe circle.
    extent = .64 if maskable else .84
    unit = size * scale * extent / 40
    offset = size * scale * (1 - extent) / 2
    def p(value): return round(offset + value * unit)
    stroke = max(1, round(2.7 * unit))
    draw.arc((p(7), p(8), p(33), p(34)), 42, 318, fill='#785744', width=stroke)
    draw.line([(p(29), p(27)), (p(29), p(21)), (p(20), p(21))], fill='#785744', width=stroke)
    draw.line([(p(26), p(7)), (p(26), p(12))], fill='#785744', width=max(1, round(1.2 * unit)))
    draw.line([(p(24), p(9)), (p(29), p(9))], fill='#785744', width=max(1, round(1.2 * unit)))
    image.resize((size, size), Image.Resampling.LANCZOS).save(WEB / f'icon-{"maskable-" if maskable else ""}{size}.png')


if __name__ == '__main__':
    for size in (180, 192, 512): render(size)
    render(512, True)
    print('PASS four opaque PNG application icons')
