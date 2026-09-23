from datetime import datetime

from pydantic import BaseModel

from app.schemas.regulation_comparison import (
    RegulationComparisonResponse,
)


class NotificationResponse(BaseModel):
    """
    Bildirim listesi cevabi.

    Tum alanlar notifications tablosunda gercekten bulunan
    kolonlardan gelir. monitor_name, monitors tablosundan
    join ile alinir.
    """

    id: int

    monitor_id: int
    monitor_name: str

    change_id: int
    ai_analysis_id: int

    notification_type: str

    title: str
    message: str

    importance: str
    category: str

    status: str
    is_read: bool

    created_at: datetime


class UnreadCountResponse(BaseModel):
    """
    Okunmamis bildirim sayisi.
    """

    count: int


class NotificationReadResponse(BaseModel):
    """
    Okundu isaretleme sonucu.

    Yalnizca is_read degistigi icin minimal tutulmustur.
    """

    id: int
    is_read: bool


class ContextLineResponse(BaseModel):
    """
    Snapshot icindeki tek bir satir.

    changed=True ise bu satir degisiklige dahildir ve
    frontend tarafinda highlight edilir.
    """

    line_number: int
    text: str
    changed: bool
    evidence: bool = False


class ContextBlockResponse(BaseModel):
    """
    Degisen satirlarin cevresindeki context blogu.
    """

    start_line: int
    end_line: int
    lines: list[ContextLineResponse]


class NotificationDetailResponse(BaseModel):
    """
    Degisiklik detayi ekrani icin tek istekte gereken veri.

    Tum alanlar mevcut notifications / monitors / changes /
    snapshots / ai_analyses kayitlarindan gelir.
    Context bloklari bu kayitlardan deterministik olarak
    hesaplanir, yeni AI cagrisi yapilmaz.
    """

    notification_id: int

    monitor_id: int
    monitor_name: str
    url: str

    change_id: int
    ai_analysis_id: int

    title: str
    message: str
    importance: str
    category: str
    created_at: datetime
    is_read: bool

    ai_summary: str | None = None

    # Gemini kararinin dayandigi kaynak metin.
    # Eski kayitlarda null olabilir.
    evidence_text: str | None = None
    evidence_type: str | None = None

    # changes tablosundan
    added_content: str | None = None
    removed_content: str | None = None
    relevance_score: float | None = None
    detected_at: datetime | None = None

    # snapshots tablosundan
    old_snapshot_id: int | None = None
    new_snapshot_id: int | None = None
    old_snapshot_content: str | None = None
    new_snapshot_content: str | None = None
    snapshot_title: str | None = None

    # Deterministik olarak hesaplanan context bloklari
    added_blocks: list[ContextBlockResponse] = []
    removed_blocks: list[ContextBlockResponse] = []

    # MEVZUAT KARSILASTIRMASI
    #
    # Yalnizca regulation_monitor kayitlarinda ve yalnizca
    # bu degisiklik icin bir karsilastirma yapildiysa dolu
    # gelir. Istemcinin ikinci bir istek yapmasi gerekmez.
    #
    # website_monitor bildirimlerinde daima null'dir;
    # mevcut detay ekrani davranisi degismez.
    regulation_comparison: (
        RegulationComparisonResponse | None
    ) = None
