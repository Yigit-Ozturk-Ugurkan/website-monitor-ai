from pathlib import Path


SNAPSHOT_DIRECTORY = Path("data") / "snapshots"


def _ensure_snapshot_directory() -> None:
    """
    Snapshot klasörü yoksa oluşturur.
    """

    SNAPSHOT_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )


def _get_snapshot_path(monitor_key: str) -> Path:
    """
    Monitor anahtarına göre snapshot dosya yolunu oluşturur.
    """

    if not monitor_key:
        raise ValueError("monitor_key boş olamaz.")

    safe_key = "".join(
        character
        for character in monitor_key
        if character.isalnum() or character in ("-", "_")
    )

    if not safe_key:
        raise ValueError("Geçerli bir monitor_key belirtilmelidir.")

    return SNAPSHOT_DIRECTORY / f"{safe_key}.txt"


def snapshot_exists(monitor_key: str) -> bool:
    """
    İlgili monitor için daha önce snapshot alınmış mı kontrol eder.
    """

    _ensure_snapshot_directory()

    snapshot_path = _get_snapshot_path(monitor_key)

    return snapshot_path.exists()


def load_snapshot(monitor_key: str) -> str | None:
    """
    Daha önce kaydedilmiş snapshot içeriğini döndürür.
    """

    _ensure_snapshot_directory()

    snapshot_path = _get_snapshot_path(monitor_key)

    if not snapshot_path.exists():
        return None

    return snapshot_path.read_text(
        encoding="utf-8",
    )


def save_snapshot(
    monitor_key: str,
    content: str,
) -> Path:
    """
    Yeni snapshot içeriğini dosyaya kaydeder.
    """

    _ensure_snapshot_directory()

    snapshot_path = _get_snapshot_path(monitor_key)

    snapshot_path.write_text(
        content,
        encoding="utf-8",
    )

    return snapshot_path