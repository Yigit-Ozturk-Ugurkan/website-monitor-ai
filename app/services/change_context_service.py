"""
Degisiklik detayi icin snapshot context cikarma.

Bu modul SADECE okuma ve sunum amaclidir:
- change detection algoritmasina dokunmaz
- yeni AI cagrisi yapmaz
- veritabanina yazmaz

Tamamen deterministiktir: ayni girdi her zaman ayni ciktiyi verir.
"""

from dataclasses import dataclass


# Degisen satirin oncesinde/sonrasinda gosterilecek satir sayisi.
CONTEXT_LINES = 3

# Cok buyuk snapshot'larda dev bir sayfa render etmemek icin
# uretilecek en fazla blok ve satir sayisi.
MAX_BLOCKS = 20
MAX_LINES_PER_BLOCK = 60


@dataclass
class ContextLine:
    line_number: int
    text: str
    changed: bool

    # Bildirime sebep olan degisiklik satiri mi?
    # AI evidence ile eslesen satirlar icin True.
    evidence: bool = False


@dataclass
class ContextBlock:
    start_line: int
    end_line: int
    lines: list[ContextLine]


def _split_lines(content: str | None) -> list[str]:

    if not content:
        return []

    return content.splitlines()


def is_locatable(line: str) -> bool:
    """
    Satir snapshot icinde konumlandirilmaya uygun mu?

    Ayrac karakterleri ("|", "(", ")" gibi) sayfada onlarca kez
    tekrar ettigi icin exact matching bunlari yanlis yerlerde
    isaretleyebiliyor. Bu satirlar highlight edilmez, ancak
    context olarak yine gorunur.
    """

    cleaned = line.strip()

    if len(cleaned) < 2:
        return False

    return any(
        character.isalnum()
        for character in cleaned
    )


def locate_changed_lines(
    snapshot_lines: list[str],
    changed_lines: list[str],
) -> list[int]:
    """
    Degisen satirlarin snapshot icindeki 0 tabanli indekslerini bulur.

    Exact text matching kullanilir. Ayni metin birden fazla kez
    geciyorsa her degisen satir bir sonraki eslesmemis konuma
    baglanir, boylece tekrar eden satirlar ayni yere yigilmaz.
    """

    positions_by_text: dict[str, list[int]] = {}

    for index, line in enumerate(snapshot_lines):
        positions_by_text.setdefault(line, []).append(index)

    next_position: dict[str, int] = {}

    found: list[int] = []

    for changed in changed_lines:

        positions = positions_by_text.get(changed)

        if not positions:
            continue

        cursor = next_position.get(changed, 0)

        if cursor >= len(positions):
            # Snapshot'ta bu metinden daha fazla kopya yok.
            continue

        found.append(positions[cursor])
        next_position[changed] = cursor + 1

    return sorted(set(found))


def is_evidence_line(
    line: str,
    evidence_text: str | None,
) -> bool:
    """
    Satir AI evidence metnini iceriyor mu?

    Evidence, ai_analyzer icinde kaynak icerikle zaten
    dogrulanmis oldugu icin burada basit bir eslesme yeterli.
    """

    if not evidence_text:
        return False

    cleaned = evidence_text.strip()

    if not cleaned:
        return False

    return cleaned in line.strip()


def build_context_blocks(
    snapshot_content: str | None,
    changed_content: str | None,
    context_lines: int = CONTEXT_LINES,
    max_blocks: int = MAX_BLOCKS,
    evidence_text: str | None = None,
) -> list[ContextBlock]:
    """
    Degisen satirlari snapshot icinde bulup cevrelerindeki
    context ile birlikte bloklar halinde dondurur.

    Yakin degisiklikler tek blokta birlestirilir.
    """

    snapshot_lines = _split_lines(snapshot_content)

    changed_lines = [
        line
        for line in _split_lines(changed_content)
        if is_locatable(line)
    ]

    if not snapshot_lines or not changed_lines:
        return []

    changed_indexes = locate_changed_lines(
        snapshot_lines=snapshot_lines,
        changed_lines=changed_lines,
    )

    if not changed_indexes:
        return []

    changed_index_set = set(changed_indexes)

    # Birbirine yakin degisiklikleri ayni blokta topla.
    groups: list[list[int]] = []

    for index in changed_indexes:

        if groups and index - groups[-1][-1] <= context_lines * 2:
            groups[-1].append(index)
        else:
            groups.append([index])

    blocks: list[ContextBlock] = []

    for group in groups[:max_blocks]:

        start = max(0, group[0] - context_lines)
        end = min(
            len(snapshot_lines) - 1,
            group[-1] + context_lines,
        )

        if end - start + 1 > MAX_LINES_PER_BLOCK:
            end = start + MAX_LINES_PER_BLOCK - 1

        block_lines = [
            ContextLine(
                line_number=position + 1,
                text=snapshot_lines[position],
                changed=position in changed_index_set,
                evidence=is_evidence_line(
                    snapshot_lines[position],
                    evidence_text,
                ),
            )
            for position in range(start, end + 1)
        ]

        blocks.append(
            ContextBlock(
                start_line=start + 1,
                end_line=end + 1,
                lines=block_lines,
            )
        )

    return blocks
