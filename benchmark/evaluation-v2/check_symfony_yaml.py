"""Replay the YAML round trip on the two commits reviewed in the campaign."""

import argparse
import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile


BASE = "f8beaaca25d084c4abf1c65d461ba615b6238ae5"
HEAD = "4cc9f97a0a48f87c3e0eec3cbc477185cdf1ef65"
YAML_COMPONENT = "src/Symfony/Component/Yaml"
PROBE = r"""<?php
function trigger_deprecation(...$args): void {}
spl_autoload_register(static function (string $class): void {
    $file = __DIR__.'/src/'.str_replace(chr(92), '/', $class).'.php';
    if (is_file($file)) {
        require $file;
    }
});

use Symfony\Component\Yaml\Yaml;

$input = [['text' => "#one\n#two"]];
$flags = Yaml::DUMP_MULTI_LINE_LITERAL_BLOCK;
$parsed = Yaml::parse(Yaml::dump($input, 2, 4, $flags));
echo json_encode($parsed, JSON_THROW_ON_ERROR).PHP_EOL;
"""


def round_trip(repo: Path, revision: str) -> str:
    archive = subprocess.check_output([
        "git", "-C", str(repo), "archive", "--format=zip", revision, YAML_COMPONENT,
    ])
    with TemporaryDirectory() as directory:
        with ZipFile(io.BytesIO(archive)) as source:
            source.extractall(directory)
        probe = Path(directory) / "round-trip.php"
        probe.write_text(PROBE, encoding="utf-8")
        return subprocess.check_output(["php", str(probe)], encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, help="A Git clone containing both pinned Symfony campaign commits")
    repo = parser.parse_args().repo
    for label, revision, expected in (
        ("base", BASE, r'[{"text":"#one\n#two"}]'),
        ("head", HEAD, '[{"text":""}]'),
    ):
        observed = round_trip(repo, revision)
        print(f"{label} {observed}")
        if observed != expected:
            raise SystemExit(f"Expected {expected} at {revision}, got {observed}")


if __name__ == "__main__":
    main()
