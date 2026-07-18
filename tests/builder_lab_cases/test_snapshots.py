import io
import json
import tarfile
import unittest

from builder_lab.snapshots import SnapshotRejected, collect_declared_snapshot


ARTIFACT = {
    "schema_version": "1.0",
    "revision": 1,
    "stage": "agent_build",
    "art_direction": "Editorial navigator",
    "body_html": '<section class="kaigo-widget"></section>',
    "css": ".kaigo-widget { color: #123; }",
    "theme_tokens": {"accent": "#123456"},
    "suggested_actions": ["Начать"],
}


def tar_bytes(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for entry in entries:
            if len(entry) == 2:
                name, content = entry
                info = tarfile.TarInfo(name)
                data = content if isinstance(content, bytes) else content.encode("utf-8")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                name, kind, target = entry
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = target
                archive.addfile(info)
    return output.getvalue()


def valid_archive(extra=()):
    return tar_bytes(
        [
            ("out/widget-artifact.json", json.dumps(ARTIFACT)),
            ("out/build-report.json", json.dumps({"validator": "passed"})),
            *extra,
        ]
    )


class SnapshotCollectionTests(unittest.TestCase):
    def test_collects_only_declared_json_files(self):
        artifact, report = collect_declared_snapshot(
            valid_archive((("workspace/source.js", "diagnostic only"),))
        )
        self.assertEqual(artifact.revision, 1)
        self.assertEqual(report, {"validator": "passed"})

    def test_rejects_traversal_absolute_and_drive_paths(self):
        for path in ("../out/widget-artifact.json", "/etc/passwd", "C:/Windows/system.ini"):
            with self.subTest(path=path), self.assertRaises(SnapshotRejected):
                collect_declared_snapshot(valid_archive(((path, "x"),)))

    def test_rejects_links_and_special_members(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind), self.assertRaises(SnapshotRejected):
                collect_declared_snapshot(valid_archive((("out/link", kind, "target"),)))

    def test_rejects_duplicate_declared_path(self):
        archive = valid_archive(
            (("out/widget-artifact.json", json.dumps(ARTIFACT)),)
        )
        with self.assertRaisesRegex(SnapshotRejected, "duplicate"):
            collect_declared_snapshot(archive)

    def test_rejects_missing_declared_path(self):
        archive = tar_bytes((("out/widget-artifact.json", json.dumps(ARTIFACT)),))
        with self.assertRaisesRegex(SnapshotRejected, "missing"):
            collect_declared_snapshot(archive)

    def test_rejects_member_count_and_byte_limits(self):
        many = tuple((f"diagnostics/{index}.txt", "x") for index in range(6))
        with self.assertRaisesRegex(SnapshotRejected, "members"):
            collect_declared_snapshot(valid_archive(many), max_members=5)
        with self.assertRaisesRegex(SnapshotRejected, "bytes"):
            collect_declared_snapshot(valid_archive(), max_total_bytes=20)

    def test_rejects_large_declared_file(self):
        huge = {**ARTIFACT, "body_html": "x" * 3000}
        archive = tar_bytes(
            (
                ("out/widget-artifact.json", json.dumps(huge)),
                ("out/build-report.json", "{}"),
            )
        )
        with self.assertRaisesRegex(SnapshotRejected, "file"):
            collect_declared_snapshot(archive, max_file_bytes=1024)

    def test_rejects_invalid_json_and_non_object_report(self):
        invalid = tar_bytes(
            (
                ("out/widget-artifact.json", "not-json"),
                ("out/build-report.json", "{}"),
            )
        )
        with self.assertRaisesRegex(SnapshotRejected, "JSON"):
            collect_declared_snapshot(invalid)
        non_object = tar_bytes(
            (
                ("out/widget-artifact.json", json.dumps(ARTIFACT)),
                ("out/build-report.json", "[]"),
            )
        )
        with self.assertRaisesRegex(SnapshotRejected, "object"):
            collect_declared_snapshot(non_object)


if __name__ == "__main__":
    unittest.main()
