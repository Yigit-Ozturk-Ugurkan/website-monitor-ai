from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str

    # Uygulama saat dilimi
    # schedule_time bu saat diliminde yorumlanır.
    app_timezone: str = "Europe/Istanbul"

    # EK CA SERTIFIKALARI (opsiyonel)
    #
    # Bazi sunucular TLS el sikismasinda ara sertifikayi
    # gondermez ve zincir kurulamaz. Projeyle gelen
    # app/certs/ altindaki ara sertifikalar zaten
    # yuklenir; bu ayar ek bir PEM dosyasi gostermek
    # icindir.
    #
    # Sertifika DOGRULAMASINI KAPATMAZ. Dogrulamayi
    # kapatan bir ayar bilerek yoktur.
    extra_ca_bundle: str | None = None

    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-3.6-flash"

    # Primary model kota/rate limit hatasi verirse
    # yalnizca BIR KEZ denenecek yedek model.
    # Bos birakilirsa fallback devre disi kalir.
    gemini_fallback_model: str | None = "gemini-3.7-flash"

    # Uygulamanin frontend adresi.
    # Bildirim e-mail'lerindeki detay baglantisi bununla kurulur.
    app_base_url: str = "http://localhost:5173"

    # SMTP
    smtp_host: str | None = None
    smtp_port: int = 587

    smtp_username: str | None = None
    smtp_password: str | None = None

    smtp_from_email: str | None = None

    # Gonderen adi. Bos birakilirsa yalnizca adres kullanilir.
    smtp_from_name: str | None = "Website Monitor AI"

    # STARTTLS (varsayilan, 587)
    smtp_use_tls: bool = True

    # Bastan SSL (genellikle 465).
    # Acikken STARTTLS uygulanmaz.
    smtp_use_ssl: bool = False

    # Sadece geliştirme/test için
    smtp_test_to: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()