#!/usr/bin/env python3
"""Install embedded WoW addon libraries from pkgmeta.yaml externals."""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


PACKAGER_RELEASE_URL = "https://raw.githubusercontent.com/BigWigsMods/packager/{ref}/release.sh"


def read_externals(manifest_path):
    externals = []
    in_externals = False
    base_indent = None

    for raw_line in manifest_path.read_text(encoding="utf-8-sig").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if stripped == "externals:":
            in_externals = True
            base_indent = indent
            continue

        if in_externals and indent <= base_indent:
            break

        if not in_externals:
            continue

        if ":" not in stripped:
            raise ValueError(f"Unsupported externals line: {raw_line}")

        target, source = stripped.split(":", 1)
        target = target.strip().strip("'\"")
        source = source.strip().strip("'\"")
        if target and source:
            externals.append((target, source))

    if not externals:
        raise ValueError(f"No externals found in {manifest_path}")

    return externals


def checked_target(root, relative_path):
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"External target escapes project root: {relative_path}")
    return target


def run(command, cwd=None):
    subprocess.run(command, check=True, cwd=cwd)


def find_packager_bash():
    candidates = [
        shutil.which("bash"),
        "/opt/homebrew/bin/bash",
        "/usr/local/bin/bash",
    ]
    checked = []
    for candidate in candidates:
        if not candidate or candidate in checked:
            continue
        checked.append(candidate)
        path = Path(candidate)
        if not path.exists():
            continue
        result = subprocess.run(
            [
                str(path),
                "-c",
                "(( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3) ))",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return str(path)
    raise RuntimeError(
        "BigWigsMods/packager requires bash 4.3 or newer. Install a modern bash "
        "(for example, `brew install bash`)."
    )


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def copy_packager_export(source, target):
    remove_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def is_svn_external(source_url):
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme == "svn":
        return True
    if parsed.netloc in {"svn.curseforge.com", "svn.wowace.com"}:
        return True
    if parsed.netloc in {"repos.curseforge.com", "repos.wowace.com"}:
        return bool(re.search(r"/trunk(?:/|$)", parsed.path))
    return False


def download_packager_script(target, ref):
    download_url(PACKAGER_RELEASE_URL.format(ref=ref), target)
    target.chmod(0o755)


def find_packager_libs_dir(release_dir):
    candidates = [
        path
        for path in release_dir.glob("*/libs")
        if path.is_dir()
    ]
    if len(candidates) != 1:
        found = ", ".join(str(path) for path in candidates) or "none"
        raise RuntimeError(f"Expected one packaged libs directory, found {found}")
    return candidates[0]


def install_with_packager(
    root,
    manifest_path,
    externals,
    dry_run=False,
    packager_ref="v2",
    packager_script=None,
):
    print(
        f"Installing {len(externals)} addon libraries with BigWigsMods/packager@{packager_ref}",
        flush=True,
    )
    for target_path, source_url in externals:
        print(f"  {target_path} from {source_url}", flush=True)

    if dry_run:
        return

    bash = find_packager_bash()
    if shutil.which("git") is None:
        raise RuntimeError("git is required to run BigWigsMods/packager")
    if any(is_svn_external(source_url) for _, source_url in externals) and shutil.which("svn") is None:
        raise RuntimeError(
            "This pkgmeta.yaml contains SVN externals. BigWigsMods/packager uses "
            "svn checkout for these, but svn is not installed. Install subversion "
            "(for example, `brew install subversion`)."
        )

    with tempfile.TemporaryDirectory(prefix="mdt-packager-") as temp_name:
        temp_dir = Path(temp_name)
        release_dir = temp_dir / "release"
        script_path = Path(packager_script).resolve() if packager_script else temp_dir / "release.sh"
        if not packager_script:
            download_packager_script(script_path, packager_ref)

        run(
            [
                bash,
                str(script_path),
                "-d",  # skip uploads
                "-l",  # skip localization replacement
                "-z",  # skip zip creation
                "-w",
                "0",  # match this repo's GitHub Actions packager args
                "-r",
                str(release_dir),
                "-t",
                str(root),
                "-m",
                str(manifest_path),
            ],
            cwd=root,
        )

        packaged_libs = find_packager_libs_dir(release_dir)
        for target_path, _source_url in externals:
            packaged_external = packaged_libs.parent / target_path
            if not packaged_external.exists():
                raise RuntimeError(f"Packager did not create {target_path}")
            copy_packager_export(packaged_external, checked_target(root, target_path))


def download_url(source_url, target_file):
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "MDT addon library installer"},
    )
    with urllib.request.urlopen(request) as response:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("wb") as output:
            shutil.copyfileobj(response, output)


def main():
    parser = argparse.ArgumentParser(
        description="Install addon libraries declared in pkgmeta.yaml externals."
    )
    parser.add_argument(
        "--manifest",
        default="pkgmeta.yaml",
        help="Path to the pkgmeta.yaml file. Defaults to ./pkgmeta.yaml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the libraries that would be installed without changing files.",
    )
    parser.add_argument(
        "--packager-ref",
        default="v2",
        help="BigWigsMods/packager ref to use. Defaults to v2.",
    )
    parser.add_argument(
        "--packager-script",
        help="Use a local release.sh instead of downloading BigWigsMods/packager.",
    )
    args = parser.parse_args()

    root = Path.cwd().resolve()
    manifest_path = (root / args.manifest).resolve()
    externals = read_externals(manifest_path)

    install_with_packager(
        root,
        manifest_path,
        externals,
        dry_run=args.dry_run,
        packager_ref=args.packager_ref,
        packager_script=args.packager_script,
    )

    print(f"Installed {len(externals)} addon libraries.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
