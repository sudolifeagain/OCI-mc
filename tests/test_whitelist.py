import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from utils.whitelist import add_verified_profile, resolve_java_profile


class VerifiedWhitelistTests(unittest.TestCase):
    def test_resolves_official_profile_and_validates_name(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {'name': 'Player', 'id': '12345678123456781234567812345678'}
        with patch('utils.whitelist.requests.get', return_value=response):
            profile = resolve_java_profile('player')
        self.assertEqual(profile, {'name': 'Player', 'uuid': '12345678-1234-5678-1234-567812345678'})

    def test_add_preserves_existing_entries_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'whitelist.json'
            before = [{'uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'name': 'Existing'}]
            path.write_text(json.dumps(before), encoding='utf-8')
            profile = {'uuid': '12345678-1234-5678-1234-567812345678', 'name': 'Player'}
            add_verified_profile(directory, profile)
            add_verified_profile(directory, profile)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), before + [profile])

    def test_rejects_same_name_with_different_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'whitelist.json'
            data = [{'uuid': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'name': 'Player'}]
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                add_verified_profile(directory, {'name': 'player', 'uuid': '12345678-1234-5678-1234-567812345678'})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), data)
