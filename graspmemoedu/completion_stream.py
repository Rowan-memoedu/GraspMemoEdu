"""Read one provider SSE completion; never retry or use an unfinished stream."""
import json


def read_completion_stream(response):
    result, content, reasoning = {}, [], []
    finish, done, event = None, False, []

    def consume():
        nonlocal finish, done
        if not event:
            return
        data = '\n'.join(event)
        event.clear()
        if data == '[DONE]':
            done = True
            return
        chunk = json.loads(data)
        if chunk.get('error'):
            raise ConnectionError('Provider returned a streaming error')
        for field in ('id', 'model', 'created'):
            if field in chunk:
                if field in result and result[field] != chunk[field]:
                    raise ConnectionError('Completion identity changed during stream')
                result[field] = chunk[field]
        if chunk.get('usage') is not None:
            result['usage'] = chunk['usage']
        for choice in chunk.get('choices', []):
            if choice.get('index') != 0 or len(chunk['choices']) != 1:
                raise ConnectionError('Unexpected streaming choice')
            delta = choice.get('delta', {})
            content.append(delta.get('content') or '')
            reasoning.append(delta.get('reasoning_content') or '')
            if choice.get('finish_reason') is not None:
                finish = choice['finish_reason']

    for line in response:
        line = line.decode('utf-8').rstrip('\r\n')
        if not line:
            consume()
        elif line.startswith('data:'):
            event.append(line[5:].lstrip(' '))
        if done:
            break
    if event:
        consume()
    if not done or finish is None or not result.get('id'):
        raise ConnectionError('Completion stream ended before its final marker')
    result.update(object='chat.completion', choices=[{'index': 0, 'finish_reason': finish,
        'message': {'role': 'assistant', 'content': ''.join(content), 'reasoning_content': ''.join(reasoning)}}])
    return result
