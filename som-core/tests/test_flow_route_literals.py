"""Check route fixture lowering and its closed input boundary."""

import ast
import json

import pytest

from som_core.flow_compact import compile_routes
from som_core.v1 import V1Error, load_blocks


def test_route_order_and_managed_parameters_survive_lowering():
    routes = [{'method': 'patch', 'path': '/api-v1/parents/{parent_id}/children/{child_id}'},
              {'method': 'get', 'path': '/api-v1/parents'}]
    source = compile_routes(json.dumps(routes))
    assert ast.literal_eval(source) == tuple((item['method'], item['path']) for item in routes)


@pytest.mark.parametrize('value', [
    None, [], {}, '', '[]', '{}', 'not JSON',
    '[{"method":"post","path":"/x","code":"print(1)"}]',
    '[{"method":"exec","path":"/x"}]',
    '[{"method":"get","path":17}]',
    '[{"method":"get","path":"/x/{arbitrary}"}]',
    '[{"method":"get","path":"/../x"}]',
    '[{"method":"get","path":"/x//y"}]',
    '[{"method":"get","path":"/x\\nprint(1)"}]',
    json.dumps([{'method': 'get', 'path': '/' + 'x' * 200}]),
    json.dumps([{'method': 'get', 'path': '/x'}] * 65),
    ' ' * 8193,
])
def test_invalid_routes_cannot_become_python_source(value):
    with pytest.raises(V1Error) as error:
        compile_routes(value)
    assert error.value.code == 'INVALID_VALUE'


@pytest.mark.parametrize('identity,accepted', [('compact.routes', True), ('legacy.routes', False)])
def test_new_parameter_kind_does_not_change_legacy_library_admission(tmp_path, identity, accepted):
    block = {'id': identity, 'version': 3, 'imports': [],
             'params': {'routes': 'routes_v7'}, 'template': 'ROUTES = {{routes}}'}
    (tmp_path / 'blocks.json').write_text(json.dumps([block]))
    if accepted:
        assert load_blocks(tmp_path) == {identity: block}
    else:
        with pytest.raises(V1Error) as error:
            load_blocks(tmp_path)
        assert error.value.code == 'INVALID_BLOCK'
