# Build: uv run pyinstaller packaging/countin-app.spec --noconfirm --distpath build/dist --workpath build/work
import sys
from pathlib import Path

root = Path(SPECPATH).parent
datas = [(str(root / "models" / "final0.ckpt"), "models"),
         (str(root / "models" / "separator"), "models/separator")]
for extra in ("samples", "bin"):
    if (root / extra).exists():
        datas.append((str(root / extra), extra))

a = Analysis([str(root / "packaging" / "countin_app.py")], pathex=[str(root / "src")], datas=datas,
             excludes=["tkinter", "matplotlib", "IPython"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Count-In", console=False,
          codesign_identity=None, entitlements_file=None)  # signing: set both for notarized builds
coll = COLLECT(exe, a.binaries, a.datas, name="Count-In")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Count-In.app", bundle_identifier="local.countin",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.1.0",
                             "CFBundleDocumentTypes": [{"CFBundleTypeName": "Audio", "CFBundleTypeRole": "Viewer",
                                                        "LSItemContentTypes": ["public.audio"]}]})
