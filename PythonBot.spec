# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


root = Path(SPECPATH)
rapidocr_data = collect_data_files(
    "rapidocr",
    includes=["config.yaml", "default_models.yaml", "models/*.onnx", "models/*.txt"],
)
onnxruntime_binaries = collect_dynamic_libs("onnxruntime")
application_data = []
reference = root / "assets" / "user_spellbar_reference.png"
if reference.exists():
    application_data.append((str(reference), "assets"))
fonts = root / "assets" / "fonts"
for font_file in sorted(fonts.glob("*.ttf")) + sorted(fonts.glob("OFL-*.txt")):
    application_data.append((str(font_file), "assets/fonts"))

icon_path = root / "assets" / "pythonbot.ico"
icon = str(icon_path) if icon_path.exists() else None

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=onnxruntime_binaries,
    datas=rapidocr_data + application_data,
    hiddenimports=[
        "rapidocr.main",
        "rapidocr.inference_engine.onnxruntime",
        "rapidocr.inference_engine.onnxruntime.main",
        "rapidocr.inference_engine.onnxruntime.provider_config",
        "onnxruntime.capi._pybind_state",
        "onnxruntime.capi.onnxruntime_pybind11_state",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "torchvision", "paddle", "openvino", "tensorrt", "MNN"],
    noarchive=False,
    optimize=1,
)

# Le PATH de l'hôte de build peut contenir une autre version du runtime MSVC.
# PySide6 fournit le runtime exact attendu par ses DLL Qt : on l'impose à la racine ONEDIR.
pyside_dir = Path(sys.prefix) / "Lib" / "site-packages" / "PySide6"
runtime_names = {
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140.dll", "vcruntime140_1.dll",
}
host_dlls_to_exclude = runtime_names | {"icuuc.dll", "icudt78.dll"}
# Les DLL d'un runtime « codex » trouvé via le PATH sont étrangères au build, sauf si
# ce runtime est l'interpréteur de base lui-même (cas de .venv\validation) : python312.dll,
# python3.dll et les modules DLLs\*.pyd doivent alors être embarqués.
build_base = Path(sys.base_prefix).resolve()


def _foreign_host_binary(source: str) -> bool:
    if "codex-runtimes" not in source.lower():
        return False
    try:
        return not Path(source).resolve().is_relative_to(build_base)
    except (OSError, ValueError):
        return True


a.binaries = [
    entry for entry in a.binaries
    if entry[0].lower() not in host_dlls_to_exclude
    and not _foreign_host_binary(str(entry[1]))
]
for runtime_name in sorted(runtime_names):
    runtime_path = pyside_dir / runtime_name
    if runtime_path.exists():
        a.binaries.append((runtime_name, str(runtime_path), "BINARY"))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PythonBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PythonBot",
)
