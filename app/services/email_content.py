"""
Bildirim e-mail'inin içeriğini üretir.

Bu modül SAF metin üretimidir:

  - veritabanına dokunmaz
  - SMTP bağlantısı açmaz
  - GEMINI ÇAĞIRMAZ

Mail gövdesi tamamen mevcut AIAnalysis kaydından kurulur;
özet ve kanıt zaten analiz sırasında üretilmiştir.

Şablon motoru kullanılmaz; gövde küçük ve okunabilir
tutulur. HTML gövde üretilirken düz metin alternatifi
her zaman birlikte üretilir.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape

from app.core.config import settings
from app.models.ai_analysis import AIAnalysis
from app.models.monitor import Monitor
from app.models.regulation_comparison import (
    RegulationComparison,
)


SUBJECT_PREFIX = "[Website Monitor AI]"


IMPORTANCE_LABELS = {
    "low": "Düşük önem",
    "medium": "Orta önem",
    "high": "Yüksek önem",
}


@dataclass
class EmailContent:
    subject: str
    text_body: str
    html_body: str


def format_importance(importance: str | None) -> str:

    if not importance:
        return "Bilinmiyor"

    return IMPORTANCE_LABELS.get(
        importance,
        importance,
    )


def format_timestamp(value: datetime | None) -> str:

    if value is None:
        return "Bilinmiyor"

    # Veritabanindaki timestamp'ler datetime.utcnow() ile
    # yazildigi icin UTC olarak etiketlenir.
    return value.strftime("%d.%m.%Y %H:%M") + " (UTC)"


def build_detail_url(notification_id: int | None) -> str | None:
    """
    Uygulamadaki bildirim detay adresi.

    notification_id yoksa baglanti uretilmez; mail yine
    gonderilir.
    """

    if notification_id is None:
        return None

    base = (settings.app_base_url or "").strip().rstrip("/")

    if not base:
        return None

    return f"{base}/notifications/{notification_id}"


def build_subject(
    monitor: Monitor,
    ai_analysis: AIAnalysis,
) -> str:

    return (
        f"{SUBJECT_PREFIX} {monitor.name} — "
        f"{format_importance(ai_analysis.importance)}"
    )


def _text_section(label: str, value: str) -> str:
    return f"{label}:\n{value}"


# ---------------------------------------------------------
# MEVZUAT KARSILASTIRMASI
#
# Mail bir OZETTIR. Yalnizca sunucu tarafinda DOGRULANMIS
# madde satirlari kullanilir; burada yeni metin uretilmez.
# Tam karsilastirma uygulamadaki detay ekranindadir.
# ---------------------------------------------------------

MAX_EMAIL_COMPARISON_ITEMS = 5


CHANGE_TYPE_LABELS = {
    "amended": "Değiştirildi",
    "added": "Eklendi",
    "repealed": "Yürürlükten kaldırıldı",
    "renamed": "Adı değiştirildi",
    "unknown": "Belirlenemedi",
}


def format_change_type(change_type: str | None) -> str:

    if not change_type:
        return "Belirlenemedi"

    return CHANGE_TYPE_LABELS.get(
        change_type,
        change_type,
    )


def comparison_item_lines(
    regulation_comparison: RegulationComparison | None,
) -> list[str]:
    """
    Mail icin kisa madde satirlari uretir.

    Karsilastirma yoksa BOS liste doner; bu durumda mail
    govdesi birebir eski haliyle ayni kalir.
    """

    if regulation_comparison is None:
        return []

    items = list(regulation_comparison.items)

    if not items:
        return []

    lines = [
        f"{item.affected_article or 'Madde belirlenemedi'}"
        f" — {format_change_type(item.change_type)}"
        for item in items[:MAX_EMAIL_COMPARISON_ITEMS]
    ]

    remaining = len(items) - MAX_EMAIL_COMPARISON_ITEMS

    if remaining > 0:
        lines.append(f"... ve {remaining} madde daha")

    return lines


def build_text_body(
    monitor: Monitor,
    ai_analysis: AIAnalysis,
    changed_at: datetime | None,
    detail_url: str | None,
    regulation_comparison: RegulationComparison | None = None,
) -> str:
    """
    Duz metin govde.

    Evidence yoksa o bolum HIC eklenmez; mail uretimi
    basarisiz olmaz.
    """

    parts = [
        "Takip ettiğiniz sayfada konuyla ilgili bir "
        "değişiklik bulundu.",
        "",
        _text_section("Monitör", monitor.name),
        "",
        _text_section("Website", monitor.url),
        "",
        _text_section("Takip konusu", monitor.topic),
        "",
        _text_section("Kategori", ai_analysis.category),
        "",
        _text_section(
            "Önem",
            format_importance(ai_analysis.importance),
        ),
        "",
        _text_section("AI Özeti", ai_analysis.summary),
    ]

    if ai_analysis.evidence_text:
        parts.extend(
            [
                "",
                _text_section(
                    "Bildirime sebep olan değişiklik",
                    ai_analysis.evidence_text,
                ),
            ]
        )

    comparison_lines = comparison_item_lines(
        regulation_comparison
    )

    if comparison_lines:

        summary = (
            regulation_comparison.overall_summary or ""
        ).strip()

        parts.extend(
            [
                "",
                _text_section(
                    "Mevzuat karşılaştırması",
                    summary or "Etkilenen maddeler:",
                ),
            ]
        )

        # HANGI YONETMELIKLE KARSILASTIRILDI
        #
        # Otomatik modda referansi KULLANICI secmez; sistem
        # kesfeder. Madde satirlarini hangi metne karsi
        # cikardigimizi soylemezsek kullanici e-postadan
        # dogrulayamaz. Referans adi yoksa bolum HIC
        # eklenmez.
        reference_title = (
            regulation_comparison.reference_regulation_title
        )

        if reference_title:
            parts.extend(
                [
                    "",
                    _text_section(
                        "Karşılaştırılan mevzuat",
                        reference_title,
                    ),
                ]
            )

        parts.extend(
            [
                "",
                "\n".join(
                    f"- {line}"
                    for line in comparison_lines
                ),
                "",
                "AI tarafından oluşturulan karşılaştırmadır. "
                "Resmî metin esas alınmalıdır.",
            ]
        )

    parts.extend(
        [
            "",
            _text_section(
                "Değişiklik zamanı",
                format_timestamp(changed_at),
            ),
        ]
    )

    if detail_url:
        parts.extend(
            [
                "",
                _text_section("Detay", detail_url),
            ]
        )

    parts.extend(
        [
            "",
            "—",
            "Website Monitor AI",
        ]
    )

    return "\n".join(parts)


def _html_row(label: str, value: str) -> str:
    """
    Tum degerler escape edilir; sayfa icerigi mail
    icinde HTML olarak yorumlanmaz.
    """

    return (
        '<tr>'
        '<td style="padding:6px 12px 6px 0;'
        'color:#667085;white-space:nowrap;'
        'vertical-align:top;">'
        f"{escape(label)}"
        "</td>"
        '<td style="padding:6px 0;color:#101828;">'
        f"{escape(value)}"
        "</td>"
        "</tr>"
    )


def build_html_body(
    monitor: Monitor,
    ai_analysis: AIAnalysis,
    changed_at: datetime | None,
    detail_url: str | None,
    regulation_comparison: RegulationComparison | None = None,
) -> str:
    """
    Sade HTML govde.

    Sablon motoru yok; stiller satir ici tutulur cunku
    mail istemcileri harici CSS'i uygulamaz.
    """

    rows = [
        _html_row("Monitör", monitor.name),
        _html_row("Website", monitor.url),
        _html_row("Takip konusu", monitor.topic),
        _html_row("Kategori", ai_analysis.category),
        _html_row(
            "Önem",
            format_importance(ai_analysis.importance),
        ),
        _html_row(
            "Değişiklik zamanı",
            format_timestamp(changed_at),
        ),
    ]

    evidence_block = ""

    if ai_analysis.evidence_text:
        evidence_block = (
            '<p style="margin:0 0 6px;color:#667085;'
            'font-size:13px;">'
            "Bildirime sebep olan değişiklik"
            "</p>"
            '<blockquote style="margin:0 0 20px;padding:10px 14px;'
            'background:#f9fafb;border-left:3px solid #2563eb;'
            'color:#101828;font-size:14px;">'
            f"{escape(ai_analysis.evidence_text)}"
            "</blockquote>"
        )

    # Mevzuat karsilastirmasi bloku.
    # Tum degerler escape edilir; mevzuat metni mail
    # icinde HTML olarak yorumlanmaz.
    comparison_block = ""

    comparison_lines = comparison_item_lines(
        regulation_comparison
    )

    if comparison_lines:

        summary = (
            regulation_comparison.overall_summary or ""
        ).strip()

        rendered_items = "".join(
            f"<li>{escape(line)}</li>"
            for line in comparison_lines
        )

        # Referans adi yoksa satir HIC eklenmez.
        reference_title = (
            regulation_comparison.reference_regulation_title
        )

        reference_line = (
            '<p style="margin:0 0 10px;color:#667085;'
            'font-size:13px;">'
            "Karşılaştırılan mevzuat: "
            f"{escape(reference_title)}"
            "</p>"
            if reference_title
            else ""
        )

        comparison_block = (
            '<p style="margin:0 0 6px;color:#667085;'
            'font-size:13px;">'
            "Mevzuat karşılaştırması"
            "</p>"
            '<p style="margin:0 0 10px;">'
            f"{escape(summary)}"
            "</p>"
            f"{reference_line}"
            '<ul style="margin:0 0 10px;padding-left:20px;">'
            f"{rendered_items}"
            "</ul>"
            '<p style="margin:0 0 20px;color:#98a2b3;'
            'font-size:12px;">'
            "AI tarafından oluşturulan karşılaştırmadır. "
            "Resmî metin esas alınmalıdır."
            "</p>"
        )

    detail_block = ""

    if detail_url:
        detail_block = (
            '<p style="margin:20px 0 0;">'
            f'<a href="{escape(detail_url, quote=True)}" '
            'style="color:#2563eb;">'
            "Değişikliğin tamamını uygulamada görüntüle"
            "</a>"
            "</p>"
        )

    return (
        '<div style="font-family:-apple-system,Segoe UI,'
        'Roboto,Helvetica,Arial,sans-serif;'
        'font-size:14px;line-height:1.6;color:#101828;'
        'max-width:640px;">'
        '<p style="margin:0 0 18px;">'
        "Takip ettiğiniz sayfada konuyla ilgili bir "
        "değişiklik bulundu."
        "</p>"
        '<table style="border-collapse:collapse;'
        'margin:0 0 20px;" role="presentation">'
        f"{''.join(rows)}"
        "</table>"
        '<p style="margin:0 0 6px;color:#667085;'
        'font-size:13px;">AI Özeti</p>'
        '<p style="margin:0 0 20px;">'
        f"{escape(ai_analysis.summary)}"
        "</p>"
        f"{evidence_block}"
        f"{comparison_block}"
        f"{detail_block}"
        '<p style="margin:24px 0 0;color:#98a2b3;'
        'font-size:12px;">Website Monitor AI</p>'
        "</div>"
    )


def build_change_email(
    monitor: Monitor,
    ai_analysis: AIAnalysis,
    changed_at: datetime | None = None,
    notification_id: int | None = None,
    regulation_comparison: RegulationComparison | None = None,
) -> EmailContent:
    """
    Bir değişiklik bildirimi için mail içeriğini üretir.

    Yeni bir AI çağrısı YAPILMAZ; tüm bilgi mevcut
    AIAnalysis ve (varsa) RegulationComparison kayıtlarından
    gelir.

    regulation_comparison verilmezse gövde birebir eski
    haliyle aynı kalır.
    """

    detail_url = build_detail_url(notification_id)

    return EmailContent(
        subject=build_subject(
            monitor=monitor,
            ai_analysis=ai_analysis,
        ),
        text_body=build_text_body(
            monitor=monitor,
            ai_analysis=ai_analysis,
            changed_at=changed_at,
            regulation_comparison=regulation_comparison,
            detail_url=detail_url,
        ),
        html_body=build_html_body(
            monitor=monitor,
            ai_analysis=ai_analysis,
            changed_at=changed_at,
            detail_url=detail_url,
            regulation_comparison=regulation_comparison,
        ),
    )
