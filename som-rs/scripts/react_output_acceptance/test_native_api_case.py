"""GitHub-only finite data/VM controls. These grant no native or product PASS."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock

if not (os.environ.get('GITHUB_ACTIONS') == 'true' and sys.platform == 'linux'
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    raise RuntimeError('GitHub Linux isolated interpreter required')

HERE = Path(__file__).absolute().parent
SOURCE_SHA256 = 'e131eed2dacfac3dcd1b7beb18df74ca30f92119bd1bd4f45ce47cf120538920'
raw = (HERE / 'native_record_api.py').read_bytes()
if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
    raise AssertionError('exact consumer source differs')
spec = importlib.util.spec_from_file_location('_som_native_case_data_controls', HERE / 'native_record_api.py')
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


class NativeAPICaseControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='som-case-control-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def entry(self, index=0):
        prompt = {'version': 'control-only', 'entities': []}
        return {'id': 'case-' + str(index), 'product': '/control/case-' + str(index),
                'prompt': prompt, 'python_request': json.dumps({'prompt': prompt})}

    def manifest(self):
        return {'products': [self.entry(i) for i in range(129)]}

    def product(self):
        entry = self.entry()
        (self.root / 'product/pkg').mkdir(parents=True)
        (self.root / 'product/pkg/app.py').write_bytes(b'answer = 42\n')
        (self.root / 'compile.response.json').write_text(json.dumps({'bundle': {'prompt': entry['prompt']}}))
        (self.root / 'assemble.response.json').write_text(json.dumps({'files': {'pkg/app.py': 'answer = 42\n'}}))
        (self.root / 'prompt.json').write_text(json.dumps(entry['prompt'], ensure_ascii=False, indent=2) + '\n')
        return entry

    def test_duplicate_nonfinite_and_foreign_json_are_refused(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b''):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                consumer._strict_json(raw)
        class Foreign(dict):
            pass
        with self.assertRaises(ValueError):
            consumer._plain(Foreign())
        self.assertEqual(consumer._strict_json(b'{"a":[1,true,null]}'), {'a': [1, True, None]})

    def test_all129_unique_cases_are_required_without_sampling(self):
        manifest = self.manifest()
        self.assertEqual(len(consumer._manifest_rows(json.dumps(manifest).encode())), 129)
        for mutation in ('short', 'duplicate', 'traversal', 'missing-request'):
            altered = self.manifest()
            if mutation == 'short':
                altered['products'].pop()
            elif mutation == 'duplicate':
                altered['products'][1]['id'] = altered['products'][0]['id']
            elif mutation == 'traversal':
                altered['products'][0]['id'] = '../other'
            else:
                del altered['products'][0]['python_request']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                consumer._manifest_rows(json.dumps(altered).encode())

    def test_python_response_prompt_and_complete_product_bytes_join(self):
        entry = self.product()
        self.assertEqual(consumer._python_bytes(entry, self.root), {'pkg/app.py': b'answer = 42\n'})
        (self.root / 'product/pkg/app.py').write_bytes(b'answer = 43\n')
        with self.assertRaisesRegex(ValueError, 'product bytes differ'):
            consumer._python_bytes(entry, self.root)
        (self.root / 'product/pkg/app.py').write_bytes(b'answer = 42\n')
        (self.root / 'product/extra.py').write_bytes(b'not in response')
        with self.assertRaisesRegex(ValueError, 'inventory differs'):
            consumer._python_bytes(entry, self.root)

    def test_target_wrong_prompt_traversal_and_symlink_are_refused(self):
        entry = self.product()
        request = json.loads(entry['python_request'])
        request['target'] = 'python'
        entry['python_request'] = json.dumps(request)
        with self.assertRaisesRegex(ValueError, 'request/prompt'):
            consumer._python_bytes(entry, self.root)
        entry['python_request'] = json.dumps({'prompt': {'wrong': True}})
        with self.assertRaisesRegex(ValueError, 'request/prompt'):
            consumer._python_bytes(entry, self.root)
        entry = self.entry()
        (self.root / 'assemble.response.json').write_text(json.dumps({'files': {'../outside': 'bad'}}))
        with self.assertRaisesRegex(ValueError, 'safe exact'):
            consumer._python_bytes(entry, self.root)
        (self.root / 'product/link.py').symlink_to(self.root / 'product/pkg/app.py')
        (self.root / 'assemble.response.json').write_text(json.dumps({'files': {'link.py': 'answer = 42\n'}}))
        with self.assertRaisesRegex(ValueError, 'symlink'):
            consumer._python_bytes(entry, self.root)

    def test_invocation_seals_reject_changed_defaults_code_and_closure(self):
        def original(value=object()):
            return value
        row = consumer._invocation(original)
        self.assertIs(consumer._signature(row), original)
        defaults = original.__defaults__
        original.__defaults__ = (object(),)
        with self.assertRaises(ValueError):
            consumer._signature(row)
        original.__defaults__ = defaults
        def foreign(value=None):
            return value
        code = original.__code__
        original.__code__ = foreign.__code__
        with self.assertRaises(ValueError):
            consumer._signature(row)
        original.__code__ = code
        self.assertIs(consumer._signature(row), original)
        def factory():
            held = object()
            def closed():
                return held
            return closed
        closed = factory()
        closed_row = consumer._invocation(closed)
        closed.__closure__[0].cell_contents = object()
        with self.assertRaises(ValueError):
            consumer._signature(closed_row)

    def test_actual_noargument_call_offset_differs_from_supplied_argument(self):
        def probe(*args):
            consumer._callsite(sys._getframe(1), '_native_consume')
            return 'actual-control-call'
        def accepted():
            _native_consume = probe
            return _native_consume()
        def argument():
            _native_consume = probe
            return _native_consume('supplied-completion-token')
        self.assertEqual(accepted(), 'actual-control-call')
        with self.assertRaisesRegex(ValueError, 'no-argument'):
            argument()
        with self.assertRaises(ValueError):
            consumer._callsite(sys._getframe(), '_native_consume')

    def test_equal_filename_code_copy_does_not_satisfy_identity(self):
        def original():
            return 'literal'
        row = consumer._invocation(original)
        original.__code__ = original.__code__.replace()
        self.assertEqual(original.__code__.co_filename, row[1].co_filename)
        self.assertIsNot(original.__code__, row[1])
        with self.assertRaises(ValueError):
            consumer._signature(row)

    def test_deep_and_oversized_json_are_incomplete(self):
        data = 0
        for _ in range(66):
            data = [data]
        with self.assertRaisesRegex(ValueError, 'bounded plain'):
            consumer._strict_json(json.dumps(data).encode())
        with self.assertRaisesRegex(ValueError, 'bounded JSON'):
            consumer._strict_json(b' ' * (consumer.MAX_JSON + 1))

    def test_exact_output_data_refuses_sibling_case_other_C_and_arbitrary_descendant(self):
        correct = self.root / 'products/case-A/api-raw'
        correct.mkdir(parents=True)
        consumer._exact_case_output(self.root, 'case-A', correct)
        for candidate in (self.root / 'products/case-B/api-raw',
                          self.root / 'other-C/products/case-A/api-raw',
                          self.root / 'work/api-raw', self.root / 'products/case-A/other'):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, 'only this actual case'):
                consumer._exact_case_output(self.root, 'case-A', candidate)
        with self.assertRaises(ValueError):
            consumer._exact_case_output(self.root, '../case-A', correct)

    def test_native_case_log_keeps_python_os_bytes_and_original_error_then_restores(self):
        # This seam supplies only imports. It grants no native Context or entry.
        common = ModuleType('common')
        common.CONTRACT, common.REPO = self.root, self.root
        product_spec = importlib.util.spec_from_file_location('_som_case_log_control', HERE / 'check_products.py')
        products = importlib.util.module_from_spec(product_spec)
        with mock.patch.dict(sys.modules, {'common': common, 'check_contract': ModuleType('check_contract')}), \
             mock.patch.object(sys, 'path', list(sys.path)):
            product_spec.loader.exec_module(products)
        path = self.root / 'merged.log'
        original = RuntimeError('actual-control-primary')
        original_trace = None
        before = tuple(os.fstat(fd) for fd in (1, 2))
        try:
            with products._native_log(path):
                print('PYTHON_CONTROL')
                sys.stdout.flush()
                os.write(1, b'OS_STDOUT_CONTROL\n')
                os.write(2, b'OS_STDERR_CONTROL\n')
                try:
                    raise original
                except RuntimeError:
                    original_trace = original.__traceback__
                    raise
        except RuntimeError as observed:
            self.assertIs(observed, original)
            trace = observed.__traceback__
            self.assertTrue(any(node is original_trace for node in self.trace_nodes(trace)))
            self.assertTrue(any(node.tb_frame.f_code is self.test_native_case_log_keeps_python_os_bytes_and_original_error_then_restores.__func__.__code__
                                for node in self.trace_nodes(trace)))
        else:
            self.fail('the original control error was lost')
        after = tuple(os.fstat(fd) for fd in (1, 2))
        self.assertEqual([(v.st_dev, v.st_ino) for v in before], [(v.st_dev, v.st_ino) for v in after])
        self.assertEqual(path.read_bytes(), b'PYTHON_CONTROL\nOS_STDOUT_CONTROL\nOS_STDERR_CONTROL\n')
        with self.assertRaises(FileExistsError):
            with products._native_log(path):
                self.fail('an existing log was reused')

    def test_deadline_restores_handler_and_timer_after_original_body_error(self):
        common = ModuleType('common')
        common.CONTRACT, common.REPO = self.root, self.root
        product_spec = importlib.util.spec_from_file_location('_som_case_deadline_control', HERE / 'check_products.py')
        products = importlib.util.module_from_spec(product_spec)
        with mock.patch.dict(sys.modules, {'common': common, 'check_contract': ModuleType('check_contract')}), \
             mock.patch.object(sys, 'path', list(sys.path)):
            product_spec.loader.exec_module(products)
        original, old_handler, events = RuntimeError('deadline-control-primary'), object(), []
        def timer(kind, seconds):
            events.append(('timer', kind, seconds))
        def handler(kind, value):
            events.append(('handler', kind, value))
        with mock.patch.object(products.signal, 'getitimer', return_value=(0.0, 0.0)), \
             mock.patch.object(products.signal, 'getsignal', return_value=old_handler), \
             mock.patch.object(products.signal, 'setitimer', side_effect=timer), \
             mock.patch.object(products.signal, 'signal', side_effect=handler):
            try:
                with products._native_deadline():
                    raise original
            except RuntimeError as observed:
                self.assertIs(observed, original)
            else:
                self.fail('the original deadline control error was lost')
        self.assertEqual(events, [('handler', products.signal.SIGALRM, products._native_timeout),
                                  ('timer', products.signal.ITIMER_REAL, 3600.0),
                                  ('timer', products.signal.ITIMER_REAL, 0.0),
                                  ('handler', products.signal.SIGALRM, old_handler)])
        with mock.patch.object(products.signal, 'getitimer', return_value=(1.0, 0.0)):
            with self.assertRaisesRegex(ValueError, 'cannot replace'):
                with products._native_deadline():
                    self.fail('a pre-existing timer was replaced')

    def trace_nodes(self, trace):
        while trace is not None:
            yield trace
            trace = trace.tb_next


if __name__ == '__main__':
    unittest.main(verbosity=2)
