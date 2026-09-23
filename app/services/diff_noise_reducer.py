"""
DIFF GURULTU AZALTMA

Satir bazli diff, icerigi YENIDEN SIRALANAN sayfalarda
gercek bir degisiklik olmasa bile devasa cikti uretir.

Gercek ornek (Techmeme, monitor 159):

    OLD                      NEW
    Business Insider         Business Insider
    The Information          Security Affairs
    Security Affairs         The Information
    ...                      ...

difflib bunlarin cogunu hem "removed" hem "added" olarak
raporlar. Sonuc:

  - Change kaydi sisiyor
  - Gemini gereksiz metin goruyor (token maliyeti)
  - bildirim detayi gurultulu
  - yanlis pozitif riski artiyor

BU MODUL NE YAPAR

Ham diff'i ALMAZ, DEGISTIRMEZ. Ham diff uretildikten
SONRA calisan ayri ve deterministik bir azaltma
asamasidir:

    ham diff -> reduce_diff_noise() -> anlamli diff

ILKELER

  1. Snapshot'a DOKUNULMAZ. Ham gercek
     old_snapshot.content / new_snapshot.content
     icinde korunur; diff her zaman yeniden uretilebilir.

  2. Yalnizca BIREBIR (normalize edilmis) esit satirlar
     iptal edilir. Benzer metinler iptal EDILMEZ:

         "OpenAI announces GPT-6"
         "OpenAI announces GPT-7"

     iki ayri satirdir ve ikisi de korunur.

  3. MULTISET semantigi. Set kullanilmaz; tekrar eden
     satirlarin SAYISI korunur. Bir taraftan iki, digerinden
     bir kopya varsa yalnizca bir cift iptal edilir.

  4. Anlamli icerik ASLA silinmez. Suphede kalinirsa
     satir KORUNUR.

BULANIK (fuzzy) ESLESTIRME YOKTUR. Embedding yoktur.
Gurultu tespiti icin model cagrilmaz.
"""

import unicodedata

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class DiffReduction:
    """
    Azaltma sonucu.

    Sayaclar TANI amaclidir: veritabanina yazilmaz,
    yalnizca log ve testlerde kullanilir.
    """

    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)

    # Konumu degisip iceriği degismedigi icin iptal edilen
    # satir sayisi. Bir cift = 2 satir (1 added + 1 removed).
    moved_lines_cancelled: int = 0

    # Anlam tasimayan (bos / yalnizca noktalama) satirlar.
    trivial_lines_removed: int = 0

    # Kaynak profiline gore ucucu kabul edilen satirlar.
    volatile_lines_removed: int = 0

    @property
    def is_empty(self) -> bool:
        """
        Azaltma sonrasi anlamli degisiklik kaldi mi?
        """

        return (
            not self.added_lines
            and not self.removed_lines
        )

    @property
    def total_removed_noise(self) -> int:
        return (
            self.moved_lines_cancelled
            + self.trivial_lines_removed
            + self.volatile_lines_removed
        )


def normalize_line(line: str) -> str:
    """
    Tasima eslestirmesi icin GUVENLI normalizasyon.

    Yapilan:
        - Unicode NFC birlestirme
        - bastaki/sondaki bosluk kirpma
        - ic bosluklari tek boslugua indirme
          (CR/LF ve sekme dahil)

    BILEREK YAPILMAYAN:
        - kucuk harfe cevirme
        - noktalama silme
        - kelime kokune indirme
        - anlamsal benzerlik

    Bunlar farkli satirlari ayni gosterir ve gercek
    degisiklikleri gizlerdi:

        "Price: $10"  !=  "Price: $11"
        "GPT-6"       !=  "gpt-6"
    """

    if not line:
        return ""

    text = unicodedata.normalize("NFC", line)

    # split() bosluk turlerinin tamamini ayirir.
    return " ".join(text.split())


