from dataclasses import dataclass

from app.services.change_detector import (
    ChangeResult,
    detect_changes,
)
from app.services.content_extractor import (
    ExtractedContent,
    extract_content,
)
from app.services.snapshot_service import (
    load_snapshot,
    save_snapshot,
)
from app.services.web_fetcher import (
    FetchResult,
    fetch_website,
)


@dataclass
class MonitorResult:
    monitor_key: str
    url: str
    first_run: bool
    fetch_result: FetchResult
    content: ExtractedContent
    change_result: ChangeResult | None


async def run_monitor(
    monitor_key: str,
    url: str,
) -> MonitorResult:
    """
    Bir web sitesinin monitoring işlemini gerçekleştirir.

    Akış:
    1. Website'i indir.
    2. İçeriği temizle.
    3. Önceki snapshot'ı yükle.
    4. İlk çalışmaysa snapshot oluştur.
    5. Önceki snapshot varsa değişiklikleri karşılaştır.
    6. Güncel snapshot'ı kaydet.
    """

    fetch_result = await fetch_website(url)

    content = extract_content(
        fetch_result.html,
    )

    previous_content = load_snapshot(
        monitor_key,
    )

    # İlk kontrol
    if previous_content is None:
        save_snapshot(
            monitor_key,
            content.text,
        )

        return MonitorResult(
            monitor_key=monitor_key,
            url=url,
            first_run=True,
            fetch_result=fetch_result,
            content=content,
            change_result=None,
        )

    # Önceki ve yeni içerikleri karşılaştır
    change_result = detect_changes(
        old_text=previous_content,
        new_text=content.text,
    )

    # Yeni içerik artık güncel snapshot olur
    save_snapshot(
        monitor_key,
        content.text,
    )

    return MonitorResult(
        monitor_key=monitor_key,
        url=url,
        first_run=False,
        fetch_result=fetch_result,
        content=content,
        change_result=change_result,
    )