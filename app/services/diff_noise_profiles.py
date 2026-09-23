"""
KAYNAGA OZGU UCUCU SATIR PROFILLERI

Bazi sayfalar her getirmede degisen ama ICERIK OLMAYAN
satirlar tasir. Ornek (Techmeme):

    September 7, 2026, 3:25 AM      <- onceki getirme
    September 7, 2026, 4:00 AM      <- simdiki getirme

Bu bir haber degisikligi degil, sayfanin kendi
"snapshot saati"dir.

NEDEN GENEL BIR REGEX YOK

    "tarih/saat gorursen sil" ya da "N hours ago gorursen
    sil" kurallari YANLIS olurdu. Baska sayfalarda ayni
    bicimler gercek icerik olabilir:

        yayim tarihi
        son basvuru tarihi
        "Updated 3 hours ago"
        yururluk tarihi

    Bu yuzden buradaki kurallarin TAMAMI kaynak
    profiline baglidir. Profil eslesmezse hicbir sey
    filtrelenmez.

NASIL CALISIR

    Cikti her zaman SNAPSHOT'TA GERCEKTEN BULUNAN,
    normalize edilmis TAM SATIRLARDIR. reduce_diff_noise()
    bu kumeye bakar; desen bilmez.

    Iki tur kural vardir:

    1. ISARET + KONUM  (VolatileLineProfile)
       Sayfada birebir bulunan bir isaret satirina gore
       konumla belirlenir. Tarih ayristirilmaz.

           September 7, 2026, 4:00 AM             <- ucucu
           Enter Techmeme snapshot date and time: <- ISARET

    2. KAYNAK PROFILI  (Techmeme UI chrome)
       Yalnizca sayfa Techmeme olarak taninirsa calisir.
       Bkz. _techmeme_ui_lines().

GUVENLIK KURALI

    Bir metin, ancak sayfadaki BUTUN gecisleri gurultu
    yapisinin parcasiysa silinecekler kumesine girer.
    Tek bir gecis bile yapinin disindaysa o metin
    KORUNUR. Suphede kalinirsa silinmez.

SINIRLARI

    Bu bir eklenti cercevesi DEGILDIR. Kucuk ve acik bir
    tablodur.
"""

import re

from collections import Counter
from dataclasses import dataclass

from app.services.diff_noise_reducer import normalize_line


@dataclass(frozen=True)
class VolatileLineProfile:
    """
    Tek bir kaynak icin ucucu satir kurali.

    marker : sayfada birebir bulunmasi gereken isaret satiri
    offset : ucucu satirin isarete gore konumu
             (-1 = isaretten hemen onceki satir)
    """

    name: str
    marker: str
    offset: int


PROFILES: tuple[VolatileLineProfile, ...] = (
    VolatileLineProfile(
        name="techmeme_snapshot_clock",
        marker="Enter Techmeme snapshot date and time:",
        offset=-1,
    ),
)


# ---------------------------------------------------------
# TECHMEME KAYNAK PROFILI
#
# Asagidaki kurallarin TAMAMI yalnizca sayfa Techmeme
# olarak taninirsa calisir. Tanima, sayfaya ozgu ve
# birebir bir isaret satiriyla yapilir.
# ---------------------------------------------------------

TECHMEME_MARKER = "Enter Techmeme snapshot date and time:"


# UI ac/kapa kontrolleri.
#
# TAM SATIR eslesmesi (^...$) zorunludur; boylece
# "Expand OpenAI coverage" gibi gercek metin ETKILENMEZ.
#
# Gercek snapshot'ta gozlenen varyasyonlar:
#     Expand More For Next
#     Expand More For Next 3
#     Unexpand More For Next
#     Unexpand More For Next 3
# DIKKAT: gercek sayfada "Unexpand" kucuk 'e' ile yazilir
# ("UnExpand" DEGIL). Iki bicim de acikca listelenir;
# buyuk/kucuk harf duyarsizligi BILEREK eklenmez, cunku
# desen ne kadar genis olursa gercek metni yakalama riski
# o kadar artar.
EXPAND_CONTROL_PATTERN = re.compile(
    r"^(?:Expand|Unexpand) More For Next(?: \d+)?$"
)


# "Find" + goreli zaman blokunun zaman satiri.
#
# Yalnizca TAM SATIR ve yalnizca Techmeme profilinde.
# Gercek snapshot'ta gozlenenler:
#     2 hours ago / 2½ hours ago / 9 hours ago
# Ayrica tekil bicimler de desteklenir.
#
# BILEREK YAKALANMAYANLAR:
#     "September 7, 2026"
#     "3 days remaining"
#     "OpenAI announced this 3 hours ago"   (tam satir degil)
RELATIVE_TIME_PATTERN = re.compile(
    r"^\d+[½¼¾]?\s+(?:hour|hours|minute|minutes)\s+ago$"
)


