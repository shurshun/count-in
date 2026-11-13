# Build: uv run pyinstaller packaging/countin.spec --noconfirm --distpath build/dist --workpath build/work
# One bundle, two executables sharing its libraries: the app (Count-In) and the CLI (countin).
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]

datas = [(str(root / "models" / "final0.ckpt"), "models"),
         (str(root / "models" / "separator"), "models/separator")]
for extra in ("samples", "bin"):
    if (root / extra).exists():
        datas.append((str(root / extra), extra))

# Package metadata carries the version the update check compares against.
datas += copy_metadata("countin")

a = Analysis([str(root / "packaging" / f) for f in ("countin_app.py", "countin_cli.py")],
             pathex=[str(root / "src")], datas=datas, excludes=["tkinter", "matplotlib", "IPython"])
pyz = PYZ(a.pure)


def scripts_for(main: str):
    """Runtime hooks plus one entry script; a.scripts holds both entry points."""
    other = {"countin_app", "countin_cli"} - {main}
    return [s for s in a.scripts if s[0] not in other]


# Signing: set codesign_identity and entitlements_file for notarized builds.
app_exe = EXE(pyz, scripts_for("countin_app"), [], exclude_binaries=True, name="Count-In", console=False)
cli_exe = EXE(pyz, scripts_for("countin_cli"), [], exclude_binaries=True, name="countin", console=True)
coll = COLLECT(app_exe, cli_exe, a.binaries, a.datas, name="Count-In")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Count-In.app", bundle_identifier="local.countin",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": version,
                             "CFBundleDocumentTypes": [{"CFBundleTypeName": "Audio", "CFBundleTypeRole": "Viewer",
                                                        "LSItemContentTypes": ["public.audio"]}]})
