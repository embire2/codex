"""One no-secret, no-publish Apple Silicon build within CircleCI Free limits.

55-minute own-process deadline; CircleCI Free has a separate one-hour job cap.
No contexts, caches, deployment, npm auth, client credentials or provider calls.
"""
from pathlib import Path
import hashlib, importlib.util, json, os, platform, re, shutil, signal, subprocess, sys, time, uuid, zipfile

CONTROL = Path(__file__).resolve().parent
ROOT = Path.cwd().resolve()
TARGET = "aarch64-apple-darwin"
DEADLINE = time.monotonic() + 55 * 60
DIAGNOSTICS = ROOT / "managed-diagnostics"
DIAGNOSTICS.mkdir(exist_ok=True)
ACTIVE = None


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def deadline(*args):
    raise TimeoutError("The scoped 55-minute native build deadline expired")


def run(args, *, cwd=ROOT, capture=False):
    global ACTIVE
    remaining = DEADLINE - time.monotonic()
    assert remaining > 0
    ACTIVE = subprocess.Popen(args, cwd=cwd, env=ENV, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE if capture else None,
                              stderr=subprocess.STDOUT if capture else None,
                              text=True, start_new_session=True)
    try:
        if capture:
            output, _ = ACTIVE.communicate(timeout=remaining)
        else:
            ACTIVE.wait(timeout=remaining)
            output = ""
        assert ACTIVE.returncode == 0, "Scoped native command failed: " + str(args[0])
        return output
    except BaseException:
        if ACTIVE.poll() is None:
            os.killpg(ACTIVE.pid, signal.SIGTERM)
            try:
                ACTIVE.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(ACTIVE.pid, signal.SIGKILL)
                ACTIVE.wait(timeout=10)
        raise
    finally:
        ACTIVE = None


def apply_owned_env(path):
    for line in path.read_text().splitlines():
        name, sep, value = line.partition("=")
        assert sep and re.fullmatch("[A-Z0-9_]+", name)
        assert name in {"CODEX_BWRAP_SHA256", "STABLE_GIT_COMMIT"}
        ENV[name] = value


def macos_minimum(path):
    text = run(["otool", "-arch", "arm64", "-l", str(path)], capture=True)
    values = re.findall(r"(?m)^\s*minos\s+(\d+(?:\.\d+)*)\s*$", text)
    if not values:
        values = [re.search(r"(?m)^\s*version\s+(\d+(?:\.\d+)*)\s*$", block).group(1)
                  for block in text.split("cmd LC_VERSION_MIN_MACOSX")[1:]]
    assert len(values) == 1
    return tuple(map(int, values[0].split(".")))


assert sys.version_info >= (3, 12)
assert sys.platform == "darwin" and platform.machine() == "arm64"
assert os.environ["CIRCLE_BRANCH"] == "magnetapi/coderx-01672-20261112"
assert os.environ["CIRCLE_SHA1"] == os.environ["MAGNET_REVIEWED_REVISION"]
assert re.fullmatch("[a-f0-9]{40}", os.environ["CIRCLE_SHA1"])
assert int(os.environ["MAGNET_FREE_CREDIT_RESERVE"]) >= 18000
assert int(os.environ["MAGNET_ARTIFACT_RETENTION_DAYS"]) == 1
assert not os.environ.get("CIRCLE_PULL_REQUEST") and not os.environ.get("CIRCLE_PR_NUMBER")
for key in ("CIRCLE_WORKFLOW_ID", "CIRCLE_WORKFLOW_JOB_ID", "MAGNET_CI_PIPELINE_ID"):
    uuid.UUID(os.environ[key])
assert int(os.environ["CIRCLE_BUILD_NUM"]) > 0
# Strip automatic CI/OIDC/account secrets from every build/tool/native child.
public = {"PATH", "LANG", "LC_ALL", "TMPDIR", "SDKROOT", "DEVELOPER_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
          "CIRCLE_SHA1", "CIRCLE_BRANCH", "CIRCLE_WORKFLOW_ID", "CIRCLE_WORKFLOW_JOB_ID", "CIRCLE_BUILD_NUM",
          "MAGNET_CI_PIPELINE_ID"}
