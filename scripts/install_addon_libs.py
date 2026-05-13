#!/usr/bin/env python3
"""Install embedded WoW addon libraries from pkgmeta.yaml externals."""

import argparse
import html.parser
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SKIPPED_NAMES = {".git", ".github", ".pkgmeta", ".svn"}


class DirectoryListingParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs = dict(attrs)
        href = attrs.get("href")
        if href:
            self.hrefs.append(href)


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


def run(command):
    subprocess.run(command, check=True)


def copy_export(source, target):
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=ignored_export_names)


def ignored_export_names(_directory, names):
    return [
        name
        for name in names
        if name in SKIPPED_NAMES or name.startswith(".")
    ]


def clone_git(source_url, export_dir):
    run(["git", "clone", "--depth", "1", source_url, str(export_dir)])


def listing_hrefs(source_url):
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "MDT addon library installer"},
    )
    with urllib.request.urlopen(request) as response:
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            raise ValueError(f"{source_url} did not return a directory listing")
        html = response.read().decode("utf-8", errors="replace")

    parser = DirectoryListingParser()
    parser.feed(html)
    return parser.hrefs


def download_url(source_url, target_file):
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "MDT addon library installer"},
    )
    with urllib.request.urlopen(request) as response:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with target_file.open("wb") as output:
            shutil.copyfileobj(response, output)


def export_http_directory(source_url, export_dir):
    source_url = source_url.rstrip("/") + "/"
    export_dir.mkdir(parents=True, exist_ok=True)

    for href in listing_hrefs(source_url):
        parsed_href = urllib.parse.urlparse(href)
        if parsed_href.scheme or parsed_href.netloc or parsed_href.query or parsed_href.fragment:
            continue
        if "/" in href.rstrip("/"):
            continue

        name = urllib.parse.unquote(href.rstrip("/")).rsplit("/", 1)[-1]
        if href in {"../", "./"} or name in SKIPPED_NAMES or name.startswith("."):
            continue

        item_url = urllib.parse.urljoin(source_url, href)
        if href.endswith("/"):
            export_http_directory(item_url, export_dir / name)
        else:
            download_url(item_url, export_dir / name)


def install_external(root, target_path, source_url, dry_run=False):
    target = checked_target(root, target_path)
    print(f"Installing {target_path} from {source_url}", flush=True)

    if dry_run:
        return

    with tempfile.TemporaryDirectory(prefix="mdt-lib-") as temp_name:
        export_dir = Path(temp_name) / "export"
        if urllib.parse.urlparse(source_url).netloc == "github.com":
            clone_git(source_url, export_dir)
        else:
            try:
                export_http_directory(source_url, export_dir)
            except (urllib.error.URLError, ValueError) as error:
                if shutil.which("svn") is None:
                    raise RuntimeError(
                        f"Could not export {source_url} over HTTP and svn is not installed"
                    ) from error
                run(["svn", "export", "--force", source_url, str(export_dir)])

        copy_export(export_dir, target)


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
    args = parser.parse_args()

    root = Path.cwd().resolve()
    manifest_path = (root / args.manifest).resolve()
    externals = read_externals(manifest_path)

    for target_path, source_url in externals:
        install_external(root, target_path, source_url, dry_run=args.dry_run)

    print(f"Installed {len(externals)} addon libraries.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
