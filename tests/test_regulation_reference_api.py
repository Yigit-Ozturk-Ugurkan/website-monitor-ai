"""
REFERANS YONETMELIK API TESTLERI

Kapsam:
    6.  referans olusturma URL fetch kullaniyor
    7.  content extraction calisiyor
    8.  content hash saklaniyor
    9.  liste ucu
    10. detay ucu
    11. kopya baslik / kopya URL davranisi kontrollu
    12. refresh - icerik degismedi
    13. refresh - icerik degisti
    25. referans olusturma/listeleme EK GEMINI CAGRISI URETMIYOR

Gercek PostgreSQL uzerinde calisir ama KALICI KAYIT BIRAKMAZ.
Tum istekler dis bir transaction'a bagli session uzerinden
gider ve test sonunda rollback edilir.

DIS AG BAGLANTISI BLOKLANIR. Website fetch fixture ile
degistirilir; herhangi bir siteye istek gitmedigi ayrica
assert edilir.

genai.Client sahte bir nesneyle degistirilir; boylece bir
Gemini istemcisinin OLUSTURULMASI bile yakalanir.

Calistirma:
    python -m tests.test_regulation_reference_api
"""

import socket
import warnings

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import engine, get_db
from app.models.regulation_reference import RegulationReference
from app.repositories.regulation_reference_repository import (
    calculate_content_hash,
)
from app.schemas.regulation_reference import MAX_PREVIEW_CHARS
from app.services import regulation_reference_service


warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


PASSED = []
FAILED = []


def check(label: str, condition: bool, detail: str = "") -> None:

    status = "PASS" if condition else "FAIL"

    print(f"[{status}] {label}" + (f" -> {detail}" if detail else ""))

    if condition:
        PASSED.append(label)
    else:
        FAILED.append(f"{label} -> {detail}")


# ---------------------------------------------------------
# DIS AG BLOKAJI
#
# Loopback (asyncio self-pipe, yerel PostgreSQL) serbest;
# disariya cikan her baglanti hata uretir.
# ---------------------------------------------------------

NETWORK_ATTEMPTS = []

_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


LOOPBACK_HOSTS = {
    "127.0.0.1",
    "::1",
    "localhost",
    "",
}


def _is_loopback(address) -> bool:

    if not isinstance(address, tuple) or not address:
        return True

    host = address[0]

    if not isinstance(host, str):
        return True

    return host in LOOPBACK_HOSTS


def _blocked_connect(self, address, *args, **kwargs):
    if _is_loopback(address):
        return _real_connect(self, address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Test sirasinda dis ag baglantisi denendi: {address}"
    )


def _blocked_create_connection(address, *args, **kwargs):
    if _is_loopback(address):
        return _real_create_connection(address, *args, **kwargs)

    NETWORK_ATTEMPTS.append(address)

    raise AssertionError(
        f"Test sirasinda dis ag baglantisi denendi: {address}"
    )


def block_network():
    socket.socket.connect = _blocked_connect
    socket.create_connection = _blocked_create_connection


def unblock_network():
    socket.socket.connect = _real_connect
    socket.create_connection = _real_create_connection


# ---------------------------------------------------------
# GEMINI CASUSU
#
# genai.Client cagrilirsa kaydedilir. Bu testte sayinin
# SIFIR kalmasi bekleniyor.
# ---------------------------------------------------------

GEMINI_CLIENT_CALLS = []


class SpyGeminiClient:
    def __init__(self, *args, **kwargs):
        GEMINI_CLIENT_CALLS.append(kwargs)

        raise AssertionError(
            "Referans yonetmelik akisinda Gemini istemcisi "
            "olusturuldu."
        )


# ---------------------------------------------------------
# SAHTE WEBSITE FETCH
# ---------------------------------------------------------

SOURCE_URL = "https://mevzuat.test.local/lisans-yonetmeligi"

TITLE = "Referans Test Yonetmeligi"


