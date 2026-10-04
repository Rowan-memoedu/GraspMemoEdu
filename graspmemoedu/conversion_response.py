"""Recover intact question objects from a partially malformed batch response."""
import json


def decode_questions(response):
    if isinstance(response, dict) and 'choices' in response:
        if response['choices'][0].get('finish_reason') != 'stop':
            raise ValueError('Incomplete provider response')
        response = response['choices'][0]['message']['content']
    if isinstance(response, dict):
        return response
    try:
        return json.loads(response)
    except (ValueError, TypeError):
        if not isinstance(response, str):
            raise ValueError('Expected a JSON response')
    # Decode only intact JSON objects; do not repair punctuation, invent missing
    # fields or alter option content. Braces inside quoted text are ignored.
    decoder, rows = json.JSONDecoder(), []
    quoted = escaped = False
    consumed_until = -1
    for index, char in enumerate(response):
        if index < consumed_until:
            continue
        if escaped:
            escaped = False
            continue
        if char == '\\' and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == '{' and not quoted:
            try:
                row, end = decoder.raw_decode(response, index)
            except ValueError:
                continue
            if isinstance(row, dict) and set(row) == {'id', 'distractors'} and isinstance(row['id'], str):
                rows.append(row)
                consumed_until = end
    return {'questions': rows}
