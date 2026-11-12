"""No-secret, immutable-source CoderX build. Never publishes or reads client credentials."""
from pathlib import Path
import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

CONTROL = Path(__file__).resolve().parent
PLAN = json.loads((CONTROL / "plan.json").read_text())
ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, dest, *, expected_sha=None, integrity=None, limit=600_000_000):
    assert url.startswith("https://")
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha512() if integrity else hashlib.sha256()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "MagnetAPI-native-build/0.167.2"})
    with urllib.request.urlopen(request, timeout=90) as response, dest.open("wb") as output:
        assert response.geturl().startswith("https://")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            assert size <= limit, "Download exceeded its public-artifact bound"
            output.write(chunk)
            digest.update(chunk)
    actual = "sha512-" + base64.b64encode(digest.digest()).decode() if integrity else digest.hexdigest()
    assert actual == (integrity or expected_sha), "Public artifact checksum did not match its pinned provenance"
    return size


def extract(archive, target):
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(target, filter="data")


def append_env(name, value):
    assert "\n" not in value and "\r" not in value
    with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
        stream.write(name + "=" + value + "\n")


def platform(name):
    return next(p for p in PLAN["platforms"] if p["id"] == name)


def prepare(name):
    spec = platform(name)
    archive = ROOT / ".managed-source.tar.gz"
    download(PLAN["source"]["url"], archive, expected_sha=PLAN["source"]["sha256"], limit=25_000_000)
    unpacked = ROOT / ".managed-source"
    extract(archive, unpacked)
    children = list(unpacked.iterdir())
    assert len(children) == 1
    source = children[0]
    assert not (source / "control").exists()
    for item in source.iterdir():
        target = ROOT / item.name
        assert not target.exists(), "Source extraction would replace an unrelated path"
        shutil.move(str(item), target)
    raw = gzip.decompress(base64.b64decode((CONTROL / "native-branding.patch.gz.b64").read_bytes()))
    assert hashlib.sha256(raw).hexdigest() == PLAN["patchSha256"]
    patch = ROOT / ".managed-branding.patch"
    patch.write_bytes(raw)
    boundaries = json.loads((CONTROL / "native-branding-changes.json").read_text())
    for row in boundaries["changes"]:
        assert sha(ROOT / row["path"]) == row["before"], row["path"]
    subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--check", "--whitespace=nowarn", str(patch)], cwd=ROOT, check=True)
    subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", str(patch)], cwd=ROOT, check=True)
    changed = {row["path"]: row for row in boundaries["changes"]}
    # Assert every file outside the reviewed presentation/test patch remains identical.
    with tarfile.open(archive) as tf:
        for member in tf:
            if not member.isfile():
                continue
            rel = Path(*Path(member.name).parts[1:]).as_posix()
            original_hash = hashlib.sha256(tf.extractfile(member).read()).hexdigest()
            assert sha(ROOT / rel) == changed.get(rel, {}).get("after", original_hash), rel
    lock = ROOT / "codex-rs/Cargo.lock"
    assert sha(lock) == PLAN["upstreamLockSha256"]
    old = tomllib.loads(lock.read_text())
    old_bytes = lock.read_text()
    sections = old_bytes.split("[[package]]")
    changes = 0
    for index in range(1, len(sections)):
        if not re.search(r"^source\s*=", sections[index], re.M):
            sections[index], count = re.subn(r'^version = "0\.0\.0"$', 'version = "0.167.2"', sections[index], flags=re.M)
            assert count == 1
            changes += 1
    assert changes == 171
    lock.write_bytes("[[package]]".join(sections).encode())
    new = tomllib.loads(lock.read_text())
    for before, after in zip(old["package"], new["package"], strict=True):
        assert {k: v for k, v in before.items() if k != "version"} == {k: v for k, v in after.items() if k != "version"}
        assert after["version"] == (before["version"] if before.get("source") else "0.167.2")
    assert sha(lock) == PLAN["normalizedLockSha256"]
    original = ROOT / ".upstream-platform.tgz"
    download(spec["tarball"], original, integrity=spec["integrity"])
    upstream = ROOT / ".upstream-platform"
    extract(original, upstream)
    helper = upstream / "package/vendor" / spec["target"] / "codex-resources/bwrap"
    if helper.exists():
        append_env("CODEX_BWRAP_SHA256", sha(helper))
    append_env("STABLE_GIT_COMMIT", PLAN["source"]["commit"])
    print(json.dumps({"prepared": name, "sourceSha256": PLAN["source"]["sha256"], "patchSha256": PLAN["patchSha256"], "onlyWorkspaceLockVersionsChanged": changes}))


def zig(name):
    spec = PLAN["zig"]["aarch64-linux" if name.endswith("arm64") else "x86_64-linux"]
    archive = Path(os.environ["RUNNER_TEMP"]) / "managed-zig.tar.xz"
    download(spec["tarball"], archive, expected_sha=spec["shasum"], limit=60_000_000)
    target = archive.parent / "managed-zig"
    extract(archive, target)
    entries = list(target.iterdir())
    assert len(entries) == 1 and (entries[0] / "zig").is_file()
    with Path(os.environ["GITHUB_PATH"]).open("a") as output:
        output.write(str(entries[0]) + "\n")


def goal_test(name):
    result = subprocess.run(["cargo", "test", "--locked", "--release", "--target", platform(name)["target"], "-p", "codex-tui", "--lib", PLAN["goalTest"], "--", "--exact"], cwd=ROOT / "codex-rs", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout)
    assert result.returncode == 0 and "1 passed; 0 failed" in result.stdout, "Compiled saved-goal regression did not run and pass exactly once"


