"""No native rebuild: exact-byte startup/ELF/GDB comparison plus two hello-world toys."""
from pathlib import Path
import base64, hashlib, json, os, re, resource, shutil, signal, subprocess, sys, tarfile, tempfile, urllib.error, urllib.parse, urllib.request, zipfile

CONTROL = Path(__file__).resolve().parent
ROOT = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
INPUTS = json.loads((CONTROL / "startup-comparison-inputs.json").read_text())
DATA = ROOT / "startup-comparison-inputs"
REPORTS = ROOT / "startup-comparison-reports"
REPORTS.mkdir(exist_ok=True)
assert os.environ["GITHUB_REPOSITORY"] == "embire2/codex"
assert os.environ["GITHUB_REF"] == "refs/heads/magnetapi/coderx-01672-20261112"


def sha(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def response(url, token=None):
    # Authorization is sent only to the first fixed GitHub API URL, never to storage redirects.
    headers = {"User-Agent": "MagnetAPI-bounded-native-diagnostics", "Accept": "application/vnd.github+json"}
    if token:
        assert url.startswith("https://api.github.com/repos/embire2/codex/actions/")
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(NoRedirect)
    for _ in range(4):
        request = urllib.request.Request(url, headers=headers)
        try:
            return opener.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise RuntimeError("Public/owned artifact request failed HTTP " + str(error.code)) from None
            url = urllib.parse.urljoin(url, error.headers["Location"])
            parsed = urllib.parse.urlsplit(url)
            assert parsed.scheme == "https" and not parsed.username and not parsed.password
            assert parsed.hostname in {"api.github.com", "github.com", "registry.npmjs.org"} or parsed.hostname.endswith((".blob.core.windows.net", ".actions.githubusercontent.com", ".githubusercontent.com"))
            headers = {"User-Agent": "MagnetAPI-bounded-native-diagnostics"}
    raise RuntimeError("Too many bounded artifact redirects")


def download(url, path, *, expected, algorithm="sha256", token=None, limit=400_000_000):
    size = 0
    with response(url, token) as source, path.open("xb") as output:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            size += len(part)
            assert size <= limit
            output.write(part)
    assert sha(path, algorithm) == expected


def fetch():
    DATA.mkdir(exist_ok=False)
    token = os.environ["GH_ARTIFACT_READ_TOKEN"]
    api = "https://api.github.com/repos/embire2/codex/actions/artifacts/" + str(INPUTS["failedArtifactId"])
    with response(api, token) as stream:
        metadata = json.loads(stream.read(100_000))
    assert metadata["id"] == INPUTS["failedArtifactId"] and metadata["expired"] is False
    assert metadata["digest"] == "sha256:" + INPUTS["failedZipSha256"]
    assert metadata["workflow_run"]["id"] == 43289590619
    assert metadata["workflow_run"]["head_sha"] == "00d15ead6dbbf4d0f8c236674e63179c68fe000d"
    archive = DATA / "failed-NOT-FOR-RELEASE.zip"
    download(api + "/zip", archive, expected=INPUTS["failedZipSha256"], token=token)
    with zipfile.ZipFile(archive) as source:
        assert len(source.infolist()) == 6 and sum(i.file_size for i in source.infolist()) < 400_000_000
        with source.open("codex-NOT-FOR-RELEASE") as stream, (DATA / "custom-NOT-FOR-RELEASE").open("xb") as output:
            shutil.copyfileobj(stream, output, 1024 * 1024)
    assert sha(DATA / "custom-NOT-FOR-RELEASE") == INPUTS["failedNativeSha256"]
    original = DATA / "official-package.tgz"
    spec = INPUTS["stockPackage"]
    expected = base64.b64decode(spec["integrity"].removeprefix("sha512-")).hex()
    download(spec["tarball"], original, expected=expected, algorithm="sha512")
    with tarfile.open(original) as source:
        entry = source.getmember("package/vendor/x86_64-unknown-linux-musl/bin/codex")
        assert entry.isfile() and entry.size == INPUTS["stockNativeBytes"]
        with source.extractfile(entry) as stream, (DATA / "official").open("xb") as output:
            shutil.copyfileobj(stream, output, 1024 * 1024)
    assert sha(DATA / "official") == INPUTS["stockNativeSha256"]
    for name in ("official", "custom-NOT-FOR-RELEASE"):
        (DATA / name).chmod(0o700)
    (REPORTS / "verified-inputs.json").write_text(json.dumps({"officialNativeSha256": INPUTS["stockNativeSha256"], "customNativeSha256": INPUTS["failedNativeSha256"], "ownedArtifactId": INPUTS["failedArtifactId"], "zipSha256": INPUTS["failedZipSha256"], "releaseAccepted": False}, indent=2) + "\n")


def limits(cpu):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def run(args, home, *, timeout=15, cpu=10):
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(home / ".codex"),
           "CODEXCLAUDE_MANAGED": "1", "CODEX_DISABLE_UPDATE_CHECK": "1", "TERM": "dumb"}
    child = subprocess.Popen(args, cwd=home, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             preexec_fn=lambda: limits(cpu), start_new_session=True)
    timed_out = False
    try:
        output, _ = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(child.pid, signal.SIGKILL)
        output, _ = child.communicate(timeout=10)
    return {"returncode": child.returncode, "timedOut": timed_out, "output": output[:200_000]}


