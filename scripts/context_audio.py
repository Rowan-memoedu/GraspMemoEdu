"""Bind source audio scripts to verified, content-addressed local MP3 assets."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu import context_production as cp


def mp3_metadata(path):
    completed = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_name',
                                '-of','json',str(path)], capture_output=True,text=True,encoding='utf-8',timeout=30)
    if completed.returncode:
        raise ValueError('Audio cannot be decoded')
    value = json.loads(completed.stdout)
    duration = float(value.get('format',{}).get('duration',0))
    if not .2 <= duration <= 300 or not any(s.get('codec_name') == 'mp3' for s in value.get('streams',[])):
        raise ValueError('Expected MP3 audio of 0.2..300 seconds')
    return {'duration_seconds':duration,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes':path.stat().st_size}


def bind(source, output, *, executable='speech-kit', dry_run=False):
    document = cp.read(source)
    if document.get('schema') != 2:
        raise ValueError('Structured Topic schema 2 required')
    value = deepcopy(document)
    pending = [(t,s,c) for t in value['topics'] for s in t['sections'] for c in s['flashcards'] if c.get('audio_source')]
    if dry_run:
        return {'pending_cards':len(pending),'scripts':len({c['audio_source']['transcript'] for _,_,c in pending})}
    receipts = []
    for topic, section, card in pending:
        spec = card['audio_source']
        if set(spec) != {'mode','transcript'} or spec['mode'] not in ('listen','dictation','read_aloud','retell'):
            raise ValueError('Invalid audio source')
        cp.text(spec['transcript'],10000)
        script_key = cp.digest({'provider':'mimo','voice':'Mia','text':spec['transcript']})
        folder = output.parent / 'media-production' / script_key
        cp.save(folder/'job.json',{'provider':'mimo','voice':'Mia','text':spec['transcript']})
        text_path = folder/'input.txt'
        if text_path.exists() and text_path.read_text(encoding='utf-8') != spec['transcript']:
            raise ValueError('Saved audio text changed')
        if not text_path.exists(): text_path.write_text(spec['transcript'],encoding='utf-8')
        audio = folder/'voice.mp3'; receipt = folder/'provider.json'
        if not receipt.exists():
            if audio.exists() or (folder/'started.json').exists():
                raise ValueError('Previous synthesis outcome unresolved; inspect retained evidence before repeating')
            cp.save(folder/'started.json',{'script_sha256':script_key})
            print(card['id']+' synthesizing',flush=True)
            completed = subprocess.run([executable,'--json','tts','synthesize','--text-file',str(text_path),'--out',str(audio)],
                                        capture_output=True,text=True,encoding='utf-8',timeout=600)
            # The maintained provider CLI returns credential-free result metadata.
            try: result = json.loads(completed.stdout)
            except json.JSONDecodeError: raise ValueError('TTS returned no JSON receipt') from None
            cp.save(receipt,result)
            if completed.returncode or result.get('ok') is not True:
                raise ValueError('TTS failed; retain its receipt and source')
        result = cp.read(receipt)
        if result.get('ok') is not True or not audio.exists():
            raise ValueError('TTS receipt does not confirm audio')
        metadata = mp3_metadata(audio)
        relative = 'audio/'+metadata['sha256']+'.mp3'
        destination = output.parent / relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists() and destination.read_bytes() != audio.read_bytes():
            raise ValueError('Content-addressed audio differs')
        if not destination.exists(): shutil.copyfile(audio,destination)
        card['audio'] = {'version':1,'asset':relative,'mime_type':'audio/mpeg',
                         'mode':spec['mode'],'transcript':spec['transcript']}
        del card['audio_source']
        receipts.append({'topic_id':topic['id'],'card_id':card['id'],'asset':relative,
                         'mode':spec['mode'],**metadata,'provider_receipt':str(receipt)})
        print(card['id']+' bound '+format(metadata['duration_seconds'],'.1f')+'s',flush=True)
    # Existing bound assets also remain portable if the destination is new.
    for topic in value['topics']:
        for section in topic['sections']:
            for card in section['flashcards']:
                if card.get('audio'):
                    asset = card['audio']['asset']
                    if (not re.fullmatch(r'audio/[0-9a-f]{64}\.mp3',asset)
                            or card['audio'].get('mime_type') != 'audio/mpeg'):
                        raise ValueError('Unsafe bound audio path or MIME')
                    target = output.parent/asset
                    if not target.exists():
                        original = source.parent/asset
                        if not original.is_file(): raise ValueError('Missing bound audio asset')
                        if mp3_metadata(original)['sha256'] != Path(asset).stem:
                            raise ValueError('Existing audio bytes differ from their filename')
                        target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(original,target)
    cp.save(output,value)
    cp.save(output.parent/'audio-manifest.json',{'provider':'mimo','voice':'Mia','files':receipts})
    return {'bound_cards':len(receipts),'output':str(output)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--speech-kit',default='speech-kit')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    print(json.dumps(bind(args.input,args.output,executable=args.speech_kit,dry_run=args.dry_run),ensure_ascii=False))


if __name__=='__main__': main()