ENV = {key: value for key, value in os.environ.items() if key in public}
home = ROOT / ".native-home"
home.mkdir(exist_ok=False)
empty_npm = ROOT / ".npmrc-user-empty"
empty_npm.write_text("")
empty_npm.chmod(0o600)
empty_npm_global = ROOT / ".npmrc-global-empty"
empty_npm_global.write_text("")
empty_npm_global.chmod(0o600)
owned_env = ROOT / ".native-build-env"
owned_env.write_text("")
ENV.update(HOME=str(home), USERPROFILE=str(home), GITHUB_WORKSPACE=str(ROOT), GITHUB_ENV=str(owned_env),
           CARGO_HOME=str(ROOT / ".circle-cargo"), RUSTUP_HOME=str(ROOT / ".circle-rustup"),
           RUSTUP_TOOLCHAIN="1.95.0", CARGO_BUILD_JOBS="4", CARGO_NET_GIT_FETCH_WITH_CLI="true",
           CARGO_PROFILE_RELEASE_LTO="false", CARGO_PROFILE_RELEASE_CODEGEN_UNITS="16",
           CARGO_PROFILE_RELEASE_OPT_LEVEL="1", CARGO_PROFILE_RELEASE_DEBUG="0", CARGO_PROFILE_RELEASE_STRIP="symbols",
           TARGET=TARGET, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
           GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="credential.helper", GIT_CONFIG_VALUE_0="",
           NPM_CONFIG_USERCONFIG=str(empty_npm), NPM_CONFIG_GLOBALCONFIG=str(empty_npm_global))
