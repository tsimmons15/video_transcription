from pathlib import Path
from .config import SUPPORTED_EXTENSIONS

def validate_directory(path: str, create_if_not_exists: bool) -> Path:
    p = Path(path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    elif not p.is_dir():
        raise ValueError(f"Invalid directory: {path}")
    return p


def discover_media_files(directory: Path):
    return [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

def get_media_file(directory: Path, filename: str):
    print(f"Get media files for {directory}\{filename}")
    #for f in directory.iterdir():
    #    print(f"File: '{f.name}'\nComparison filename: '{filename}'\nIs file? {f.is_file()}\nFile: {f}\nEquality? {f.name.lower() == filename.lower()}")
    return [
        f for f in directory.iterdir()
        if f.is_file() and f.name.lower() == filename.lower()
    ]