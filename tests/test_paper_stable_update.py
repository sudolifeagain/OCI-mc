import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.manage_paper_artifact import (
    PaperArtifact,
    finalize_artifact,
    hash_file,
    install_artifact,
    load_artifact,
    rollback_artifact,
)
from scripts.paper_stable_update import (
    PaperBuild,
    build_updated_manifest,
    load_manifest,
    select_latest_stable,
    update_required,
)


def _build(build_id: int, channel: str = "STABLE") -> dict[str, object]:
    digest = f"{build_id:064x}"
    name = f"paper-26.2-{build_id}.jar"
    return {
        "id": build_id,
        "channel": channel,
        "downloads": {
            "server:default": {
                "name": name,
                "url": f"https://fill-data.papermc.io/v1/objects/{digest}/{name}",
                "size": 64_000_000,
                "checksums": {"sha256": digest},
            }
        },
    }


def _manifest(build_id: int = 62, channel: str = "BETA") -> dict[str, object]:
    digest = f"{build_id:064x}"
    return {
        "schema_version": 1,
        "updated_at": "2026-07-20",
        "minecraft_version": "26.2",
        "paper": {
            "version": f"26.2-{build_id}",
            "channel": channel,
            "filename": "paper.jar",
            "url": (
                "https://fill-data.papermc.io/v1/objects/"
                f"{digest}/paper-26.2-{build_id}.jar"
            ),
            "hash": {"algorithm": "sha256", "value": digest},
        },
    }


class PaperStableDetectionTests(unittest.TestCase):
    def test_no_stable_returns_none(self) -> None:
        self.assertIsNone(select_latest_stable([_build(62, "BETA")]))

    def test_latest_stable_uses_highest_build_id(self) -> None:
        selected = select_latest_stable([_build(41), _build(62, "BETA"), _build(43)])
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.build_id, 43)

    def test_beta_moves_to_stable_even_when_stable_build_is_older(self) -> None:
        build = PaperBuild(50, "STABLE", "paper-26.2-50.jar", "url", "0" * 64, 1)
        self.assertTrue(update_required(_manifest(62, "BETA"), build))

    def test_current_or_older_stable_does_not_update(self) -> None:
        build = PaperBuild(50, "STABLE", "paper-26.2-50.jar", "url", "0" * 64, 1)
        self.assertFalse(update_required(_manifest(50, "STABLE"), build))
        self.assertFalse(update_required(_manifest(51, "STABLE"), build))

    def test_updated_manifest_is_fixed_to_26_2_stable(self) -> None:
        selected = select_latest_stable([_build(43)])
        assert selected is not None
        updated = build_updated_manifest(_manifest(), selected, "2026-07-21")
        self.assertEqual(updated["minecraft_version"], "26.2")
        self.assertEqual(updated["paper"]["version"], "26.2-43")
        self.assertEqual(updated["paper"]["channel"], "STABLE")
        self.assertEqual(updated["paper"]["source_filename"], "paper-26.2-43.jar")

    def test_manifest_rejects_other_minecraft_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "manifest.json"
            manifest = _manifest()
            manifest["minecraft_version"] = "26.3"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "26.2"):
                load_manifest(path)

    def test_stable_rejects_unapproved_download_host(self) -> None:
        build = _build(43)
        build["downloads"]["server:default"]["url"] = "https://example.com/paper-26.2-43.jar"
        with self.assertRaisesRegex(ValueError, "許可されていない"):
            select_latest_stable([build])


class PaperArtifactDeploymentTests(unittest.TestCase):
    def _artifact(self, content: bytes) -> PaperArtifact:
        return PaperArtifact(
            version="26.2-70",
            build_id=70,
            channel="STABLE",
            url=(
                "https://fill-data.papermc.io/v1/objects/hash/"
                "paper-26.2-70.jar"
            ),
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
        )

    def test_install_and_rollback_restore_previous_jar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "paper" / "paper.jar"
            state = root / "runtime" / "state.json"
            backup = root / "runtime" / "paper.jar.previous"
            target.parent.mkdir()
            target.write_bytes(b"old-paper")
            artifact = self._artifact(b"new-paper")

            def fake_download(_: PaperArtifact, destination: Path) -> None:
                destination.write_bytes(b"new-paper")

            with patch("scripts.manage_paper_artifact.download_artifact", fake_download):
                changed = install_artifact(
                    artifact, target, state, backup, owner=None, group=None
                )
            self.assertTrue(changed)
            self.assertEqual(target.read_bytes(), b"new-paper")
            self.assertTrue(state.exists())
            self.assertTrue(backup.exists())

            restored = rollback_artifact(
                target, state, backup, owner=None, group=None
            )
            self.assertTrue(restored)
            self.assertEqual(target.read_bytes(), b"old-paper")
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())

    def test_finalize_removes_temporary_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "paper" / "paper.jar"
            state = root / "runtime" / "state.json"
            backup = root / "runtime" / "paper.jar.previous"
            target.parent.mkdir()
            target.write_bytes(b"old-paper")
            artifact = self._artifact(b"new-paper")

            def fake_download(_: PaperArtifact, destination: Path) -> None:
                destination.write_bytes(b"new-paper")

            with patch("scripts.manage_paper_artifact.download_artifact", fake_download):
                install_artifact(artifact, target, state, backup, None, None)
            self.assertTrue(finalize_artifact(target, state, backup))
            self.assertEqual(hash_file(target), artifact.sha256)
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())

    def test_hash_mismatch_does_not_replace_current_jar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "paper" / "paper.jar"
            state = root / "runtime" / "state.json"
            backup = root / "runtime" / "paper.jar.previous"
            target.parent.mkdir()
            target.write_bytes(b"old-paper")
            artifact = self._artifact(b"expected-paper")

            def fake_download(_: PaperArtifact, destination: Path) -> None:
                destination.write_bytes(b"tampered-paper")
                raise ValueError("Paper artifactのSHA-256検証に失敗")

            with patch("scripts.manage_paper_artifact.download_artifact", fake_download):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    install_artifact(artifact, target, state, backup, None, None)
            self.assertEqual(target.read_bytes(), b"old-paper")
            self.assertFalse(state.exists())
            self.assertFalse(backup.exists())

    def test_loader_rejects_other_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "manifest.json"
            manifest = _manifest()
            manifest["minecraft_version"] = "26.3"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "26.2"):
                load_artifact(path)


if __name__ == "__main__":
    unittest.main()
