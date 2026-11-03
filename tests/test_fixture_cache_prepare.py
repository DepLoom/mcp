"""Synthetic fixture cache negatives; actual install/runtime remains at its owner."""
import base64,hashlib,io,json,shutil,subprocess,tarfile,tempfile,unittest
from pathlib import Path

class FixtureCacheIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cache=self.root/'cache';self.cache.mkdir()
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w:gz') as archive:
            body=json.dumps({'name':'fixture-runtime','version':'2.0.0'}).encode()
            info=tarfile.TarInfo('package/package.json');info.size=len(body);archive.addfile(info,io.BytesIO(body))
        self.archive=self.root/'fixture.tgz';self.archive.write_bytes(data.getvalue())
        self.integrity='sha512-'+base64.b64encode(hashlib.sha512(data.getvalue()).digest()).decode()

    def invoke(self,integrity,version):
        node=Path(shutil.which('node'));npm=node.parent/'node_modules/npm/bin/npm-cli.js'
        return subprocess.run([str(node),str(Path(__file__).with_name('npm_fixture_cache.cjs')),str(npm),str(self.archive),str(self.cache),integrity,'https://example.invalid/fixture-runtime-2.0.0.tgz','fixture-runtime',version],capture_output=True,timeout=20)

    def test_corrupt_sri_cannot_seed_a_verified_cache(self):
        wrong='sha512-'+base64.b64encode(bytes(64)).decode()
        result=self.invoke(wrong,'2.0.0')
        self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,b'')
        self.assertEqual(list(self.cache.iterdir()),[])

    def test_wrong_declared_version_is_not_accepted_from_valid_bytes(self):
        result=self.invoke(self.integrity,'9.9.9')
        self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,b'')