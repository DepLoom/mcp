"""Real pinned host TypeScript syntax boundary, never project-source execution."""
import hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from deploom_mcp.migration_usage import inspect_usage
HOST_PARSER=Path(__file__).resolve().parents[3]/'desktop/node_modules/typescript/lib/typescript.js'

class MissingParserTests(unittest.TestCase):
    def test_missing_host_parser_is_unknown_not_unused(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'DEPLOOM_MCP_TYPESCRIPT_PATH':'','DEPLOOM_MCP_TYPESCRIPT_SHA256':''}):
            result=inspect_usage(Path(temp),['vite'],Path(temp)/'state',deadline=time.monotonic()+5,source_identity='a'*64)
        self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['unusedControls'],[])

@unittest.skipUnless(HOST_PARSER.is_file(),'requires explicitly configured existing host TypeScript6.0.3; unavailable means production UNKNOWN')
class ActualTypeScriptBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.project=self.root/'project';self.project.mkdir();self.state=self.root/'state'
        self.sha=hashlib.sha256(HOST_PARSER.read_bytes()).hexdigest();self.env=patch.dict(os.environ,{'DEPLOOM_MCP_TYPESCRIPT_PATH':str(HOST_PARSER),'DEPLOOM_MCP_TYPESCRIPT_SHA256':self.sha});self.env.start();self.addCleanup(self.env.stop)
    def inspect(self):return inspect_usage(self.project,['strip-json-comments','vite'],self.state,deadline=time.monotonic()+15,source_identity='a'*64)
    def write(self,name,text):p=self.project/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
    def test_literal_import_require_and_config_observations(self):
        self.write('entry.ts',"import strip from 'strip-json-comments'; const old = require('strip-json-comments'); import {defineConfig as cfg} from 'vite'; export default cfg({css:{preprocessorOptions:{scss:{api:'legacy'}}}});")
        result=self.inspect();forms={r['form'] for r in result['observations']}
        self.assertEqual(result['status'],'OBSERVED_SYNTAX_ONLY');self.assertTrue({'DEFAULT_IMPORT','REQUIRE_LITERAL','VITE_SASS_API_LITERAL'}<=forms)
        self.assertEqual(result['semanticApplicability'],'UNKNOWN');self.assertEqual(result['sourceIdentity'],'a'*64)
        self.assertEqual(result['parser']['sha256'],self.sha);self.assertFalse(list(self.state.glob('migration-usage-input-*')))
    def test_project_source_is_parsed_not_executed(self):
        sentinel=self.root/'MUST_NOT_EXIST'
        self.write('danger.js',"require('node:fs').writeFileSync("+json.dumps(str(sentinel))+",'executed'); throw Error('must not execute'); const x = require('strip-json-comments');")
        result=self.inspect();self.assertFalse(sentinel.exists());self.assertTrue(result['observations'])
    def test_dynamic_import_and_require_are_unknown(self):
        self.write('dynamic.js',"const name = 'strip-json-comments'; const x=require(name); const y=import(name);")
        result=self.inspect();self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['localCoverage']['dynamicLoads'],2)
    def test_require_alias_not_invented_unused(self):
        self.write('alias.js',"const load = require; const x=load('strip-json-comments');")
        result=self.inspect();self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['localCoverage']['aliasLoads'],1);self.assertEqual(result['unusedControls'],[])
    def test_nested_spread_config_is_unknown(self):
        self.write('vite.config.js',"import {defineConfig} from 'vite'; export default defineConfig({css:{preprocessorOptions:{scss:{...options,api:'legacy'}}}})")
        result=self.inspect();self.assertEqual(result['status'],'UNKNOWN');self.assertGreater(result['localCoverage']['dynamicConfigs'],0)
    def test_computed_or_callback_config_is_unknown(self):
        self.write('vite.config.js',"import {defineConfig} from 'vite'; export default defineConfig(()=>({css:{}}))")
        result=self.inspect();self.assertEqual(result['status'],'UNKNOWN')
    def test_denied_directory_keeps_coverage_unknown(self):
        def denied_walk(*args,**kwargs):
            kwargs['onerror'](PermissionError('engineering-denied-directory'))
            return iter([])
        with patch('deploom_mcp.migration_usage.os.walk',side_effect=denied_walk):result=self.inspect()
        self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['localCoverage']['unreadable'],1)
        self.assertEqual(result['unusedControls'],[])
    def test_no_match_is_only_scoped_absence(self):
        self.write('other.js','export const value=42;')
        result=self.inspect();self.assertEqual(result['status'],'NOT_OBSERVED_SCOPED');self.assertIn('NOT_RUNTIME_OR_WHOLE_PROJECT',result['absenceClaimScope']);self.assertEqual(result['unusedControls'],[])
    def test_unsupported_file_and_parse_errors_remain_unknown(self):
        self.write('component.vue','<script>import x from "vite"</script>');self.write('broken.ts','import { from "vite"')
        result=self.inspect();self.assertEqual(result['status'],'UNKNOWN');self.assertGreater(result['localCoverage']['unsupported'],0);self.assertGreater(result['localCoverage']['parseErrors'],0)
    def test_file_count_limit_keeps_denominator(self):
        for n in range(257):self.write(str(n)+'.js','export const x=1;')
        result=self.inspect();self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(result['localCoverage']['eligible'],257);self.assertEqual(result['localCoverage']['scanned'],256)
    def test_changed_parser_hash_rejected_before_loading(self):
        with patch.dict(os.environ,{'DEPLOOM_MCP_TYPESCRIPT_SHA256':'b'*64}):result=self.inspect()
        self.assertEqual(result['reason'],'HOST_PARSER_IDENTITY_REJECTED');self.assertFalse(self.state.exists())
    def test_pinned_helper_cannot_promote_changed_source(self):
        self.write('entry.js',"const x=require('strip-json-comments');")
        from deploom_mcp import migration_usage
        actual=migration_usage.run_process
        def mutate(*args,**kwargs):
            result=actual(*args,**kwargs);self.write('entry.js','changed');return result
        with patch.object(migration_usage,'run_process',side_effect=mutate):result=self.inspect()
        self.assertEqual(result['status'],'STALE');self.assertEqual(result['reason'],'USAGE_SOURCE_CHANGED')