def verify_linux_x64_elf(name):
    assert name == "linux-x64", "This linker correction is restricted to Linux x64"
    expected = "-C link-arg=-Wl,--no-dynamic-linker"
    assert os.environ.get("CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_RUSTFLAGS") == expected
    assert not os.environ.get("RUSTFLAGS"), "No global Rust flags may override the reviewed target flag"
    spec = platform(name)
    binary = ROOT / "codex-rs/target" / spec["target"] / "release/codex"
    commands = {"header": ["readelf", "-h", str(binary)], "programs": ["readelf", "-W", "-l", str(binary)], "dynamic": ["readelf", "-d", str(binary)]}
    reports = {key: subprocess.check_output(command, text=True, timeout=30) for key, command in commands.items()}
    facts = {"pie": "DYN (Position-Independent Executable file)" in reports["header"] and "PIE" in reports["dynamic"], "hasInterpreter": "INTERP" in reports["programs"], "hasNeeded": "(NEEDED)" in reports["dynamic"], "relro": "GNU_RELRO" in reports["programs"], "bindNow": "BIND_NOW" in reports["dynamic"], "kernelAslr": Path("/proc/sys/kernel/randomize_va_space").read_text().strip()}
    output = ROOT / "managed-diagnostics"
    output.mkdir(exist_ok=True)
    for key, text in reports.items():
        (output / ("elf-" + key + ".txt")).write_text(text)
    record = {"targetRustflags": expected, "target": spec["target"], "nativeSha256": sha(binary), "elf": facts}
    (output / "elf-linking.json").write_text(json.dumps(record, indent=2) + "\n")
    assert facts["pie"] and not facts["hasInterpreter"] and not facts["hasNeeded"] and facts["relro"] and facts["bindNow"] and facts["kernelAslr"] == "2", "The shipped x64 runtime must be a hardened static PIE without an external dynamic loader"
    return record


def pack(name):
    spec = platform(name)
    executable = "codex.exe" if name.startswith("win32") else "codex"
    binary = ROOT / "codex-rs/target" / spec["target"] / "release" / executable
    assert binary.is_file()
    destination = ROOT / "managed-release" / name
    shutil.copytree(ROOT / ".upstream-platform/package", destination)
    relative = Path("vendor") / spec["target"] / "bin" / executable
    before = {str(p.relative_to(destination)): sha(p) for p in destination.rglob("*") if p.is_file()}
    shutil.copy2(binary, destination / relative)
    for rel, digest in before.items():
        if rel not in [str(relative), "package.json"]:
            assert sha(destination / rel) == digest, "An upstream helper/protocol resource changed"
    manifest = {"name": "@codexapi/codexclaude", "version": PLAN["wrapperVersion"] + "-" + name, "description": "CoderX native runtime for the managed codexclaude client", "license": "Apache-2.0", "os": spec["os"], "cpu": spec["cpu"], "files": ["vendor", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "provenance.json"], "engines": {"node": ">=20"}, "publishConfig": {"access": "public"}}
    (destination / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for legal in ["LICENSE", "NOTICE"]:
        shutil.copy2(ROOT / legal, destination / legal)
    (destination / "THIRD_PARTY_NOTICES.md").write_text("# Third-party notices\n\nRuntime source: OpenAI Codex rust-v0.167.2. Static managed presentation is modified by MagnetAPI.org. Upstream helper/protocol resources, LICENSE and NOTICE remain unchanged. See provenance.json for immutable source, patch and upstream package integrity.\n")
    provenance = {"source": PLAN["source"], "patchSha256": PLAN["patchSha256"], "upstreamPackage": spec, "nativeSha256": sha(binary), "compiler": subprocess.check_output(["rustc", "--version"], text=True).strip(), "profile": {"optimization": 1, "lto": False, "codegenUnits": 16, "strip": "symbols"}, "reviewedGoalTest": PLAN["goalTest"], "ciCommit": os.environ.get("GITHUB_SHA"), "ciRunId": os.environ.get("GITHUB_RUN_ID")}
    provenance["linking"] = verify_linux_x64_elf(name)
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    output = ROOT / "managed-artifacts"
    output.mkdir(exist_ok=True)
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    assert npm
    command = [npm]
    if os.name == "nt":
        node = shutil.which("node")
        assert node
        candidates = [Path(npm).parent / "node_modules/npm/bin/npm-cli.js", Path(node).parent / "node_modules/npm/bin/npm-cli.js"]
        npm_cli = next((p for p in candidates if p.is_file()), None)
        assert npm_cli, "Locate npm-cli.js without invoking a shell shim"
        command = [node, str(npm_cli)]
    result = subprocess.run([*command, "pack", "--ignore-scripts", "--json", "--pack-destination", str(output)], cwd=destination, text=True, stdout=subprocess.PIPE, check=True, shell=False)
    receipt = json.loads(result.stdout)[0]
    package = output / receipt["filename"]
    receipt["sha256"] = sha(package)
    receipt["nativeSha256"] = provenance["nativeSha256"]
    receipt["platform"] = name
    (output / (name + "-receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    shutil.copy2(ROOT / "native-smoke.json", output / (name + "-smoke.json"))
    print(json.dumps({key: receipt[key] for key in ["filename", "sha256", "nativeSha256", "platform"]}))


if __name__ == "__main__":
    mode, name = sys.argv[1:]
    {"prepare": prepare, "zig": zig, "goal-test": goal_test, "elf": verify_linux_x64_elf, "pack": pack}[mode](name)