# Goreli zaman satirinin ONUNDE bulunmasi gereken satir.
FIND_MARKER = "Find"


def _is_techmeme(normalized_lines: list[str]) -> bool:
    """
    Sayfa Techmeme mi?

    Tanima birebir bir isaret satiriyla yapilir; alan adi
    veya URL'ye BAKILMAZ (pipeline bu bilgiyi tasimaz).
    """

    return normalize_line(TECHMEME_MARKER) in set(
        normalized_lines
    )


def _safe_to_drop(
    text: str,
    total: Counter,
    structural: Counter,
) -> bool:
    """
    Bu metnin BUTUN gecisleri gurultu yapisinin parcasi mi?

    Degilse metin KORUNUR: sayfada ayni metin bir de gercek
    icerik olarak geciyor olabilir.
    """

    return bool(text) and total[text] == structural[text]


def _techmeme_ui_lines(normalized: list[str]) -> set[str]:
    """
    Techmeme'e ozgu UI chrome satirlarini toplar.

    KURAL 1 - AC/KAPA KONTROLLERI

        Expand More For Next 4
        Unexpand More For Next 4

        Tam satir eslesmesi; haber metni etkilenmez.

    KURAL 2 - "Find" + GORELI ZAMAN BLOKU

        Find
        2½ hours ago

        Ikisi de YAPISAL olarak taninir: goreli zaman
        satiri yalnizca hemen ONCESINDE "Find" varsa
        gurultu sayilir.

        Boylece:
          - tek basina "Find"          -> yapinin disinda
                                          gecerse KORUNUR
          - "OpenAI announced this 3 hours ago"
                                       -> tam satir degil,
                                          hic eslesmez
    """

    found: set[str] = set()

    # -----------------------------------------------------
    # KURAL 1
    # -----------------------------------------------------

    for line in normalized:
        if EXPAND_CONTROL_PATTERN.match(line):
            found.add(line)

    # -----------------------------------------------------
    # KURAL 2
    #
    # Once yapisal gecisler sayilir, sonra "butun gecisleri
    # yapisal mi?" sorusu sorulur.
    # -----------------------------------------------------

    total = Counter(line for line in normalized if line)

    structural: Counter = Counter()

    for index, line in enumerate(normalized):

        if line != FIND_MARKER:
            continue

        if index + 1 >= len(normalized):
            continue

        following = normalized[index + 1]

        if not RELATIVE_TIME_PATTERN.match(following):
            continue

        structural[FIND_MARKER] += 1
        structural[following] += 1

    for text in structural:
        if _safe_to_drop(text, total, structural):
            found.add(text)

    return found


def _volatile_lines_in(content: str | None) -> set[str]:
    """
    Tek bir snapshot icindeki ucucu satirlari bulur.
    """

    if not content:
        return set()

    lines = content.splitlines()

    # Isaret satirlari normalize edilerek aranir; sayfadaki
    # bosluk farklari kurali bozmasin.
    normalized = [normalize_line(line) for line in lines]

    found: set[str] = set()

    # -----------------------------------------------------
    # ISARET + KONUM KURALLARI
    # -----------------------------------------------------

    for profile in PROFILES:

        marker = normalize_line(profile.marker)

        for index, line in enumerate(normalized):

            if line != marker:
                continue

            target = index + profile.offset

            if 0 <= target < len(normalized):

                candidate = normalized[target]

                # Bos satir ucucu sayilmaz; zaten anlamsiz
                # satir filtresi onu ele alir.
                if candidate:
                    found.add(candidate)

    # -----------------------------------------------------
    # KAYNAK PROFILI
    # -----------------------------------------------------

    if _is_techmeme(normalized):
        found |= _techmeme_ui_lines(normalized)

    return found


def collect_volatile_lines(
    old_content: str | None,
    new_content: str | None,
) -> frozenset[str]:
    """
    Iki snapshot'taki ucucu satirlarin BIRLESIMI.

    Donen degerler NORMALIZE EDILMIS satirlardir;
    reduce_diff_noise() karsilastirmayi ayni normalizasyon
    ile yapar.

    Hicbir profil eslesmezse BOS kume doner ve filtreleme
    yapilmaz.
    """

    return frozenset(
        _volatile_lines_in(old_content)
        | _volatile_lines_in(new_content)
    )
