"""Public benchmark registry configuration never embeds an operator hostname."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from correctness_oracle import chalk_registry_from_environment,OracleFailure

class CustomRegistryTests(unittest.TestCase):
    def test_default_is_original_public_registry(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(chalk_registry_from_environment(),'https://registry.npmjs.org/')
    def test_custom_profile_requires_explicit_registry(self):
        with patch.dict(os.environ,{'D1_REGISTRY_PROFILE':'nexus'},clear=True):
            with self.assertRaisesRegex(OracleFailure,'EXPLICIT_CUSTOM_REGISTRY_REQUIRED'):
                chalk_registry_from_environment()
    def test_explicit_registry_is_normalized_without_source_hash_override(self):
        with patch.dict(os.environ,{'D1_NPM_REGISTRY':'https://registry.example.invalid/npm'},clear=True):
            self.assertEqual(chalk_registry_from_environment(),'https://registry.example.invalid/npm/')
    def test_credentials_query_fragment_and_control_are_rejected(self):
        for value in ['https://' + 'user:password' + '@example.invalid/','https://example.invalid/?token=secret','https://example.invalid/#fragment','https://example.invalid/\n','file:///tmp/packages']:
            with self.subTest(value=value),patch.dict(os.environ,{'D1_NPM_REGISTRY':value},clear=True):
                with self.assertRaises(OracleFailure):chalk_registry_from_environment()