signal.signal(signal.SIGALRM, deadline)
signal.alarm(55 * 60)
success = False
binary = ROOT / "codex-rs/target" / TARGET / "release/codex"
try:
    actual = {"provider": "circleci", "machine": platform.machine(),
              "macos": run(["sw_vers", "-productVersion"], capture=True).strip(),
              "xcode": run(["xcodebuild", "-version"], capture=True).strip(),
              "resourceClass": "m4pro.medium", "logicalCpus": int(run(["sysctl", "-n", "hw.logicalcpu"], capture=True).strip()),
              "memoryBytes": int(run(["sysctl", "-n", "hw.memsize"], capture=True).strip()), "workflowId": ENV["CIRCLE_WORKFLOW_ID"],
              "jobId": ENV["CIRCLE_WORKFLOW_JOB_ID"], "jobNumber": int(ENV["CIRCLE_BUILD_NUM"]),
              "pipelineId": ENV["MAGNET_CI_PIPELINE_ID"], "commit": ENV["CIRCLE_SHA1"],
              "nativeDeadlineSeconds": 55 * 60, "platformFreeJobLimitSeconds": 60 * 60}
    assert actual["logicalCpus"] == 6 and actual["memoryBytes"] >= 26 * 1024 ** 3
    assert actual["xcode"].splitlines() == ["Xcode 26.5", "Build version 17F42"]
    (DIAGNOSTICS / "circle-host.json").write_text(json.dumps(actual, indent=2) + "\n")
    spec = importlib.util.spec_from_file_location("circle_prepare", CONTROL / "prepare_native_circleci.py")
    module = importlib.util.module_from_spec(spec)
    # The preparation helper reads only these owned compatibility paths.
    os.environ["GITHUB_WORKSPACE"] = str(ROOT)
    spec.loader.exec_module(module)
    run([sys.executable, str(CONTROL / "prepare_native_circleci.py"), "prepare", "darwin-arm64"])
    apply_owned_env(owned_env)
    bootstrap = json.loads((CONTROL / "rustup-bootstrap-circleci.json").read_text())
    installer = ROOT / ".rustup-init"
    module.download(bootstrap["url"], installer, expected_sha=bootstrap["sha256"], limit=30_000_000)
    installer.chmod(0o700)
    run([str(installer), "-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "none"])
    ENV["PATH"] = str(Path(ENV["CARGO_HOME"]) / "bin") + os.pathsep + ENV["PATH"]
    run(["rustup", "toolchain", "install", "1.95.0", "--profile", "minimal"])
    run(["rustup", "target", "add", "--toolchain", "1.95.0", TARGET])
    compiler = run(["rustc", "--version"], capture=True).strip()
    assert compiler.startswith("rustc 1.95.0 ")
    version = run([sys.executable, str(ROOT / ".github/scripts/rusty_v8_bazel.py"), "resolved-v8-crate-version"], capture=True).strip()
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
    profile = "ptrcomp_sandbox_release"
    archive_name = "librusty_v8_" + profile + "_" + TARGET + ".a.gz"
    binding_name = "src_binding_" + profile + "_" + TARGET + ".rs"
    sums_name = "rusty_v8_" + profile + "_" + TARGET + ".sha256"
    trusted = ROOT / ("third_party/v8/rusty_v8_" + version.replace(".", "_") + "_release_manifests.sha256")
    manifests = {line.split()[1]: line.split()[0] for line in trusted.read_text().splitlines() if line.strip()}
    folder = ROOT / ".rusty-v8"
    folder.mkdir()
    base = "https://github.com/openai/codex/releases/download/rusty-v8-v" + version + "/"
    module.download(base + sums_name, folder / sums_name, expected_sha=manifests[sums_name], limit=10000)
    sum_lines = (folder / sums_name).read_text().splitlines()
    assert len(sum_lines) == 2
    hashes = {line.split()[1]: line.split()[0] for line in sum_lines}
    assert set(hashes) == {archive_name, binding_name}
    for name in (archive_name, binding_name):
        module.download(base + name, folder / name, expected_sha=hashes[name])
    ENV.update(RUSTY_V8_ARCHIVE=str(folder / archive_name), RUSTY_V8_SRC_BINDING_PATH=str(folder / binding_name))
    run(["cargo", "build", "--locked", "--release", "--target", TARGET, "--bin", "codex", "--package", "codex-cli"], cwd=ROOT / "codex-rs")
    shutil.copy2(binary, DIAGNOSTICS / "codex-NOT-FOR-RELEASE")
    actual.update(nativeSha256=digest(binary), compiler=compiler,
                  source=module.PLAN["source"], patchSha256=module.PLAN["patchSha256"],
                  normalizedLockSha256=digest(ROOT / "codex-rs/Cargo.lock"))
    (DIAGNOSTICS / "circle-native.json").write_text(json.dumps(actual, indent=2) + "\n")
    original_min = macos_minimum(ROOT / ".upstream-platform/package/vendor" / TARGET / "bin/codex")
    managed_min = macos_minimum(binary)
    assert (managed_min + (0, 0, 0))[:3] <= (original_min + (0, 0, 0))[:3]
    (DIAGNOSTICS / "macos-minimum.json").write_text(json.dumps({"upstreamMinimum": original_min, "managedMinimum": managed_min, "minimumNotIncreased": True}) + "\n")
    run([sys.executable, str(CONTROL / "native_smoke.py"), "darwin-arm64"])
    smoke = json.loads((ROOT / "native-smoke.json").read_text())
    assert len(smoke["records"]) == 17 and all(row["passed"] is True for row in smoke["records"])
    run([sys.executable, str(CONTROL / "prepare_native_circleci.py"), "pack", "darwin-arm64"])
    artifacts = ROOT / "managed-artifacts"
    entries = list(artifacts.iterdir())
    assert len(entries) == 3 and all(path.is_file() for path in entries)
    archive = artifacts / "coderx-01672-darwin-arm64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        for path in entries:
            output.write(path, arcname=path.name)
    (artifacts / "circle-result.json").write_text(json.dumps({**actual, "archive": archive.name, "archiveSha256": digest(archive), "nativeSmokeChecks": 17, "minimumNotIncreased": True, "allPassed": True}, indent=2) + "\n")
    (DIAGNOSTICS / "codex-NOT-FOR-RELEASE").unlink()
    success = True
finally:
    signal.alarm(0)
    (DIAGNOSTICS / "circle-completion.json").write_text(json.dumps({"success": success, "elapsedSeconds": round(55 * 60 - max(0, DEADLINE - time.monotonic())), "qualification": "actual native bytes require successful provider job/workflow, pinned source/helpers/legal and independent verification before publication"}, indent=2) + "\n")
