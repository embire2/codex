"""Build one real Windows x64 runtime on a dedicated bounded Free CircleCI VM."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile

CONTROL = Path(__file__).resolve().parent
ROOT = Path.cwd().resolve()
PLATFORM = "win32-x64"
TARGET = "x86_64-pc-windows-msvc"
DEADLINE = time.monotonic() + 54 * 60
assert sys.version_info >= (3,12) and sys.platform == "win32" and platform.machine().lower() in {"amd64","x86_64"}
assert os.environ["CIRCLE_BRANCH"] == "magnetapi/coderx-01710-circle-win-20261120"
assert os.environ["CIRCLE_SHA1"] == os.environ["MAGNET_REVIEWED_REVISION"]
assert int(os.environ["MAGNET_FREE_CREDIT_RESERVE"]) >= 5000
assert int(os.environ["MAGNET_ARTIFACT_RETENTION_DAYS"]) == 30

PUBLIC = {"PATH", "Path", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA", "APPDATA", "PROCESSOR_ARCHITECTURE", "CIRCLE_SHA1",
          "CIRCLE_BRANCH","CIRCLE_WORKFLOW_ID","CIRCLE_WORKFLOW_JOB_ID","CIRCLE_BUILD_NUM","MAGNET_CI_PIPELINE_ID"}
PUBLIC = {key.upper() for key in PUBLIC}
ENV = {k:v for k,v in os.environ.items() if k.upper() in PUBLIC}
assert platform.version().split(".")[2].isdigit() and int(platform.version().split(".")[2]) >= 17763
HOME = ROOT / ".build-home"
TMP = ROOT / ".build-temp"
for path in (HOME, TMP):
    path.mkdir(exist_ok=False,mode=0o700)
ENV_FILE = ROOT / ".build-env"
PATH_FILE = ROOT / ".build-path"
for path in (ENV_FILE, PATH_FILE, HOME / ".npmrc"):
    path.touch(exist_ok=False,mode=0o600)
ENV.update(HOME=str(HOME),USERPROFILE=str(HOME),RUNNER_TEMP=str(TMP),TMPDIR=str(TMP),
    GITHUB_WORKSPACE=str(ROOT),GITHUB_ENV=str(ENV_FILE),GITHUB_PATH=str(PATH_FILE),TEMP=str(TMP),TMP=str(TMP),
    CARGO_HOME=str(Path(ROOT.anchor) / "mc"),RUSTUP_HOME=str(HOME/".rustup"),RUSTUP_TOOLCHAIN="1.99.1",
    CARGO_BUILD_JOBS="3",CARGO_NET_GIT_FETCH_WITH_CLI="true",TARGET=TARGET,RUSTUP_INIT_SKIP_PATH_CHECK="yes",
    CARGO_PROFILE_RELEASE_LTO="false",CARGO_PROFILE_RELEASE_CODEGEN_UNITS="16",
    CARGO_PROFILE_RELEASE_OPT_LEVEL="1",CARGO_PROFILE_RELEASE_DEBUG="0",CARGO_PROFILE_RELEASE_STRIP="symbols",
    AWS_LC_SYS_NO_JITTER_ENTROPY="1",GIT_TERMINAL_PROMPT="0",GIT_CONFIG_NOSYSTEM="1",
    GIT_CONFIG_COUNT="1",GIT_CONFIG_KEY_0="core.longpaths",GIT_CONFIG_VALUE_0="true",
    NPM_CONFIG_USERCONFIG=str(HOME/".npmrc"),NPM_CONFIG_GLOBALCONFIG=str(HOME/".npmrc"))
assert len(ENV["CARGO_HOME"])<=5
Path(ENV["CARGO_HOME"]).mkdir(exist_ok=False)

def run(args, cwd=ROOT, capture=False):
    wait = DEADLINE - time.monotonic()
    assert wait > 0
    executable = shutil.which(args[0], path=ENV["PATH"])
    assert executable, args[0]
    return subprocess.run([executable, *args[1:]], cwd=cwd, env=ENV, stdin=subprocess.DEVNULL, check=True,
                          timeout=wait, text=True, stdout=subprocess.PIPE if capture else None).stdout

def apply_env():
    allowed = {"CODEX_BWRAP_SHA256","STABLE_GIT_COMMIT","INCLUDE","LIB","LIBPATH","PATH",
               "UCRTVersion","UniversalCRTSdkDir","VCINSTALLDIR","VCToolsInstallDir","WindowsLibPath",
               "WindowsSdkBinPath","WindowsSdkDir","WindowsSDKLibVersion","WindowsSDKVersion",
               "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER","CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS"}
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        name,value=line.split("=",1)
        assert name in allowed, name
        ENV[name]=value

build_spec = importlib.util.spec_from_file_location("managed_prepare", CONTROL / "prepare_native.py")
build = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(build)
run([sys.executable,str(CONTROL/"prepare_native.py"),"prepare",PLATFORM])
installer = ROOT / "rustup-init.exe"
bootstrap = json.loads((CONTROL/"rustup-bootstrap-compute.json").read_text())[PLATFORM]
build.download(bootstrap["url"], installer, expected_sha=bootstrap["sha256"], limit=35_000_000)
installer.chmod(0o700)
run([str(installer),"-y","--no-modify-path","--profile","minimal","--default-toolchain","none"])
ENV["PATH"]=str(Path(ENV["CARGO_HOME"])/"bin")+os.pathsep+(ENV.get("PATH") or ENV.pop("Path"))
run(["rustup","toolchain","install","1.99.1","--profile","minimal"])
run(["rustup","target","add","--toolchain","1.99.1",TARGET])
compiler=run(["rustc","--version"],capture=True).strip()
assert compiler.startswith("rustc 1.99.1 ")
run(["pwsh.exe","-NoProfile","-File",str(ROOT/".github/actions/setup-msvc-env/setup-msvc-env.ps1"),"-Target",TARGET,"-HostArch","x64"])
apply_env()
version=run([sys.executable,".github/scripts/rusty_v8_bazel.py","resolved-v8-crate-version"],capture=True).strip()
assert version=="152.2.0"
profile="ptrcomp_sandbox_release"
archive="rusty_v8_"+profile+"_"+TARGET+".lib.gz"
binding="src_binding_"+profile+"_"+TARGET+".rs"
sums="rusty_v8_"+profile+"_"+TARGET+".sha256"
home=ROOT/".rusty-v8"
home.mkdir()
repo="https://github.com/openai/codex/releases/download/rusty-v8-v"+version+"/"
checks={parts[1]:parts[0] for line in
        (ROOT/"third_party/v8/rusty_v8_152_2_0_release_manifests.sha256").read_text().splitlines()
        if len(parts:=line.split())==2}
build.download(repo+sums,home/sums,expected_sha=checks[sums],limit=10_000)
checks={p[1]:p[0] for line in (home/sums).read_text().splitlines() if len(p:=line.split())==2}
assert set(checks)=={archive,binding}
for name in (archive,binding):
    build.download(repo+name,home/name,expected_sha=checks[name])
ENV.update(RUSTY_V8_ARCHIVE=str(home/archive),RUSTY_V8_SRC_BINDING_PATH=str(home/binding))
run(["cargo","build","--locked","--release","--target",TARGET,"--bin","codex","--package","codex-cli"],cwd=ROOT/"codex-rs")
run([sys.executable,str(CONTROL/"native_smoke.py"),PLATFORM])
run([sys.executable,str(CONTROL/"prepare_native.py"),"pack",PLATFORM])
artifacts=ROOT/"managed-artifacts"
members=sorted(artifacts.iterdir())
assert len(members)==3
with zipfile.ZipFile(artifacts/f"coderx-01710-{PLATFORM}.zip","x",compression=zipfile.ZIP_STORED) as bundle:
    for part in members:
        bundle.write(part,part.name)
print(json.dumps({"platform":PLATFORM,"job":ENV["CIRCLE_WORKFLOW_JOB_ID"],"allPassed":True}))
