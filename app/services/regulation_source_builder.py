"""
KARSILASTIRMA KAYNAK METNI

Mevzuat karsilastirmasinin uzerinde calisacagi metni kurar.

NEDEN AYRI BIR KURUCU GEREKIYOR

Ilk surumde kaynak yalnizca change.added_content idi. Bu
cogu Resmî Gazete yayiminda dogru calisir: yeni bir
degisiklik yonetmeligi sayfaya tamamen EKLENEN satirlar
olarak gelir.

Ancak change_detector "replace" durumunda ayni bolgeyi hem
removed hem added listesine yaziyor. Gunluk yayin listesi
gibi sayfalarda onceki gunun kaydi yeni gunun kaydiyla YER
DEGISTIRIR; bu durumda degisikligin bir kismi
removed_content tarafina dusebilir ve yalnizca added'e
bakmak baglami eksik birakir.

TEK KAYNAK KURALI (COK ONEMLI)

Bu modulun urettigi metin, karsilastirmada
TEK VE AYNI KAYNAK olarak kullanilir:

    - Gemini'ye giden prompt bu metinden kurulur
    - source_evidence ve new_text exact dogrulamasi
      AYNI metin uzerinde yapilir

Boylece modelin gordugu her cumle sunucunun da
dogrulayabildigi bir cumledir. Modele gosterilmeyen bir
metin uzerinden kanit kabul edilmez; modele gosterilip
dogrulanamayan bir kanit da reddedilir.

old_text ve reference_evidence bu metinde DEGIL, secilmis
referans adayinda aranir. Bu ayrim korunur.

SINIRLAR

Tum snapshot modele GONDERILMEZ. Bolumler oncelik sirasina
gore alinir ve toplam boyut sinirlanir.
"""

from dataclasses import dataclass, field


# Karsilastirma kaynagi icin toplam ust sinir.
MAX_SOURCE_TEXT_CHARS = 20000

# Kaldirilan icerige ayrilan pay.
# Asil sinyal eklenen icerikte; kaldirilan icerik yalnizca
# "eski ifade neydi" sorusuna baglam saglar.
MAX_REMOVED_CHARS = 4000

# Snapshot baglamina ayrilan pay.
MAX_CONTEXT_CHARS = 3000


# Bolum basliklari.
#
# Bunlar prompt icinde bolumleri ayirir. Exact dogrulama
# ayni metin uzerinde yapildigi icin basliklarin kendisi de
# kaynagin parcasidir; model bir basligi kanit diye
# dondurse bile bu metinde gercekten bulunur ve
# yanlis pozitif olusturmaz (baslikta madde metni yoktur).
ADDED_HEADING = "[YENİ EKLENEN İÇERİK]"
REMOVED_HEADING = "[KALDIRILAN İÇERİK]"
CONTEXT_HEADING = "[SAYFA BAĞLAMI]"


# Mevzuat degisikligine isaret eden ifadeler.
#
# Kaldirilan icerik ve baglam SECICI alinir; sayfadaki her
# satiri kaynaga doldurmak hem kotayi hem de dogrulama
# yuzeyini gereksiz buyutur.
REGULATION_MARKERS = (
    "madde",
    "yönetmelik",
    "yonetmelik",
    "tebliğ",
    "teblig",
    "kanun",
    "kararname",
    "fıkra",
    "fikra",
    "bent",
    "ibare",
    "değiştirilmiştir",
    "degistirilmistir",
    "eklenmiştir",
    "eklenmistir",
    "yürürlükten",
    "yururlukten",
    "kaldırılmıştır",
    "kaldirilmistir",
    "yürürlüğe",
    "yururluge",
)


@dataclass
class ComparisonSource:
    """
    Karsilastirmada kullanilan TEK kaynak metin.

    text hem prompt'a girer hem de exact dogrulamada
    kullanilir.
    """

    text: str

    # Hangi bolumler kullanildi? Teshis ve test icin.
    used_added: bool = False
    used_removed: bool = False
    used_context: bool = False

    truncated: bool = False

    sections: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.text)


def _clean_lines(content: str | None) -> list[str]:

    if not content:
        return []

    return [
        line
        for line in (
            raw.strip()
            for raw in content.splitlines()
        )
        if line
    ]


