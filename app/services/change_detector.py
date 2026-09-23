from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class ChangeResult:
    changed: bool
    similarity_ratio: float
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)


def detect_changes(
    old_text: str,
    new_text: str,
) -> ChangeResult:
    """
    Eski ve yeni metin arasındaki değişiklikleri tespit eder.
    """

    if old_text is None:
        raise ValueError("old_text None olamaz.")

    if new_text is None:
        raise ValueError("new_text None olamaz.")

    old_text = old_text.strip()
    new_text = new_text.strip()

    # İçerikler tamamen aynıysa karşılaştırmayı uzatma
    if old_text == new_text:
        return ChangeResult(
            changed=False,
            similarity_ratio=1.0,
        )

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    matcher = SequenceMatcher(
        None,
        old_lines,
        new_lines,
        autojunk=False,
    )

    added_lines = []
    removed_lines = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        if tag == "equal":
            continue

        if tag == "insert":
            added_lines.extend(new_lines[j1:j2])

        elif tag == "delete":
            removed_lines.extend(old_lines[i1:i2])

        elif tag == "replace":
            removed_lines.extend(old_lines[i1:i2])
            added_lines.extend(new_lines[j1:j2])

    similarity_ratio = matcher.ratio()

    return ChangeResult(
        changed=True,
        similarity_ratio=round(similarity_ratio, 4),
        added_lines=added_lines,
        removed_lines=removed_lines,
    )