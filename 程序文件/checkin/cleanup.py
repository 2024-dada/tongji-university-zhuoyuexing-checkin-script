"""Safely remove locally stored image files without following directory links."""
from pathlib import Path
import re


def clear_record_photos(root, row_id):
    """Remove only a completed record's own cache; never follow links."""
    if not re.fullmatch(r'[0-9a-f]{32}', str(row_id)):
        raise ValueError('记录标识无效，未删除配图。')
    photos = validate_photo_directory(root)
    folder = photos / row_id
    if not folder.exists() and not folder.is_symlink():
        return
    if folder.is_symlink() or folder.is_junction():
        raise ValueError('记录照片目录包含链接，未删除配图。')

    def remove(path):
        if path.is_symlink():
            path.unlink()
        elif path.is_junction():
            path.rmdir()
        elif path.is_dir():
            for child in list(path.iterdir()):
                remove(child)
            path.rmdir()
        else:
            path.unlink()

    remove(folder)


def validate_photo_directory(root):
    folder = Path(root).resolve(strict=True) / '照片'
    if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
        raise ValueError('照片目录是链接，为避免删除其他位置文件，已停止清理。')
    if folder.exists() and not folder.is_dir():
        raise ValueError('照片路径不是文件夹，已停止清理。')
    return folder


def clear_photo_directory(root):
    folder = validate_photo_directory(root)
    if not folder.exists():
        return 0, []
    removed, errors = 0, []

    def remove(path):
        nonlocal removed
        if path.is_symlink():
            path.unlink()
            removed += 1
        elif hasattr(path, 'is_junction') and path.is_junction():
            path.rmdir()
            removed += 1
        elif path.is_dir():
            for child in list(path.iterdir()):
                remove(child)
            path.rmdir()
            removed += 1
        else:
            path.unlink()
            removed += 1

    for item in list(folder.iterdir()):
        try:
            remove(item)
        except OSError:
            errors.append(str(item))
    return removed, errors

