import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ["livemap", "demo", "LICENSE", "THIRD_PARTY.md", "README.md", "requirements.txt", "livemap.example.toml",
           "livemap.cmd", "livemap_tunnel.cmd", "livemap_demo.cmd"]
SKIP = ("__pycache__", ".pyc")
PURE = ("mpyq",)


def fetch(url, path):
    if not os.path.exists(path):
        print("download", url, flush=True)
        urllib.request.urlretrieve(url, path)
    return path


def main():
    ap = argparse.ArgumentParser(description="build the portable Windows zip (embedded Python, no install)")
    ap.add_argument("--python", default="3.12.10", help="embeddable Python version (needs a Windows binary release)")
    ap.add_argument("--version", default="dev", help="release name used in the zip file name")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()

    work = os.path.join(args.out, "build")
    cache = os.path.join(args.out, "cache")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(cache, exist_ok=True)
    name = f"spp-livemap-{args.version}-win64"
    stage = os.path.join(work, name)
    runtime = os.path.join(stage, "runtime")
    os.makedirs(runtime)

    embed = fetch(f"https://www.python.org/ftp/python/{args.python}/python-{args.python}-embed-amd64.zip",
                  os.path.join(cache, f"python-{args.python}-embed-amd64.zip"))
    with zipfile.ZipFile(embed) as z:
        z.extractall(runtime)
    tag = "".join(args.python.split(".")[:2])
    with open(os.path.join(runtime, f"python{tag}._pth"), "w", newline="\r\n") as fh:
        fh.write(f"python{tag}.zip\n.\nLib\\site-packages\n..\n")

    wheels = os.path.join(cache, f"wheels-cp{tag}")
    os.makedirs(wheels, exist_ok=True)
    with open(os.path.join(ROOT, "requirements.txt")) as fh:
        reqs = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    pure = [r for r in reqs if r.lower().startswith(PURE)]
    binary = [r for r in reqs if r not in pure]
    if pure:
        subprocess.check_call([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", wheels] + pure)
    subprocess.check_call([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--platform", "win_amd64",
                           "--python-version", tag, "--implementation", "cp", "-d", wheels] + binary)
    site = os.path.join(runtime, "Lib", "site-packages")
    os.makedirs(site)
    for whl in sorted(os.listdir(wheels)):
        if whl.endswith(".whl"):
            with zipfile.ZipFile(os.path.join(wheels, whl)) as z:
                z.extractall(site)

    for item in INCLUDE:
        src = os.path.join(ROOT, item)
        if not os.path.exists(src):
            continue
        dst = os.path.join(stage, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP))
        else:
            shutil.copy2(src, dst)
    with open(os.path.join(stage, "PYTHON_VERSION.txt"), "w") as fh:
        fh.write(f"Embedded Python {args.python} (python.org embeddable package, amd64)\n")

    zip_path = os.path.join(args.out, name + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d, _, files in os.walk(stage):
            for f in files:
                full = os.path.join(d, f)
                z.write(full, os.path.relpath(full, work))
    print(f"{zip_path} ({os.path.getsize(zip_path) / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