def elf(binary, home, label):
    header = run(["readelf", "-h", str(binary)], home)
    program = run(["readelf", "-l", str(binary)], home)
    dynamic = run(["readelf", "-d", str(binary)], home)
    assert all(row["returncode"] == 0 for row in (header, program, dynamic))
    for suffix, value in (("header", header), ("program", program), ("dynamic", dynamic)):
        (REPORTS / (label + "-elf-" + suffix + ".txt")).write_text(value["output"])
    return {"pie": "DYN (Position-Independent Executable file)" in header["output"],
            "hasInterpreter": "INTERP" in program["output"], "hasNeeded": "(NEEDED)" in dynamic["output"],
            "hasRelro": "GNU_RELRO" in program["output"], "hasBindNow": "BIND_NOW" in dynamic["output"]}


def compare():
    assert sha(DATA / "official") == INPUTS["stockNativeSha256"]
    assert sha(DATA / "custom-NOT-FOR-RELEASE") == INPUTS["failedNativeSha256"]
    results = {}
    with tempfile.TemporaryDirectory(prefix="owned-startup-diagnostic-") as directory:
        home = Path(directory)
        results["packages"] = run(["dpkg-query", "-W", "musl", "musl-dev", "musl-tools", "gdb", "gcc", "binutils"], home)
        results["aslrSetting"] = Path("/proc/sys/kernel/randomize_va_space").read_text().strip()
        assert results["aslrSetting"] != "0"
        for label, binary in (("official", DATA / "official"), ("custom", DATA / "custom-NOT-FOR-RELEASE")):
            result = run([str(binary), "--version"], home)
            results[label] = {"version": result, "elf": elf(binary, home, label)}
            commands = ["gdb", "--batch", "-nx", "-nh", "-iex", "set auto-load safe-path /nonexistent",
                        "-iex", "set auto-load gdb-scripts off", "-iex", "set auto-load python-scripts off",
                        "-iex", "set auto-load local-gdbinit off", "-iex", "set auto-load libthread-db off",
                        "-iex", "set debuginfod enabled off",
                        "-ex", "set pagination off", "-ex", "set confirm off",
                        "-ex", "set disable-randomization off", "-ex", "run", "-ex", "p $_siginfo",
                        "-ex", "info registers", "-ex", "x/16i $pc", "-ex", "thread apply all bt 20",
                        "-ex", "info proc mappings", "-ex", "info files", "--args", str(binary), "--version"]
            report = run(commands, home, timeout=45, cpu=25)
            (REPORTS / (label + "-gdb.txt")).write_text(report["output"])
            results[label]["gdbReturncode"] = report["returncode"]
            results[label]["gdbTimedOut"] = report["timedOut"]
        spec_path = Path("/usr/lib/x86_64-linux-musl/musl-gcc.specs")
        assert spec_path.is_file()
        (REPORTS / "musl-gcc.specs.txt").write_bytes(spec_path.read_bytes())
        source = home / "linkage-toy.rs"
        source.write_text('fn main() { println!("LINKAGE_TOY_01672"); }\n')
        rust_home = Path(os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")))
        rustc = rust_home / "toolchains/1.95.0-x86_64-unknown-linux-gnu/bin/rustc"
        assert rustc.is_file()
        base = [str(rustc), "--edition=2024", "--target", "x86_64-unknown-linux-musl",
                "-C", "opt-level=1", "-C", "codegen-units=16", "-C", "strip=symbols",
                "-C", "linker=/usr/bin/x86_64-linux-musl-gcc"]
        results["compiler"] = run([str(rustc), "--version"], home)
        assert results["compiler"]["returncode"] == 0 and "rustc 1.95.0 " in results["compiler"]["output"]
        for label, extra in (("toy-default", []), ("toy-no-dynamic-linker", ["-C", "link-arg=-Wl,--no-dynamic-linker"])):
            binary = home / label
            compiled = run([*base, *extra, str(source), "-o", str(binary)], home, timeout=60, cpu=40)
            assert compiled["returncode"] == 0, compiled["output"]
            result = run([str(binary)], home)
            results[label] = {"run": result, "elf": elf(binary, home, label), "linkerCandidateOnly": extra}
        original, candidate = results["toy-default"], results["toy-no-dynamic-linker"]
        results["linkerHypothesisSupportedByToy"] = bool(original["elf"]["hasInterpreter"] and original["run"]["returncode"] == -11
            and candidate["elf"]["pie"] and not candidate["elf"]["hasInterpreter"] and not candidate["elf"]["hasNeeded"]
            and candidate["run"]["returncode"] == 0 and "LINKAGE_TOY_01672" in candidate["run"]["output"])
        assert results["official"]["version"]["returncode"] == 0 and "0.167.2" in results["official"]["version"]["output"]
    results.update(qualification="diagnostic comparison and tiny synthetic linkage toy only; no native rebuild, product flag approval, package publication or release acceptance", releaseAccepted=False, productionNetworkOrCredentials=False)
    (REPORTS / "comparison.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"diagnosticsCompleted": True, "customVersionReturncode": results["custom"]["version"]["returncode"], "toyHypothesisSupported": results["linkerHypothesisSupportedByToy"], "releaseAccepted": False}))


if __name__ == "__main__":
    {"fetch": fetch, "compare": compare}[sys.argv[1]]()
