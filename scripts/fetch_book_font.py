"""Vendor the official OFL Noto Serif SC webfont, without a remote runtime dependency."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT=Path(__file__).resolve().parents[1]/'web/vendor/book-font'
URL='https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400..700&display=swap'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=90) as response:
        return response.read()

def main():
    css=fetch(URL).decode(); urls=sorted(set(re.findall(r'https://[^)\s]+\.woff2',css)))
    if not urls:raise ValueError('No official webfont subsets received')
    ROOT.mkdir(parents=True,exist_ok=True)
    def one(url):
        name=hashlib.sha256(url.encode()).hexdigest()[:16]+'.woff2'; path=ROOT/name
        if not path.exists():path.write_bytes(fetch(url))
        print('Font subset '+name,flush=True)
        return url, name, hashlib.sha256(path.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=6) as pool:files=list(pool.map(one,urls))
    for url,name,_ in files:css=css.replace(url,'./'+name)
    (ROOT/'font.css').write_text(css,encoding='utf-8')
    license_url='https://raw.githubusercontent.com/notofonts/noto-cjk/main/Serif/LICENSE'
    (ROOT/'LICENSE').write_bytes(fetch(license_url))
    (ROOT/'sources.json').write_text(json.dumps({'css':URL,'license':license_url,'files':[
        {'url':u,'name':n,'sha256':s} for u,n,s in files]},indent=2),encoding='utf-8')
    manifest=ROOT.parents[2]/'scripts/book-font-assets.json'
    manifest.write_text(json.dumps({'files':{'vendor/book-font/'+p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(ROOT.iterdir()) if p.is_file()}},indent=2)+'\n',encoding='utf-8')
    print('Vendored '+str(len(files))+' subsets',flush=True)

if __name__=='__main__':main()
