from __future__ import annotations

from pathlib import Path
from typing import Protocol

from media_dl.download_plan import METADATA_MODE_NAVIDROME_CLEAN, MetadataPolicy


SUPPORTED_METADATA_SUFFIXES = {".flac", ".m4a", ".mp3", ".ogg", ".opus"}


class EasyAudio(Protocol):
    def get(self, key: str, default=None): ...
    def __setitem__(self, key: str, value) -> None: ...
    def __delitem__(self, key: str) -> None: ...
    def save(self) -> None: ...


def clean_new_audio_metadata(
    paths: list[Path],
    policy: MetadataPolicy,
) -> str | None:
    if policy.mode != METADATA_MODE_NAVIDROME_CLEAN:
        return None

    failures: list[str] = []
    for path in paths:
        try:
            clean_audio_metadata(path, policy)
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")

    if failures:
        return f"metadata cleanup failed for {len(failures)} file(s): {failures[0]}"
    return None


def clean_audio_metadata(path: Path, policy: MetadataPolicy) -> None:
    audio = _open_audio(path)
    changed = False

    if not _values(audio, "title"):
        audio["title"] = [path.stem]
        changed = True

    artists = _values(audio, "artist")
    if not artists:
        artists = ["Unknown Artist"]
        audio["artist"] = artists
        changed = True

    if policy.album_artist_fallback == "copy_artist" and not _values(audio, "albumartist"):
        audio["albumartist"] = artists
        changed = True

    if not _values(audio, "album"):
        changed = _delete_if_present(audio, "album") or changed

    if changed:
        audio.save()


def _open_audio(path: Path) -> EasyAudio:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return _open_mp3(path)
    if suffix == ".m4a":
        from mutagen.easymp4 import EasyMP4

        return EasyMP4(path)
    if suffix == ".flac":
        from mutagen.flac import FLAC

        return FLAC(path)
    if suffix == ".opus":
        from mutagen.oggopus import OggOpus

        return OggOpus(path)
    if suffix == ".ogg":
        from mutagen.oggvorbis import OggVorbis

        return OggVorbis(path)
    if suffix == ".wav":
        raise ValueError("wav metadata cleanup is not supported")
    raise ValueError(f"unsupported audio format for metadata cleanup: {suffix}")


def _open_mp3(path: Path) -> EasyAudio:
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3, ID3NoHeaderError

    try:
        return EasyID3(path)
    except ID3NoHeaderError:
        ID3().save(path)
        return EasyID3(path)


def _values(audio: EasyAudio, key: str) -> list[str]:
    raw = audio.get(key, [])
    if raw is None:
        return []
    if isinstance(raw, str):
        values = [raw]
    else:
        values = list(raw)
    return [value for value in values if isinstance(value, str) and value.strip()]


def _delete_if_present(audio: EasyAudio, key: str) -> bool:
    try:
        del audio[key]  # type: ignore[index]
    except KeyError:
        return False
    return True
