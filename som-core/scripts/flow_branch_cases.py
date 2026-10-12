"""Choose branch inputs from the public requirement, without reading blocks."""

from datetime import date, timedelta
from itertools import product


def nodes_in(steps):
    """Visit every public operation and both branch arms."""
    for node in steps:
        yield node
        for key in ('steps', 'then', 'else'):
            yield from nodes_in(node.get(key, []))


def branch_inputs(prompt):
    """Cover comparison boundaries and nulls with distinct HTTP inputs."""
    nodes = {node['name']: node for node in nodes_in(prompt['flow']['steps'])}
    inputs = {item['name']: item['type'] for item in prompt['flow']['inputs']}

    def target(reference, member=(), visited=frozenset()):
        if 'input' in reference:
            return {(reference['input'], member)}
        if 'step' not in reference:
            return set()
        name = reference['step']
        if name in visited:
            raise ValueError('cyclic requirement reference')
        fields = tuple(reference['field'].split('.')) if reference['field'] else ()
        fields += member
        if name in nodes:
            node = nodes[name]
            operation = node['op']
            if operation in {'parent.create', 'child.create'}:
                return target(node['args']['data'], fields, visited | {name})
            if operation == 'batch.create' and fields and fields[0] == 'parent':
                return target(node['args']['parent'], fields[1:], visited | {name})
            if operation in {'parent.get', 'child.get'}:
                identifier = node['args'][operation.split('.')[0] + '_id']
                if 'step' in identifier:
                    prefix = identifier['field'].split('.')[:-1]
                    return target(dict(identifier, field='.'.join(prefix)), fields, visited | {name})
        for branch in nodes.values():
            if name in branch.get('exports', {}):
                exports = branch['exports'][name]
                return set().union(*(target(exports[arm], fields, visited | {name})
                                     for arm in ('then', 'else')))
        return set()

    def value_kind(name, members):
        kind = inputs[name]
        if members:
            if len(members) != 1 or kind not in {'parent_input', 'child_input'}:
                raise ValueError('unresolved branch input member')
            role = kind.split('_')[0]
            fields = prompt['resources'][role]['fields']
            return next(item['type'] for item in fields if item['name'] == members[0])
        return kind

    domains = {}
    unresolved = []
    for node in nodes.values():
        if node['op'] != 'if':
            continue
        condition = node['condition']
        sources = target(condition['left'])
        if not sources:
            unresolved.append(node['name'])
            continue
        right = condition.get('right', {})
        if condition['op'] != 'is_null' and 'literal' not in right:
            unresolved.append(node['name'])
            continue
        for location in sorted(sources):
            kind = value_kind(*location)
            if condition['op'] == 'is_null':
                example = {'date': '2030-01-01', 'str': 'set', 'string': 'set',
                           'int': 1, 'integer': 1, 'float': 0.1,
                           'bool': False, 'boolean': False}[kind]
                values = [None, example]
            elif kind in {'bool', 'boolean'}:
                values = [False, True]
            elif kind in {'int', 'integer', 'float'}:
                boundary = right['literal']
                values = [boundary - 1, boundary, boundary + 1]
            elif kind == 'date':
                boundary = date.fromisoformat(right['literal'])
                values = [(boundary + timedelta(days=delta)).isoformat()
                          for delta in (-1, 0, 1)]
            elif kind in {'str', 'string'}:
                boundary = right['literal']
                values = [boundary, 'other' if boundary != 'other' else 'set']
            else:
                unresolved.append(node['name'])
                continue
            domain = domains.setdefault(location, [])
            for value in values:
                if not any(type(value) is type(saved) and value == saved for saved in domain):
                    domain.append(value)
    cases = [list(zip(domains, values, strict=True)) for values in product(*domains.values())]
    return {'cases': cases, 'unresolved': sorted(set(unresolved)),
            'branches': sorted(node['name'] for node in nodes.values() if node['op'] == 'if')}


def apply_case(inputs, changes):
    """Copy an input and apply only the selected public input fields."""
    from copy import deepcopy

    result = deepcopy(inputs)
    for (name, members), value in changes:
        if not members:
            result[name] = value
        else:
            result[name][members[0]] = value
    return result


def require_branch_coverage(matrix, records):
    """Reject a check run that never visits an applicable branch arm."""
    if matrix['unresolved']:
        raise AssertionError('unresolved branch probes: ' + ', '.join(matrix['unresolved']))
    observed = {name: set() for name in matrix['branches']}
    for record in records:
        for name, decision in record['branches'].items():
            if name not in observed or type(decision) is not bool:
                raise AssertionError('invalid branch observation')
            observed[name].add(decision)
    missing = {name: sorted({False, True} - arms) for name, arms in observed.items()
               if arms != {False, True}}
    if missing:
        raise AssertionError('unvisited branch arms: ' + str(missing))
    return {'passed': True, 'arms': {name: sorted(arms) for name, arms in observed.items()}}
