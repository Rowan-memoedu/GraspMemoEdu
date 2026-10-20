"""Reuse the validated Hyperskill→RemNote media path, without writing RemNote."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urljoin

sys.path.insert(0, os.environ.get("HYPERSKILL_REMNOTE_ROOT", r"D:\Projects\HyperskillRemNoteWorkflow"))
from outline import Assets
from bs4 import BeautifulSoup


def embed(request):
    directory = Path(request["directory"]).resolve()
    assets = Assets(directory)
    cached = {}
    manifest_file = request.get("manifest")
    if manifest_file and Path(manifest_file).is_file():
        cached = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
    result = []
    for source in request["html"]:
        soup = BeautifulSoup(source, "html.parser")
        for image in soup.find_all("img"):
            url = urljoin(request.get("base_url", "https://hyperskill.org/"), image.get("src", ""))
            if url.startswith("data:image/"):
                continue
            if not image.get("src"):
                raise ValueError("Source image lacks src")
            item = cached.get(url)
            if item:
                original = Path(item["localPath"])
                raw = original.read_bytes()
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("Cached RemNote image hash differs")
                target = directory / original.name
                if not target.exists():
                    shutil.copyfile(original, target)
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".webp": "image/webp", ".gif": "image/gif"}[target.suffix.lower()]
                import base64
                data = {"mime": mime, "base64": base64.b64encode(raw).decode()}
                item = {**item, "localPath": str(target)}
                if item.get("conversion"):
                    conversion = item["conversion"]
                    svg = Path(conversion["sourcePath"])
                    if hashlib.sha256(svg.read_bytes()).hexdigest() != conversion["sourceSha256"]:
                        raise ValueError("Cached original SVG hash differs")
                    copied = directory / svg.name
                    if not copied.exists():
                        shutil.copyfile(svg, copied)
                    item["conversion"] = {**conversion, "sourcePath": str(copied)}
                assets.manifest[url] = item
            else:
                asset_id = assets.add(url)
                data = assets.assets[asset_id]
            image["src"] = f'data:{data["mime"]};base64,{data["base64"]}'
        result.append(str(soup))
    return {"html": result, "assets": assets.manifest}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        response = embed(request)
    print(json.dumps(response, ensure_ascii=False))
