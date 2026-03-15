"""WSL and Windows path conversion utilities.

All internal logic uses pathlib.Path (POSIX). Windows path conversion is a
leaf operation: it only happens when writing .adg XML for Ableton consumption.

Design constraints:
- Pure string manipulation -- no subprocess calls (no wslpath dependency).
- Returns None gracefully when the path is not Windows-accessible.
- Fully deterministic and testable.
"""

from __future__ import annotations

from pathlib import Path


_WSL_MOUNT_ROOT = "/mnt"
_DRIVE_LETTER_LEN = 1


def to_windows_path(posix_path: Path) -> str | None:
    """Convert a WSL /mnt/<drive>/... path to a Windows drive path string.

    Returns a Windows-style path (e.g. ``C:\\Users\\me\\Music``),
    or None if the path is not under /mnt/ with a single-letter drive component.

    This path is embedded in .adg XML so that Ableton Live (on Windows) can
    locate the audio files without knowing about WSL mount points.

    Example:
        >>> to_windows_path(Path("/mnt/c/Users/me/Music"))
        'C:\\\\Users\\\\me\\\\Music'
        >>> to_windows_path(Path("/home/me/dev/project"))
        >>> to_windows_path(Path("/mnt/c"))
        'C:\\\\'
    """
    parts = posix_path.parts
    # Expect: ('/', 'mnt', '<drive_letter>', ...)
    if (
        len(parts) >= 3
        and parts[0] == "/"
        and parts[1] == "mnt"
        and len(parts[2]) == _DRIVE_LETTER_LEN
        and parts[2].isalpha()
    ):
        drive = parts[2].upper()
        rest = "\\".join(parts[3:])
        return f"{drive}:\\{rest}"
    return None


def is_windows_accessible(path: Path) -> bool:
    """Return True when the path is under a WSL Windows mount point (/mnt/<letter>/).

    Example:
        >>> is_windows_accessible(Path("/mnt/c/Users/me"))
        True
        >>> is_windows_accessible(Path("/home/me/dev"))
        False
    """
    return to_windows_path(path) is not None


def windows_path_to_posix(win_path: str) -> Path | None:
    """Convert a Windows absolute path to its WSL /mnt/<drive>/... equivalent.

    Handles backslash and forward-slash separators.
    Returns None if ``win_path`` does not look like a Windows drive path.

    Example:
        >>> windows_path_to_posix("C:\\\\Users\\\\me\\\\Music\\\\foo.wav")
        PosixPath('/mnt/c/Users/me/Music/foo.wav')
        >>> windows_path_to_posix("C:/Users/me/Music/foo.wav")
        PosixPath('/mnt/c/Users/me/Music/foo.wav')
    """
    if len(win_path) < 3 or win_path[1] != ":" or win_path[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
        return None
    drive = win_path[0].lower()
    rest = win_path[2:].replace("\\", "/")
    return Path(f"/mnt/{drive}{rest}")


def resolve_output_dir(raw: str, cwd: Path | None = None) -> Path:
    """Resolve an output directory string to an absolute Path.

    Relative paths are resolved against cwd (defaults to Path.cwd()).
    Tildes are expanded.

    Example:
        >>> resolve_output_dir("/mnt/c/Users/me/Music/Ableton/GuidePacks")
        PosixPath('/mnt/c/Users/me/Music/Ableton/GuidePacks')
    """
    base = cwd or Path.cwd()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def resolve_path(raw: str, cwd: Path | None = None) -> Path:
    """Resolve any path string to an absolute Path (general-purpose helper)."""
    return resolve_output_dir(raw, cwd)
