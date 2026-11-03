"""Real filesystem scope and lock membership; no package-manager execution."""
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import observe_npm_workspace


class WorkspaceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manifests = {
            'package.json': {'name': 'fixture', 'version': '1.0.0', 'workspaces': ['packages/*'],
                             'devDependencies': {'typescript': '5.0.0'}},
            'packages/a/package.json': {'name': '@fixture/a', 'version': '1.0.0',
                                        'dependencies': {'kleur': '^3.0.0'}},
            'packages/b/package.json': {'name': '@fixture/b', 'version': '1.0.0',
                                        'dependencies': {'kleur': '^4.0.0'},
                                        'peerDependencies': {'kleur': '^4.0.0'}}}
        self.lock = {'lockfileVersion': 3, 'packages': {
            '': copy.deepcopy(self.manifests['package.json']),
            'packages/a': copy.deepcopy(self.manifests['packages/a/package.json']),
            'packages/b': copy.deepcopy(self.manifests['packages/b/package.json']),
            'node_modules/@fixture/a': {'resolved': 'packages/a', 'link': True},
            'node_modules/@fixture/b': {'resolved': 'packages/b', 'link': True},
            'node_modules/kleur': {'version': '3.0.0'},
            'packages/b/node_modules/kleur': {'version': '4.0.0'}}}
        self.save()

    def save(self):
        for name, value in {**self.manifests, 'package-lock.json': self.lock}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')

    def fails(self, code):
        with self.assertRaisesRegex(GoalContractError, code):
            observe_npm_workspace(self.root)

    def test_owner_and_section_preserved_no_flattening_or_execution(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        result = observe_npm_workspace(self.root)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(result['owners']), 3)
        rows = [r for r in result['declarations'] if r['name'] == 'kleur']
        self.assertEqual([(r['owner'], r['section'], r['declaredSpec']) for r in rows], [
            ('packages/a', 'dependencies', '^3.0.0'),
            ('packages/b', 'dependencies', '^4.0.0'),
            ('packages/b', 'peerDependencies', '^4.0.0')])
        self.assertFalse(result['executionReady'])
        self.assertEqual(result['jointAssignment'], 'NOT_COMPUTED')
        self.assertEqual(result['physicalTree'], 'NOT_OBSERVED')

    def test_child_cannot_be_mistaken_for_lock_owner(self):
        with self.assertRaises(GoalContractError):
            observe_npm_workspace(self.root / 'packages/a')

    def test_stale_workspace_manifest_rejected(self):
        self.manifests['packages/a/package.json']['dependencies']['kleur'] = '99.0.0'
        self.save()
        self.fails('MANIFEST_LOCK_MISMATCH')

    def test_duplicate_workspace_names_rejected(self):
        self.manifests['packages/b/package.json']['name'] = '@fixture/a'
        self.lock['packages']['packages/b']['name'] = '@fixture/a'
        self.save()
        self.fails('DUPLICATE_NAME')

    def test_missing_lock_member_rejected(self):
        del self.lock['packages']['packages/a']
        self.save()
        self.fails('LOCK_MEMBER_MISSING')

    def test_unmatched_workspace_pattern_rejected(self):
        self.manifests['package.json']['workspaces'] = ['missing/*']
        self.save()
        self.fails('PATTERN_UNMATCHED')

    def test_lock_link_escape_rejected(self):
        self.lock['packages']['node_modules/@fixture/a']['resolved'] = '../outside'
        self.save()
        self.fails('LINK_TARGET_UNSUPPORTED')

    def test_link_renaming_or_missing_rejected(self):
        entry = self.lock['packages'].pop('node_modules/@fixture/a')
        self.lock['packages']['node_modules/alias'] = entry
        self.save()
        self.fails('LINK_OWNER_MISMATCH')
        del self.lock['packages']['node_modules/alias']
        self.save()
        self.fails('LINK_MISSING')

    def test_stale_member_record_not_covered_by_current_globs_rejected(self):
        self.lock['packages']['packages/missing'] = {'name': 'ghost'}
        self.save()
        self.fails('LOCK_MEMBERSHIP_MISMATCH')

    def test_discovery_cap_cannot_return_complete(self):
        with self.assertRaisesRegex(GoalContractError, 'DISCOVERY_LIMIT'):
            observe_npm_workspace(self.root, max_directories=1)
        with self.assertRaisesRegex(GoalContractError, 'MANIFEST_LIMIT'):
            observe_npm_workspace(self.root, max_manifests=1)

    def test_reread_detects_stale_lock_even_without_declared_edge_change(self):
        from deploom_mcp.workspace_inventory import _discover
        calls = 0
        def mutate(*args):
            nonlocal calls
            calls += 1
            result = _discover(*args)
            if calls == 2:
                value = copy.deepcopy(self.lock)
                value['packages']['node_modules/kleur']['version'] = '99.0.0'
                (self.root / 'package-lock.json').write_text(json.dumps(value))
            return result
        with patch('deploom_mcp.workspace_inventory._discover', side_effect=mutate):
            self.fails('INPUT_CHANGED')

    def test_membership_change_during_observation_rejected(self):
        from deploom_mcp.workspace_inventory import _discover
        calls = 0
        def mutate(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                added = self.root / 'packages/c'
                added.mkdir()
                (added / 'package.json').write_text('{"name":"c"}')
            return _discover(*args)
        with patch('deploom_mcp.workspace_inventory._discover', side_effect=mutate):
            self.fails('INPUT_CHANGED')

    def test_unsupported_globs_never_silently_omit_members(self):
        for pattern in ['../packages/*', 'packages/**', '!packages/b', 'packages/{a,b}', 'C:/packages/*', 'packages/../*']:
            with self.subTest(pattern=pattern):
                self.manifests['package.json']['workspaces'] = [pattern]
                self.save()
                self.fails('PATTERN_UNSUPPORTED')

    def test_ambiguous_lock_owners_rejected(self):
        (self.root / 'npm-shrinkwrap.json').write_text('{}')
        self.fails('LOCK_OWNER_UNSUPPORTED')

    def test_unsupported_manager_rejected(self):
        self.manifests['package.json']['packageManager'] = 'pnpm@10.0.0'
        self.save()
        self.fails('UNSUPPORTED_WORKSPACE_OR_MANAGER')

    def test_boolean_lock_version_rejected(self):
        self.lock['lockfileVersion'] = True
        self.save()
        self.fails('LOCK_V3_REQUIRED')

    def test_duplicate_json_key_rejected(self):
        (self.root / 'packages/a/package.json').write_text('{"name":"a","name":"b"}')
        self.fails('DUPLICATE_JSON_KEY')

    def test_reparse_directory_rejected_without_following_it(self):
        real_scandir = os.scandir
        class Entry:
            name = 'unsafe'
            def stat(self, **kwargs):
                class Info:
                    st_mode = 0o040755
                    st_file_attributes = 0x400
                return Info()
        class Scan:
            def __enter__(self): return iter([Entry()])
            def __exit__(self, *args): pass
        def guarded(folder):
            return Scan() if Path(folder) == self.root else real_scandir(folder)
        with patch('deploom_mcp.workspace_inventory.os.scandir', side_effect=guarded):
            self.fails('UNSAFE_INPUT')


    def test_proposal_holds_other_owners_and_same_name_other_sections(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        target = {'owner': 'packages/b', 'section': 'dependencies', 'name': 'kleur',
                  'fromSpec': '^4.0.0', 'toVersion': '4.1.0'}
        proposal = propose_workspace_targets(self.root, inventory['inventoryDigest'], [target])
        self.assertEqual(proposal['heldDeclarationCount'], 3)
        self.assertEqual(proposal['targets'], [target])
        self.assertFalse(proposal['executionReady'])
        self.assertEqual(proposal['globalImmutableInstances'], 'NOT_SPECIFIED_BY_THIS_PROPOSAL')
        self.assertEqual({r['owner']:r['count'] for r in proposal['heldDeclarationsByOwner']},
                         {'.':1, 'packages/a':1, 'packages/b':1})

    def test_flat_name_or_stale_before_spec_cannot_expand_scope(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        targets = [{'name':'kleur','toVersion':'4.1.0'},
                   {'owner':'.','section':'dependencies','name':'kleur','fromSpec':'^4.0.0','toVersion':'4.1.0'},
                   {'owner':'packages/b','section':'dependencies','name':'kleur','fromSpec':'3.0.0','toVersion':'4.1.0'}]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(GoalContractError):
                propose_workspace_targets(self.root, inventory['inventoryDigest'], [target])

    def test_proposal_reobserves_identity_and_rejects_old_dependency_inputs(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        self.manifests['packages/a/package.json']['description']='changed'
        self.save()
        with self.assertRaisesRegex(GoalContractError, 'STALE_WORKSPACE_INVENTORY'):
            propose_workspace_targets(self.root, inventory['inventoryDigest'], [{'owner':'packages/b','section':'dependencies',
                'name':'kleur','fromSpec':'^4.0.0','toVersion':'4.1.0'}])

    def test_duplicate_owner_target_rejected(self):
        from deploom_mcp.workspace_inventory import propose_workspace_targets
        inventory = observe_npm_workspace(self.root)
        target={'owner':'packages/b','section':'dependencies','name':'kleur','fromSpec':'^4.0.0','toVersion':'4.1.0'}
        with self.assertRaisesRegex(GoalContractError, 'WORKSPACE_TARGET_NOT_CURRENT_DECLARATION'):
            propose_workspace_targets(self.root, inventory['inventoryDigest'], [target,target])

    def test_legacy_goal_worker_keeps_workspace_rejection(self):
        from deploom_mcp.goal_worker import observe_goal_source
        with self.assertRaisesRegex(GoalContractError, 'UNSUPPORTED_WORKSPACE_OR_MANAGER'):
            observe_goal_source(self.root, 'a'*64)


if __name__ == '__main__':
    unittest.main()
