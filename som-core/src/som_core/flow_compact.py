"""Closed template lowering for the separate M2 compact candidate."""

from copy import deepcopy
from functools import cache
import hashlib
import json
from pathlib import Path

from . import composed, flow, flow_codec, quality
from .compact_names import rewrite_calls, source_names
from .v1 import V1Error, assemble as render

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "som-code-python/data/block-contracts/flow-v7-compact.json"
LITERAL_POLICY = ROOT / "som-rs/contracts/python-literal-encoding.v1.json"


@cache
def _literal_confusables() -> frozenset[int]:
    policy = json.loads(LITERAL_POLICY.read_text())
    if (policy["version"] != "som.python-literal-encoding.v1"
            or policy["ruff"] != "ruff 0.16.6" or policy["preview"] is not False):
        raise V1Error("OUTPUT_QUALITY_FAILED", "literal encoding contract differs")
    return frozenset(policy["codepoints"])


def _literal_fragment(text: str) -> str:
    """Keep the value; make confusable source characters explicit escapes."""
    return "".join(
        (f"\\u{ord(char):04x}" if ord(char) <= 0xFFFF
         else f"\\U{ord(char):08x}")
        if ord(char) in _literal_confusables() else char
        for char in json.dumps(text, ensure_ascii=False)[1:-1]
    )