def is_trivial_line(line: str) -> bool:
    """
    Satir kendi basina bir anlam tasiyor mu?

    Anlamsiz kabul edilenler:
        - bos satir
        - yalnizca noktalama / ayrac:  ","  ":"  "|"  "---"

    Icinde TEK BIR harf veya rakam bulunan satir anlamli
    sayilir ve KORUNUR. Bu bilincli olarak temkinlidir:
    "3" veya "$5" gibi kisa ama gercek icerik silinmez.
    """

    cleaned = line.strip()

    if not cleaned:
        return True

    return not any(
        character.isalnum()
        for character in cleaned
    )


def _cancellable_counts(
    added_keys: list[str],
    removed_keys: list[str],
) -> dict[str, int]:
    """
    Her metin icin kac ciftin iptal edilebilecegini bulur.

    MULTISET kurali:

        iptal = min(eklenen_adet, silinen_adet)

    Ornek:

        OLD: OpenAI, OpenAI, Gemini
        NEW: OpenAI, Gemini

        -> bir OpenAI GERCEKTEN silinmistir; yalnizca
           eslesen ciftler iptal edilir.
    """

    added_counts = Counter(added_keys)
    removed_counts = Counter(removed_keys)

    cancellable: dict[str, int] = {}

    for key, count in added_counts.items():

        overlap = min(count, removed_counts.get(key, 0))

        if overlap > 0:
            cancellable[key] = overlap

    return cancellable


def _drop_cancelled(
    lines: list[str],
    keys: list[str],
    budget: dict[str, int],
) -> list[str]:
    """
    Butce kadar kopyayi atar, kalanlari SIRAYLA korur.
    """

    remaining = dict(budget)

    kept: list[str] = []

    for line, key in zip(lines, keys):

        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            continue

        kept.append(line)

    return kept


def reduce_diff_noise(
    added_lines: list[str],
    removed_lines: list[str],
    volatile_lines: frozenset[str] = frozenset(),
) -> DiffReduction:
    """
    Ham diff'ten gurultuyu ayiklar.

    Sira:

        1. ucucu satirlar   (kaynak profili biliyorsa)
        2. anlamsiz satirlar (bos / yalnizca noktalama)
        3. tasinan satirlar  (birebir eslesen ciftler)

    Ham listeler DEGISTIRILMEZ; yeni listeler dondurulur.

    Karmasiklik O(n): sayim ve sozluk kullanilir, satirlar
    ikili olarak KARSILASTIRILMAZ.
    """

    added_lines = list(added_lines or [])
    removed_lines = list(removed_lines or [])

    volatile_removed = 0
    trivial_removed = 0

    def keep(line: str) -> bool:
        nonlocal volatile_removed, trivial_removed

        if volatile_lines and normalize_line(line) in volatile_lines:
            volatile_removed += 1
            return False

        if is_trivial_line(line):
            trivial_removed += 1
            return False

        return True

    kept_added = [line for line in added_lines if keep(line)]
    kept_removed = [line for line in removed_lines if keep(line)]

    # -----------------------------------------------------
    # TASIMA IPTALI
    #
    # Ayni metin hem eklenenlerde hem silinenlerde varsa
    # satir SILINMEMIS, yalnizca YER DEGISTIRMISTIR.
    # -----------------------------------------------------

    added_keys = [normalize_line(line) for line in kept_added]
    removed_keys = [normalize_line(line) for line in kept_removed]

    cancellable = _cancellable_counts(
        added_keys,
        removed_keys,
    )

    final_added = _drop_cancelled(
        kept_added,
        added_keys,
        cancellable,
    )

    final_removed = _drop_cancelled(
        kept_removed,
        removed_keys,
        cancellable,
    )

    # Bir cift iki satir demektir.
    cancelled = sum(cancellable.values()) * 2

    return DiffReduction(
        added_lines=final_added,
        removed_lines=final_removed,
        moved_lines_cancelled=cancelled,
        trivial_lines_removed=trivial_removed,
        volatile_lines_removed=volatile_removed,
    )