def build_html(body_text: str) -> str:
    """
    Gercekci bir mevzuat sayfasi taklidi.

    Icinde script/style de var; content_extractor'in bunlari
    GERCEKTEN temizledigi dogrulanabilsin.
    """

    return (
        "<html><head>"
        f"<title>{TITLE} - Resmi Kaynak</title>"
        "<style>.x{color:red}</style>"
        "</head><body>"
        "<script>window.tracker = 'SCRIPT_ICERIGI';</script>"
        f"<div id='content'>{body_text}</div>"
        "</body></html>"
    )


ORIGINAL_BODY = (
    "MADDE 1 - Bu Yonetmeligin amaci elektrik piyasasinda "
    "faaliyet gosterecek tuzel kisilerin lisans islemlerine "
    "iliskin usul ve esaslari duzenlemektir. " * 6
)

CHANGED_BODY = ORIGINAL_BODY + (
    " MADDE 2 - Bu Yonetmelik yayimi tarihinde yururluge girer."
)


@dataclass
class FakeFetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    html: str


FETCH_CALLS = []

# Testin ortasinda degistirilerek "kaynak degisti"
# senaryosu kurulur.
CURRENT_BODY = {"value": ORIGINAL_BODY}


async def fake_fetch_website(url: str):
    FETCH_CALLS.append(url)

    if not url.startswith(("http://", "https://")):
        raise ValueError(
            "URL 'http://' veya 'https://' ile baslamalidir."
        )

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=build_html(CURRENT_BODY["value"]),
    )


EMPTY_SHELL_URL = "https://mevzuat.test.local/bos-sayfa"


async def fake_fetch_empty(url: str):
    """
    JavaScript ile doldurulan bos kabuk sayfa.
    Anlamli metin cikmaz.
    """

    FETCH_CALLS.append(url)

    return FakeFetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        html=(
            "<html><head><title>Bos</title></head>"
            "<body><div id='app'></div></body></html>"
        ),
    )


