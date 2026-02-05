from pathlib import Path
from .config import SUPPORTED_MEDIA_EXTENSIONS, SUPPORTED_TEXT_EXTENSIONS, MEDIA_TYPE, TEXT_TYPE

def validate_directory(path, create_if_not_exists = False):
    p = Path(path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
    elif not p.is_dir():
        raise ValueError(f"Invalid directory: {path}")
    return p


def discover_media_files(directory, type = MEDIA_TYPE):
    extensions = SUPPORTED_MEDIA_EXTENSIONS if type == MEDIA_TYPE else SUPPORTED_TEXT_EXTENSIONS
    return [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in extensions
    ]

def get_media_file(directory, filename):
    #for f in directory.iterdir():
    #    print(f"File: '{f.name}'\nComparison filename: '{filename}'\nIs file? {f.is_file()}\nFile: {f}\nEquality? {f.name.lower() == filename.lower()}")
    return [
        f for f in directory.iterdir()
        if f.is_file() and f.name.lower() == filename.lower()
    ]