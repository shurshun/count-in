# Build: uv run pyinstaller packaging/countin.spec --noconfirm
from pathlib import Path

root = Path(SPECPATH).parent
datas = [(str(root / "models" / "final0.ckpt"), "models"),
         (str(root / "models" / "separator"), "models/separator")]
if (root / "samples").exists():
    datas.append((str(root / "samples"), "samples"))
if (root / "bin").exists():
    datas.append((str(root / "bin"), "bin"))

a = Analysis([str(root / "packaging" / "countin_cli.py")], pathex=[str(root / "src")], datas=datas,
             excludes=["PySide6", "tkinter", "matplotlib", "IPython"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="countin", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="countin")
