"""Exercise Hermes file sync with native paths and a disposable POSIX archive peer."""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import sys
import tarfile
import tempfile

from tools.credential_files import map_cache_path_to_container
from tools.environments.file_sync import FileSyncManager, iter_sync_files, unique_parent_dirs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-mismatch", action="store_true")
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    original = b"original audio"
    updated = b"edited by the remote"
    with tempfile.TemporaryDirectory(prefix="hermes-cache-path-") as tmp:
        home = Path(tmp) / "local"
        os.environ["HERMES_HOME"] = str(home)
        host_file = home / "cache" / "documents" / "native-inputs" / "voice.m4a"
        host_file.parent.mkdir(parents=True)
        host_file.write_bytes(original)
        remote_home = "/home/fixture/.hermes"
        wanted = remote_home + "/cache/documents/native-inputs/voice.m4a"
        nested = host_file.parent / "native-inputs" / "voice.m4a"
        assert map_cache_path_to_container(str(host_file), container_base=remote_home) == wanted
        remote = {}
        reported_destinations = []

        def upload(files):
            for local, dest in files:
                reported_destinations.append(dest)
                # Native SSH tar staging builds its relative name with the host
                # path implementation. The POSIX peer sees names from the tar.
                rel = Path(os.path.relpath(dest, remote_home)).as_posix()
                archive_name = str(PurePosixPath(remote_home, rel))
                remote[archive_name] = Path(local).read_bytes()

        def download(dest):
            with tarfile.open(dest, "w") as tar:
                for name, data in sorted(remote.items()):
                    entry = tarfile.TarInfo(name.lstrip("/"))
                    entry.size = len(data)
                    tar.addfile(entry, io.BytesIO(data))

        def delete(_):
            raise AssertionError("fixture never deletes an input")

        sync = FileSyncManager(
            lambda: iter_sync_files(remote_home),
            lambda *_: None,
            delete,
            bulk_upload_fn=upload,
            bulk_download_fn=download,
        )
        sync.sync(force=True)
        assert len(reported_destinations) == 1, reported_destinations
        assert remote == {wanted: original}, remote
        first_dest = reported_destinations[0]
        sync.sync_back()
        unchanged_added_nested_copy = nested.exists()
        remote[wanted] = updated
        sync.sync_back()
        edit_reached_original = host_file.read_bytes() == updated
        nested_has_edit = nested.exists() and nested.read_bytes() == updated

        for _ in range(3):
            sync.sync(force=True)
            sync.sync_back()
        files = sorted(
            p.relative_to(host_file.parent).as_posix()
            for p in host_file.parent.rglob("*.m4a")
        )
        receipt = {
            "revision": args.revision,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "outgoing_path": first_dest,
            "posix_parent_directories": unique_parent_dirs([(str(host_file), first_dest)]),
            "unchanged_download_added_nested_copy": unchanged_added_nested_copy,
            "remote_edit_reached_original": edit_reached_original,
            "nested_copy_got_edit": nested_has_edit,
            "files_after_three_round_trips": files,
        }
        print(json.dumps(receipt, indent=2), flush=True)
        if args.expect_mismatch:
            assert first_dest == remote_home + r"/cache/documents/native-inputs\voice.m4a"
        else:
            assert first_dest == wanted
            assert not unchanged_added_nested_copy
            assert edit_reached_original
            assert files == ["voice.m4a"]


if __name__ == "__main__":
    main()
