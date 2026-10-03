"""Reuse distractor templates only across sources with the same textual structure."""
import ast
from collections import defaultdict
from copy import deepcopy
import re


NUMBER = re.compile(r'\d+(?:\.\d+)?')
SLOT = re.compile(r'\[\[([^\[\]\n]+)\]\]')
FIELDS = ('question', 'correct_option', 'constraints')


def skeleton(text):
    return NUMBER.sub('#', re.sub(r'\s+', '', text))


def group_sources(sources):
    groups = defaultdict(list)
    for source in sources:
        key = (source['topic_id'], source['option_kind'], *(skeleton(source[f]) for f in FIELDS),
               tuple(skeleton(i['alt']) for i in source['images']))
        groups[key].append(source)
    result = []
    for members in groups.values():
        representative = deepcopy(members[0])
        bindings = [{} for _ in members]
        columns, templates = {}, {}
        for field in FIELDS:
            numbers = [NUMBER.findall(s[field]) for s in members]
            if len({len(row) for row in numbers}) != 1:
                raise ValueError('Mechanical source shape changed')
            replacements = []
            for position in range(len(numbers[0])):
                values = tuple(row[position] for row in numbers)
                if len(set(values)) == 1:
                    replacements.append(values[0])
                    continue
                if values not in columns:
                    name = 'p' + str(len(columns) + 1)
                    columns[values] = name
                    for row, value in zip(bindings, values):
                        row[name] = value
                replacements.append('[[' + columns[values] + ']]')
            replacement = iter(replacements)
            templates[field] = NUMBER.sub(lambda _: next(replacement), members[0][field])
        if representative['option_kind'] == 'scalar':
            for binding, member in zip(bindings, members):
                binding['answer'] = member['scalar']['value']
        representative['mechanical'] = {
            'size': len(members), 'templates': templates,
            'representative_parameters': bindings[0],
            'parameter_sets': bindings,
        }
        # Only one actual question and its images are sent to the provider.
        # These small member records are used solely for local expansion/checks.
        representative['_members'] = [
            {key: member[key] for key in ('id', 'option_kind', 'scalar', 'correct_option', 'choice_count', 'question')} | {'parameters': binding}
            for member, binding in zip(members, bindings)]
        result.append(representative)
    return result


def expression_value(expression, parameters):
    """Small arithmetic AST, with no Python eval, attributes or arbitrary calls."""
    from sympy import Rational, sqrt, simplify
    from sympy.parsing.latex import parse_latex
    tree = ast.parse(expression.strip(), mode='eval')
    if len(list(ast.walk(tree))) > 60:
        raise ValueError('Mechanical expression is too large')
    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and len(str(node.value)) <= 18:
            return Rational(str(node.value))
        if isinstance(node, ast.Name) and node.id in parameters:
            text = parameters[node.id]
            text = re.sub(r'\\sqrt([0-9A-Za-z])', r'\\sqrt{\1}', text)
            return parse_latex(text.replace(r'\dfrac', r'\frac').replace(r'\tfrac', r'\frac'), backend='lark') if node.id == 'answer' else Rational(text)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = evaluate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(node.op, ast.Div) and right != 0: return left / right
            if isinstance(node.op, ast.Pow) and right.is_Integer and abs(right) <= 8: return left ** right
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'sqrt'
                and len(node.args) == 1 and not node.keywords):
            value = evaluate(node.args[0])
            if value >= 0: return sqrt(value)
        raise ValueError('Unsupported mechanical expression')
    result = simplify(evaluate(tree.body))
    if result.free_symbols or result.is_finite is not True or result.is_real is not True:
        raise ValueError('Mechanical expression must give a finite real number')
    return result


def expand_candidate(candidate, parameters):
    from sympy import latex
    def render(text):
        if not isinstance(text, str):
            raise ValueError('Template must be a string')
        if re.search(r'\d\s*\[\[|\]\]\s*\d|\]\]\s*\[\[', text):
            raise ValueError('Put the entire arithmetic inside one [[expression]]; adjacent numeric slots concatenate')
        def replace(match):
            value = expression_value(match[1], parameters)
            math_context = not re.search(r'[\u3400-\u9fff]', text) or text[:match.start()].count('$') % 2
            return latex(value) if math_context else str(value)
        expanded = SLOT.sub(replace, text)
        if '[[' in expanded or ']]' in expanded:
            raise ValueError('Unresolved template slot')
        return expanded
    if not isinstance(candidate, dict) or set(candidate) != {'distractors'} or not isinstance(candidate['distractors'], list):
        raise ValueError('Invalid mechanical candidate')
    return {'distractors': [{key: render(value) for key, value in row.items()} for row in candidate['distractors']]}