def looks_regulatory(line: str) -> bool:
    """
    Satir mevzuat degisikligi diliyle ilgili mi?

    Amac kesin siniflandirma degil; sayfadaki menu, tarih,
    sayac gibi satirlarin kaynaga dolmasini engellemek.
    """

    lowered = line.casefold()

    return any(
        marker in lowered
        for marker in REGULATION_MARKERS
    )


def _take(lines: list[str], limit: int) -> tuple[str, bool]:
    """
    Satirlari karakter sinirina kadar alir.

    Doner: (metin, kesildi mi)
    """

    collected: list[str] = []
    total = 0

    for line in lines:

        cost = len(line) + 1

        if total + cost > limit:
            return "\n".join(collected), True

        collected.append(line)
        total += cost

    return "\n".join(collected), False


def build_comparison_source(
    change,
    new_snapshot_content: str | None = None,
    max_chars: int = MAX_SOURCE_TEXT_CHARS,
) -> ComparisonSource:
    """
    Karsilastirma kaynak metnini kurar.

    Oncelik sirasi:

        1. change.added_content        (asil sinyal)
        2. ilgili change.removed_content
        3. yeni snapshot'tan SINIRLI baglam

    Eklenen icerik tek basina yeterliyse digerleri hic
    kullanilmaz; gereksiz metin modele gonderilmez.
    """

    if change is None:
        return ComparisonSource(text="")

    sections: list[str] = []
    parts: list[str] = []

    remaining = max_chars
    truncated = False

    used_added = False
    used_removed = False
    used_context = False

    # -----------------------------------------------------
    # 1. EKLENEN ICERIK
    #
    # Yeni yayimlanan mevzuat metni burada. En yuksek
    # oncelik ve en genis pay bu bolumun.
    # -----------------------------------------------------

    added_lines = _clean_lines(change.added_content)

    if added_lines:

        text, cut = _take(added_lines, remaining)

        if text:
            parts.append(f"{ADDED_HEADING}\n{text}")
            sections.append("added")

            used_added = True
            truncated = truncated or cut

            remaining = max_chars - len("\n\n".join(parts))

    # -----------------------------------------------------
    # 2. KALDIRILAN ICERIK
    #
    # change_detector "replace" durumunda ayni bolgeyi hem
    # removed hem added listesine yazar. Eski ifadenin
    # sayfadan ne oldugu bu bolumden gorulebilir.
    #
    # YALNIZCA mevzuat diline benzeyen satirlar alinir.
    # -----------------------------------------------------

    removed_lines = [
        line
        for line in _clean_lines(change.removed_content)
        if looks_regulatory(line)
    ]

    if removed_lines and remaining > 0:

        budget = min(MAX_REMOVED_CHARS, remaining)

        text, cut = _take(removed_lines, budget)

        if text:
            parts.append(f"{REMOVED_HEADING}\n{text}")
            sections.append("removed")

            used_removed = True
            truncated = truncated or cut

            remaining = max_chars - len("\n\n".join(parts))

    # -----------------------------------------------------
    # 3. SAYFA BAGLAMI
    #
    # Yalnizca eklenen icerik cok zayifsa devreye girer.
    # Tum snapshot ASLA gonderilmez; mevzuat diline benzeyen
    # satirlardan sinirli bir pay alinir.
    # -----------------------------------------------------

    needs_context = (
        not used_added
        or len("\n".join(parts)) < 200
    )

    if needs_context and new_snapshot_content and remaining > 0:

        existing = "\n\n".join(parts)

        context_lines = [
            line
            for line in _clean_lines(new_snapshot_content)
            if looks_regulatory(line)
            and line not in existing
        ]

        if context_lines:

            budget = min(MAX_CONTEXT_CHARS, remaining)

            text, cut = _take(context_lines, budget)

            if text:
                parts.append(f"{CONTEXT_HEADING}\n{text}")
                sections.append("context")

                used_context = True
                truncated = truncated or cut

    combined = "\n\n".join(parts).strip()

    if len(combined) > max_chars:
        combined = combined[:max_chars].rstrip()
        truncated = True

    return ComparisonSource(
        text=combined,
        used_added=used_added,
        used_removed=used_removed,
        used_context=used_context,
        truncated=truncated,
        sections=sections,
    )