def main() -> None:

    print("=" * 70)
    print("REFERANS YONETMELIK API TESTLERI")
    print("=" * 70)

    from google import genai

    real_client = genai.Client
    real_fetch = regulation_reference_service.fetch_website

    genai.Client = SpyGeminiClient
    regulation_reference_service.fetch_website = fake_fetch_website

    block_network()

    connection = engine.connect()
    transaction = connection.begin()

    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    app.dependency_overrides[get_db] = lambda: session

    client = TestClient(app)

    reference_count_before = session.scalar(
        select(func.count()).select_from(RegulationReference)
    )

    print(f"Baslangic referans sayisi: {reference_count_before}")
    print("-" * 70)

    try:

        # -------------------------------------------------
        # 6. OLUSTURMA - URL FETCH
        # -------------------------------------------------

        print("\n--- 6. referans olusturma (URL fetch) ---")

        fetch_calls_before = len(FETCH_CALLS)

        response = client.post(
            "/api/regulation-references",
            json={
                "title": TITLE,
                "source_url": SOURCE_URL,
            },
        )

        check(
            "6. referans olusturuldu (201)",
            response.status_code == 201,
            f"status={response.status_code} "
            f"body={response.text[:200]}",
        )

        created = response.json()
        reference_id = created.get("id")

        check(
            "6. olusturma kaynak adresi GERCEKTEN indirdi",
            len(FETCH_CALLS) == fetch_calls_before + 1
            and FETCH_CALLS[-1] == SOURCE_URL,
            f"cagrilar={FETCH_CALLS[fetch_calls_before:]}",
        )

        check(
            "6. gecersiz sema reddediliyor",
            client.post(
                "/api/regulation-references",
                json={
                    "title": "Gecersiz",
                    "source_url": "ftp://mevzuat.test.local/x",
                },
            ).status_code == 422,
        )

        check(
            "6. bos baslik reddediliyor",
            client.post(
                "/api/regulation-references",
                json={
                    "title": "   ",
                    "source_url": "https://mevzuat.test.local/y",
                },
            ).status_code == 422,
        )

        # -------------------------------------------------
        # 7. CONTENT EXTRACTION
        # -------------------------------------------------

        print("\n--- 7. content extraction ---")

        stored = session.get(
            RegulationReference,
            reference_id,
        )

        check(
            "7. kayit veritabaninda bulundu",
            stored is not None,
        )

        content = stored.content or ""

        check(
            "7. yonetmelik metni cikarildi",
            "MADDE 1" in content,
            f"uzunluk={len(content)}",
        )

        check(
            "7. script icerigi metne SIZMADI",
            "SCRIPT_ICERIGI" not in content,
        )

        check(
            "7. style icerigi metne SIZMADI",
            "color:red" not in content,
        )

        check(
            "7. saklanan icerik HTML etiketi TASIMIYOR",
            "<div" not in content and "<html" not in content,
        )

        check(
            "7. content_length gercek uzunlukla ayni",
            created["content_length"] == len(content),
            f"api={created['content_length']} db={len(content)}",
        )

        # -------------------------------------------------
        # 8. CONTENT HASH
        # -------------------------------------------------

        print("\n--- 8. content hash ---")

        check(
            "8. content_hash saklandi",
            bool(stored.content_hash),
        )

        check(
            "8. hash sha256 uzunlugunda",
            len(stored.content_hash) == 64,
            f"uzunluk={len(stored.content_hash)}",
        )

        check(
            "8. hash icerikle tutarli",
            stored.content_hash == calculate_content_hash(content),
        )

        check(
            "8. hash API yanitinda da ayni",
            created["content_hash"] == stored.content_hash,
        )

        # -------------------------------------------------
        # 9. LISTE
        # -------------------------------------------------

        print("\n--- 9. liste ucu ---")

        list_response = client.get("/api/regulation-references")

        check(
            "9. liste 200 donuyor",
            list_response.status_code == 200,
        )

        items = list_response.json()

        listed = [
            item for item in items
            if item["id"] == reference_id
        ]

        check(
            "9. olusturulan kayit listede",
            len(listed) == 1,
        )

        item = listed[0] if listed else {}

        check(
            "9. liste secim icin gerekli alanlari tasiyor",
            all(
                key in item
                for key in (
                    "id",
                    "title",
                    "source_url",
                    "updated_at",
                )
            ),
            f"alanlar={sorted(item.keys())}",
        )

        check(
            "9. liste TAM METNI tasimiyor",
            "content" not in item,
            f"alanlar={sorted(item.keys())}",
        )

        # -------------------------------------------------
        # 10. DETAY
        # -------------------------------------------------

        print("\n--- 10. detay ucu ---")

        detail_response = client.get(
            f"/api/regulation-references/{reference_id}"
        )

        check(
            "10. detay 200 donuyor",
            detail_response.status_code == 200,
        )

        detail = detail_response.json()

        check(
            "10. detay onizleme tasiyor",
            "preview" in detail and bool(detail["preview"]),
        )

        check(
            "10. detay ham 'content' alani DONDURMUYOR",
            "content" not in detail,
            f"alanlar={sorted(detail.keys())}",
        )

        check(
            "10. onizleme duz metin (HTML etiketi yok)",
            "<" not in detail["preview"],
        )

        check(
            "10. onizleme sinirlandirilmis",
            len(detail["preview"]) <= MAX_PREVIEW_CHARS + 3,
            f"uzunluk={len(detail['preview'])} "
            f"sinir={MAX_PREVIEW_CHARS}",
        )

        check(
            "10. olmayan id icin 404",
            client.get(
                "/api/regulation-references/99999999"
            ).status_code == 404,
        )

        # -------------------------------------------------
        # 11. KOPYA DAVRANISI
        # -------------------------------------------------

        print("\n--- 11. kopya baslik / URL davranisi ---")

        duplicate_url = client.post(
            "/api/regulation-references",
            json={
                "title": "Bambaska Bir Ad",
                "source_url": SOURCE_URL,
            },
        )

        check(
            "11. ayni URL ikinci kez kabul EDILMIYOR (409)",
            duplicate_url.status_code == 409,
            f"status={duplicate_url.status_code}",
        )

        check(
            "11. kopya hatasi kullaniciyi yonlendiriyor",
            "zaten" in duplicate_url.json().get("detail", ""),
            duplicate_url.json().get("detail", ""),
        )

        fetch_calls_before = len(FETCH_CALLS)

        client.post(
            "/api/regulation-references",
            json={
                "title": "Yine Ayni Kaynak",
                "source_url": SOURCE_URL,
            },
        )

        check(
            "11. kopya URL icin BOSUNA fetch yapilmiyor",
            len(FETCH_CALLS) == fetch_calls_before,
            f"ek cagri={len(FETCH_CALLS) - fetch_calls_before}",
        )

        same_title = client.post(
            "/api/regulation-references",
            json={
                "title": TITLE,
                "source_url": SOURCE_URL + "/eski-surum",
            },
        )

        check(
            "11. ayni baslik FARKLI kaynakta kabul ediliyor",
            same_title.status_code == 201,
            f"status={same_title.status_code}",
        )

        second_id = (
            same_title.json().get("id")
            if same_title.status_code == 201
            else None
        )

        check(
            "11. ayni baslikli iki kayit ayri id aliyor",
            second_id is not None and second_id != reference_id,
        )

        # -------------------------------------------------
        # 12. REFRESH - ICERIK DEGISMEDI
        # -------------------------------------------------

        print("\n--- 12. refresh (icerik degismedi) ---")

        before = session.get(RegulationReference, reference_id)
        updated_at_before = before.updated_at
        hash_before = before.content_hash

        refresh_same = client.post(
            f"/api/regulation-references/{reference_id}/refresh"
        )

        check(
            "12. refresh 200 donuyor",
            refresh_same.status_code == 200,
            f"status={refresh_same.status_code}",
        )

        same_body = refresh_same.json()

        check(
            "12. changed=False bildiriliyor",
            same_body.get("changed") is False,
            f"changed={same_body.get('changed')}",
        )

        check(
            "12. hash degismedi",
            same_body.get("content_hash") == hash_before,
        )

        check(
            "12. onceki hash de yanitta",
            same_body.get("previous_content_hash") == hash_before,
        )

        session.expire_all()
        after_same = session.get(RegulationReference, reference_id)

        check(
            "12. degismeyen refresh updated_at'i OYNATMIYOR",
            after_same.updated_at == updated_at_before,
            f"once={updated_at_before} sonra={after_same.updated_at}",
        )

        # -------------------------------------------------
        # 13. REFRESH - ICERIK DEGISTI
        # -------------------------------------------------

        print("\n--- 13. refresh (icerik degisti) ---")

        CURRENT_BODY["value"] = CHANGED_BODY

        refresh_changed = client.post(
            f"/api/regulation-references/{reference_id}/refresh"
        )

        check(
            "13. refresh 200 donuyor",
            refresh_changed.status_code == 200,
        )

        changed_body = refresh_changed.json()

        check(
            "13. changed=True bildiriliyor",
            changed_body.get("changed") is True,
            f"changed={changed_body.get('changed')}",
        )

        check(
            "13. yeni hash eskisinden farkli",
            changed_body.get("content_hash") != hash_before,
        )

        check(
            "13. onceki hash dogru raporlaniyor",
            changed_body.get("previous_content_hash") == hash_before,
        )

        session.expire_all()
        after_changed = session.get(
            RegulationReference,
            reference_id,
        )

        check(
            "13. yeni icerik veritabanina yazildi",
            "MADDE 2" in after_changed.content,
        )

        check(
            "13. saklanan hash yeni icerikle tutarli",
            after_changed.content_hash
            == calculate_content_hash(after_changed.content),
        )

        check(
            "13. degisen refresh updated_at'i guncelledi",
            after_changed.updated_at >= updated_at_before,
            f"once={updated_at_before} "
            f"sonra={after_changed.updated_at}",
        )

        check(
            "13. olmayan referansin refresh'i 404",
            client.post(
                "/api/regulation-references/99999999/refresh"
            ).status_code == 404,
        )

        # -------------------------------------------------
        # ANLAMSIZ ICERIK KONTROLLU REDDEDILIYOR
        # -------------------------------------------------

        print("\n--- anlamsiz icerik ---")

        regulation_reference_service.fetch_website = (
            fake_fetch_empty
        )

        empty_response = client.post(
            "/api/regulation-references",
            json={
                "title": "Bos Kabuk Sayfa",
                "source_url": EMPTY_SHELL_URL,
            },
        )

        check(
            "bos sayfa 422 ile reddediliyor",
            empty_response.status_code == 422,
            f"status={empty_response.status_code}",
        )

        empty_detail = empty_response.json().get("detail", "")

        check(
            "hata mesaji kullaniciya aciklayici",
            "yonetmelik" in empty_detail.lower()
            or "yönetmelik" in empty_detail.lower(),
            empty_detail,
        )

        check(
            "reddedilen fetch kayit OLUSTURMADI",
            session.scalar(
                select(func.count())
                .select_from(RegulationReference)
                .where(
                    RegulationReference.source_url
                    == EMPTY_SHELL_URL
                )
            ) == 0,
        )

        regulation_reference_service.fetch_website = (
            fake_fetch_website
        )

        # -------------------------------------------------
        # 25. EK GEMINI CAGRISI YOK
        # -------------------------------------------------

        print("\n--- 25. Gemini cagrisi yok ---")

        check(
            "25. referans akisinda Gemini istemcisi "
            "OLUSTURULMADI",
            len(GEMINI_CLIENT_CALLS) == 0,
            f"cagri sayisi={len(GEMINI_CLIENT_CALLS)}",
        )

        check(
            "25. dis ag baglantisi denenmedi",
            not NETWORK_ATTEMPTS,
            f"denemeler={NETWORK_ATTEMPTS}",
        )

    finally:
        app.dependency_overrides.clear()

        session.close()
        transaction.rollback()
        connection.close()

        unblock_network()

        genai.Client = real_client
        regulation_reference_service.fetch_website = real_fetch

        print("\n" + "-" * 70)
        print("Dis transaction geri alindi.")

    # -----------------------------------------------------
    # KALICI KAYIT BIRAKILMADI MI
    # -----------------------------------------------------

    from app.core.database import SessionLocal

    verify = SessionLocal()

    try:
        reference_count_after = verify.scalar(
            select(func.count()).select_from(RegulationReference)
        )

        leftovers = verify.scalars(
            select(RegulationReference).where(
                RegulationReference.source_url.like(
                    "%mevzuat.test.local%"
                )
            )
        ).all()

        check(
            "veritabaninda referans sayisi degismedi",
            reference_count_after == reference_count_before,
            f"once={reference_count_before} "
            f"sonra={reference_count_after}",
        )

        check(
            "test referansi veritabaninda kalmadi",
            not leftovers,
            f"kalanlar={[r.id for r in leftovers]}",
        )

    finally:
        verify.close()

    print("\n" + "=" * 70)
    print(f"BASARILI : {len(PASSED)}")
    print(f"BASARISIZ: {len(FAILED)}")

    if FAILED:
        print("-" * 70)
        for line in FAILED:
            print(line)

        raise SystemExit(1)

    print("TUM TESTLER BASARILI")
    print("=" * 70)


if __name__ == "__main__":
    main()