def _python_literal(item: object) -> str:
    if isinstance(item, str) and any(ord(char) in _literal_confusables()
                                     for char in item):
        encoded = _literal_fragment(item)
        if len(encoded) <= 60:
            return '"' + encoded + '"'
        pieces, current = [], ""
        for char in item:
            fragment = _literal_fragment(char)
            if len(current) + len(fragment) > 40:
                pieces.append('"' + current + '"')
                current = ""
            current += fragment
        if current:
            pieces.append('"' + current + '"')
        return "(\n" + "\n".join(pieces) + "\n)"
    if isinstance(item, str) and len(item) > 60:
        return "(\n" + "\n".join(
            json.dumps(item[index:index + 40], ensure_ascii=False)
            for index in range(0, len(item), 40)
        ) + "\n)"
    return json.dumps(item, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def metadata():
    return json.loads(CATALOG.read_text())


def compile_routes(value):
    """Emit bounded route literals, without runtime JSON or source input."""
    import re
    if not isinstance(value, str) or len(value.encode()) > 8192:
        raise V1Error("INVALID_VALUE", "route fixtures require bounded JSON")
    try:
        routes = json.loads(value)
    except (ValueError, RecursionError) as error:
        raise V1Error("INVALID_VALUE", "route fixtures require JSON") from error
    if not isinstance(routes, list) or not 1 <= len(routes) <= 64:
        raise V1Error("INVALID_VALUE", "route fixture count is invalid")
    lines = []
    for route in routes:
        if not isinstance(route, dict) or set(route) != {"method", "path"}:
            raise V1Error("INVALID_VALUE", "invalid route fixture")
        method, path = route["method"], route["path"]
        if (not isinstance(method, str) or method not in {"get", "post", "patch", "delete"}
                or not isinstance(path, str) or len(path) > 200
                or re.fullmatch(r"/(?:[A-Za-z0-9_-]+|\{(?:parent_id|child_id)\})(?:/(?:[A-Za-z0-9_-]+|\{(?:parent_id|child_id)\}))*", path) is None):
            raise V1Error("INVALID_VALUE", "invalid HTTP route fixture")
        lines.append("    (" + json.dumps(method) + ", " + json.dumps(path) + "),")
    return "(\n" + "\n".join(lines) + "\n)"


def is_compact(dsl):
    return any(str(block.get("id", "")).startswith("compact.")
               for file in dsl.get("files", []) for block in file.get("blocks", []))


def reference(prompt, blocks, variant="standard"):
    """Reference only; production never calls this planner."""
    original = flow.reference(prompt, blocks, variant)
    catalog = metadata()
    for file in original["files"]:
        file["path"] = next(
            (catalog["slots"][slot] for slot, path in json.loads(flow.CATALOG.read_text())["slots"].items() if file["path"] == path),
            file["path"],
        )
        for block in file["blocks"]:
            if not block["id"].startswith("flow."):
                block["id"] = "compact." + block["id"]
            block["params"] = deepcopy(catalog["blocks"][block["id"]]["bindings"])
    flow.validate_plan(prompt, original, catalog)
    return original


def resolve(prompt, dsl):
    """Bind only validated values. Keep every selected block and reference."""
    import re
    flow.validate_plan(prompt, dsl, metadata())
    values = composed.values(flow.base(prompt))
    values.update({"flow.inputs": prompt["flow"]["inputs"],
                   "flow.name": prompt["flow"]["name"],
                   "flow.hash": hashlib.sha256(composed.canonical({"prompt": prompt, "dsl": dsl, "catalog_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest()}).encode()).hexdigest(),
                   "flow.program": {"prompt": prompt, "plan": dsl["flow"]},
                   "flow.custom_fields": json.dumps({item["name"]: item["type"] for item in prompt["resources"]["child"]["fields"]}, ensure_ascii=False, separators=(",", ":")),
                   "flow.versioned": any(identity.endswith(".object.put") for identity in flow.selected_ids(dsl["flow"]))})
    result = flow.file_symbols(dsl)
    for file in result["files"]:
        file["path"] = re.sub(r"\{([A-Za-z_.]+)\}", lambda match: str(values[match[1]]), file["path"])
        for block in file["blocks"]:
            for key, ref in block["params"].items():
                source = ref.get("$ref")
                if source == "block.id": block["params"][key] = block["id"]
                elif source in values: block["params"][key] = deepcopy(values[source])
                else: raise V1Error("INVALID_REF", "unknown compact binding")
    return result


def assemble(prompt, dsl, blocks, binary):
    """Enforce the combined budget on the final formatted product."""
    composed.validate_library(blocks, metadata())
    flow_codec.features(prompt, metadata())
    flow_codec.encode(prompt, dsl, metadata())
    lowered = resolve(prompt, dsl)
    files = {}
    for file in lowered['files']:
        files.update(render({'version': 'som.dsl.v2', 'files': [file]}, blocks,
                            byte_limit=None))
    return quality.finalize(files, binary, byte_limit=None)


def compile_program(value):
    """Expand a checked tree with fixed Python statements, never source input."""
    if not isinstance(value, dict) or set(value) != {"prompt", "plan"}:
        raise V1Error("INVALID_REF", "compact program requires a typed prompt and plan")
    prompt, tree = value["prompt"], value["plan"]
    flow.validate_tree(prompt, tree, flow.catalog())
    lines = ['async def forward(ctx: FlowContext) -> JsonObject:',
             '    """Run the selected forward blocks once."""']
    variables = {}
    kinds = {}
    inputs = {item['name']: item['type'] for item in prompt['flow']['inputs']}
    input_positions = {item['name']: index for index, item in enumerate(prompt['flow']['inputs'])}
    sequence = 0

    types = {'string': 'str', 'bytes': 'str', 'integer': 'int', 'float': 'float',
             'boolean': 'bool', 'date': 'date', 'json': 'JsonValue',
             'identity': 'Identity', 'object': 'FlowObject',
             'workflow': 'WorkflowReceipt', 'batch': 'BatchRead'}
    for role in ('parent', 'child'):
        stem = role.capitalize()
        types[role+'_row'] = stem+'Row'
        types[role+'_rows'] = 'list['+stem+'Row]'
        types[role+'_input'] = stem+'InputData'
        types[role+'_patch'] = stem+'PatchData'
    types['children_input'] = 'list[ChildInputData]'

    def annotation(kind):
        return types[kind.rstrip('?')] + (' | None' if kind.endswith('?') else '')

    quoted = _python_literal

    def expression(ref):
        if "input" in ref: return 'ctx.inputs.input_'+str(input_positions[ref['input']])
        if "literal" in ref:
            literal = ref["literal"]
            if ref['type'] == 'date': return 'date.fromisoformat('+quoted(literal)+')'
            if type(literal) is bool: return "True" if literal else "False"
            if isinstance(literal, str): return quoted(literal)
            return repr(literal)
        result = variables[ref["step"]]
        kind = kinds[ref['step']]
        for field in ref["field"].split(".") if ref["field"] else []:
            if kind == 'identity': result += '['+quoted(field)+']'
            elif kind in {'parent_row', 'child_row'} and field in {'created_at', 'updated_at'}:
                result = 'text_value(model_json('+result+')['+quoted(field)+'])'
            else: result += '.'+field
            kind = flow._member(prompt, kind, field)
        return result

    def data_expression(ref):
        value = expression(ref)
        kind = flow.ref_type(prompt, ref, inputs, kinds)
        if kind in {'parent_input', 'child_input', 'parent_patch', 'child_patch'}:
            return 'model_json('+value+', True)'
        return 'json_object('+value+')'

    def children_expression(ref):
        value = expression(ref)
        if flow.ref_type(prompt, ref, inputs, kinds) == 'children_input':
            return value
        return 'TypeAdapter(list[ChildInputData]).validate_python('+value+')'

    def parent_expression(ref):
        value = expression(ref)
        if flow.ref_type(prompt, ref, inputs, kinds) == 'parent_input':
            return value
        return 'ParentInputData.model_validate('+value+')'

    def emit(text, depth): lines.append("    " * depth + text)

    def walk(nodes, depth=1, group=""):
        nonlocal sequence
        for node in nodes:
            identity, name = node["id"], node["name"]
            operation = flow.catalog()[identity]["operation"]
            if operation == "transaction":
                emit("async with ctx.transaction():", depth)
                walk(node["steps"], depth+1, name)
                continue
            if operation == "if":
                condition = node["condition"]
                left = expression(condition["left"])
                if condition['op'] == 'is_null': predicate = left + ' is None'
                elif condition['op'] in {'eq', 'ne'}:
                    singleton = type(condition['right'].get('literal')) is bool
                    operator = (' is ' if condition['op'] == 'eq' else ' is not ') if singleton else (' == ' if condition['op'] == 'eq' else ' != ')
                    predicate = left + operator + expression(condition['right'])
                else:
                    operator = {'lt': ' < ', 'le': ' <= ', 'gt': ' > ', 'ge': ' >= '}[condition['op']]
                    predicate = left + operator + expression(condition['right'])
                branch = "_branch" + str(sequence); sequence += 1
                emit(branch + " = " + predicate, depth)
                emit("ctx.record.branches["+quoted(name)+"] = "+branch, depth)
                if not group: emit("await ctx.persist()", depth)
                prior = dict(variables)
                prior_kinds = dict(kinds)
                export_kinds = {}
                exports = {key: "_value"+str(sequence+index) for index, key in enumerate(sorted(node["exports"]))}
                sequence += len(exports)
                for arm in ("then", "else"):
                    variables.clear(); variables.update(prior)
                    kinds.clear(); kinds.update(prior_kinds)
                    if arm == 'else' and condition['op'] == 'is_null':
                        key = '@'+composed.canonical(condition['left'])
                        kinds[key] = flow.ref_type(prompt, condition['left'], inputs, kinds)[:-1]
                    emit("if " + (predicate if condition['op'] == 'is_null' else branch) + ":" if arm == "then" else "else:", depth)
                    begin = len(lines)
                    walk(node[arm], depth+1, group)
                    for key, variable in exports.items():
                        emit(variable + " = " + expression(node["exports"][key][arm]), depth+1)
                        export_kinds[key] = flow.ref_type(prompt, node['exports'][key][arm], inputs, kinds)
                    if len(lines) == begin: emit("pass", depth+1)
                variables.clear(); variables.update(prior); variables.update(exports)
                kinds.clear(); kinds.update(prior_kinds); kinds.update(export_kinds)
                for key, variable in exports.items():
                    emit('ctx.record.values['+quoted(key)+'] = result_json('+variable+')', depth)
                continue
            variable = "_value"+str(sequence); sequence += 1
            arguments = node["args"]
            effect_args = ['step='+quoted(name)]
            if group:
                effect_args.append('group='+quoted(group))
            if node['undo'] is not None:
                effect_args.append('undo='+quoted(node['undo'].rsplit('.', 1)[-1]))
            effect = 'Effect('+', '.join(effect_args)+')' if node['undo'] is not None else 'None'
            if operation == "authorize": call = '{"sub": ctx.record.owner, "role": "editor"}'
            elif operation == "workflow.run": call = "await ctx.export("+expression(arguments["parent_id"])+")"
            elif operation == "workflow.import": call = "await ctx.batch("+effect+", "+expression(arguments["parent_id"])+", csv_records("+expression(arguments["csv"])+"))"
            elif operation == "batch.create": call = "await ctx.batch_create("+effect+", "+parent_expression(arguments["parent"])+", "+children_expression(arguments["children"])+")"
            elif operation == "object.get": call = "await run_in_threadpool(flow_object_get, "+expression(arguments["key"])+")"
            elif operation == "object.put": call = "await ctx.put("+effect+", "+expression(arguments["key"])+", "+expression(arguments["body"])+")"
            else:
                role, method = operation.split(".")
                args = ["role="+quoted(role), "operation="+quoted(method)]
                for key in ("parent_id", "child_id"):
                    if key in arguments:
                        destination = "row_id" if key == role+"_id" and method in {"get", "patch"} else "parent_id"
                        args.append(destination+'='+expression(arguments[key]))
                if "data" in arguments: args.append('data='+data_expression(arguments['data']))
                call = "await ctx.resource(CrudRequest("+", ".join(args)+"), "+effect+")"
            kind = flow.output_type(prompt, operation)
            if operation == 'authorize':
                emit(variable+': Identity = '+call, depth)
                emit('ctx.record.values['+quoted(name)+'] = json_value('+variable+')', depth)
                emit('await ctx.persist()', depth)
            elif operation == 'object.put':
                # This typed helper validates the receipt and checkpoints it
                # with the provider ownership data before returning.
                emit(variable+' = '+call, depth)
            else:
                # Resource dispatch returns a union. Narrow it at this
                # boundary. The other helpers return validated concrete models.
                checker = ('TypeAdapter('+annotation(kind)+').validate_python'
                           if kind.endswith('_rows') else annotation(kind)+'.model_validate')
                result = checker+'('+call+')' if operation.startswith(('parent.', 'child.')) else call
                emit(variable+' = await ctx.save_result('+quoted(name)+', '+result+(', False' if group else '')+')', depth)
            if identity.endswith(".checked"): emit('integer('+variable+'.id)', depth)
            variables[name] = variable
            kinds[name] = kind
    walk(tree["steps"])
    result = ", ".join(quoted(key)+": "+expression(ref) for key, ref in sorted(tree["result"].items()))
    emit('output = FlowResult.model_validate({'+result+'})', 1)
    emit('return model_json(output)', 1)
    source = (ROOT/'som-core/block_templates/flow_compact/forward_support.py.tpl').read_text()
    methods = {}
    import re
    matches = list(re.finditer(r'^    async def (\w+)\(', source, re.M))
    for index, match in enumerate(matches):
        body = source[match.start():matches[index+1].start() if index+1 < len(matches) else len(source)]
        body = body.removeprefix('    ').replace('\n    ', '\n')
        name = match[1]
        body = body.replace('async def '+name+'(self,', 'async def flow_'+name+'(ctx: FlowContext,').replace('self.', 'ctx.')
        methods[name] = body.rstrip()
    selected = {flow.catalog()[identity]['operation'] for identity in flow.selected_ids(tree)}
    used = set()
    if 'workflow.run' in selected: used.add('export')
    if selected & {'workflow.import', 'batch.create'}: used.add('batch')
    if 'batch.create' in selected: used.add('batch_create')
    if 'object.put' in selected: used.add('put')
    result_fields = []
    for index, (name, kind) in enumerate(sorted(flow.result_types(prompt).items())):
        result_fields.append('    result_'+str(index)+': '+annotation(kind)+' = Field(alias='+quoted(name)+')')
    supports = []
    supports.append('class FlowResult(BaseModel):\n    """Validate the declared results at their JSON boundary."""\n    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)\n'+'\n'.join(result_fields))
    replacements = {'ctx.'+name+'(': 'flow_'+name+'(ctx, ' for name in used}
    for name in sorted(used):
        body = methods[name]
        supports.append(rewrite_calls(body, replacements))
    result = rewrite_calls('\n'.join(lines), replacements)
    return source_names('\n\n\n'.join([*supports, result])+'\n')


def compile_engine(value):
    """Keep the fixed engine; omit SDK work forbidden by its capability."""
    program = compile_program(value)
    template = (ROOT/'som-core/block_templates/flow_compact/engine.py.tpl').read_text()
    if value['prompt']['capabilities']['storage'] == 'none':
        start = template.index('    async def external_inverse(')
        end = template.index('    async def reverse(', start)
        template = template[:start] + '''    async def external_inverse(self, _effect: Effect) -> None:
        """Preserve an invalid external journal without guessing an inverse."""
        raise failure(503, "invalid external journal")


''' + template[end:]
    return source_names(template.replace('{{flow_program}}', program))
