"""Pydantic-Datenmodelle fuer den Preisvergleich-Agenten."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SearchType(str, Enum):
    EAN = "ean"
    NAME = "name"


class DataSource(str, Enum):
    IDEALO = "idealo"
    GEIZHALS = "geizhals"
    GOOGLE_SHOPPING = "google_shopping"


class ProductOffer(BaseModel):
    """Ein einzelnes Angebot eines Haendlers fuer ein Produkt."""

    merchant_name: str = Field(description="Name des Haendlers")
    price: float = Field(description="Preis in Euro (ohne Versand)")
    shipping_cost: float = Field(
        default=0.0, description="Versandkosten in Euro"
    )
    total_price: float = Field(description="Gesamtpreis inkl. Versand in Euro")
    product_url: str = Field(description="URL zum Angebot beim Haendler")
    source_url: str = Field(description="URL der Preisvergleichsseite")
    source: DataSource = Field(description="Datenquelle")
    availability: Optional[str] = Field(
        default=None, description="Verfuegbarkeit (z.B. 'Auf Lager')"
    )
    delivery_time: Optional[str] = Field(
        default=None, description="Lieferzeit (z.B. '1-3 Werktage')"
    )
    rating: Optional[float] = Field(
        default=None, description="Haendlerbewertung (0-5)"
    )
    rating_count: Optional[int] = Field(
        default=None, description="Anzahl der Haendlerbewertungen"
    )


class ProductResult(BaseModel):
    """Ein Produkt mit allen gefundenen Angeboten."""

    name: str = Field(description="Produktname")
    ean: Optional[str] = Field(default=None, description="EAN/GTIN")
    brand: Optional[str] = Field(default=None, description="Hersteller/Marke")
    category: Optional[str] = Field(
        default=None, description="Produktkategorie"
    )
    image_url: Optional[str] = Field(
        default=None, description="URL des Produktbildes"
    )
    offers: List[ProductOffer] = Field(
        default_factory=list, description="Liste aller gefundenen Angebote"
    )
    cheapest_price: Optional[float] = Field(
        default=None, description="Guenstigster Gesamtpreis"
    )
    cheapest_merchant: Optional[str] = Field(
        default=None, description="Guenstigster Haendler"
    )

    def model_post_init(self, __context) -> None:
        if self.offers:
            cheapest = min(self.offers, key=lambda o: o.total_price)
            self.cheapest_price = cheapest.total_price
            self.cheapest_merchant = cheapest.merchant_name


class AlternativeProduct(BaseModel):
    """Ein guenstigeres Alternativprodukt."""

    name: str = Field(description="Produktname der Alternative")
    brand: Optional[str] = Field(default=None, description="Hersteller/Marke")
    cheapest_price: float = Field(
        description="Guenstigster verfuegbarer Preis in Euro"
    )
    cheapest_merchant: str = Field(description="Haendler mit dem besten Preis")
    product_url: str = Field(description="URL zum Angebot")
    source_url: str = Field(description="URL der Preisvergleichsseite")
    source: DataSource = Field(description="Datenquelle")
    savings: Optional[float] = Field(
        default=None,
        description="Ersparnis gegenueber dem Referenzprodukt in Euro",
    )
    savings_percent: Optional[float] = Field(
        default=None,
        description="Ersparnis gegenueber dem Referenzprodukt in Prozent",
    )
    similarity_note: Optional[str] = Field(
        default=None, description="Hinweis zur Aehnlichkeit/Kompatibilitaet"
    )


class PriceInsights(BaseModel):
    """Statistische Preis-Insights fuer Einkaufsentscheidungen."""

    min_price: float = Field(description="Guenstigster Gesamtpreis")
    max_price: float = Field(description="Teuerster Gesamtpreis")
    median_price: float = Field(description="Median-Gesamtpreis")
    avg_price: float = Field(description="Durchschnittlicher Gesamtpreis")
    price_spread: float = Field(
        description="Preisspanne (max - min) in Euro"
    )
    price_spread_percent: float = Field(
        description="Preisspanne in Prozent bezogen auf den Median"
    )
    num_offers: int = Field(description="Anzahl der Angebote")
    num_merchants: int = Field(description="Anzahl unterschiedlicher Haendler")
    sources_with_results: List[str] = Field(
        description="Quellen mit Ergebnissen"
    )


class PriceComparisonResult(BaseModel):
    """Gesamtergebnis einer Preisvergleichssuche."""

    query: str = Field(description="Suchanfrage (EAN oder Produktname)")
    search_type: SearchType = Field(description="Art der Suche")
    products: List[ProductResult] = Field(
        default_factory=list, description="Gefundene Produkte mit Angeboten"
    )
    alternatives: List[AlternativeProduct] = Field(
        default_factory=list, description="Guenstigere Alternativprodukte"
    )
    sources_queried: List[DataSource] = Field(
        default_factory=list, description="Abgefragte Datenquellen"
    )
    total_offers_found: int = Field(
        default=0, description="Gesamtanzahl gefundener Angebote"
    )
    error_messages: List[str] = Field(
        default_factory=list,
        description="Fehlermeldungen von einzelnen Quellen",
    )
    insights: Optional[PriceInsights] = Field(
        default=None, description="Statistische Preis-Insights"
    )


class BatchSearchItem(BaseModel):
    """Ein einzelnes Element in einer Batch-Suchanfrage."""

    query: str = Field(description="EAN oder Produktname")
    quantity: int = Field(default=1, ge=1, description="Benoetigte Menge")
    label: Optional[str] = Field(
        default=None,
        description="Optionale Bezeichnung/Positionsnummer aus dem Leistungsverzeichnis",
    )


class BatchSearchResultItem(BaseModel):
    """Ergebnis fuer ein einzelnes Element einer Batch-Suche."""

    query: str = Field(description="Urspruengliche Suchanfrage")
    label: Optional[str] = Field(default=None, description="Positionsbezeichnung")
    quantity: int = Field(default=1, description="Benoetigte Menge")
    product_name: Optional[str] = Field(
        default=None, description="Gefundener Produktname"
    )
    unit_price: Optional[float] = Field(
        default=None, description="Guenstigster Stueckpreis inkl. Versand"
    )
    total_price: Optional[float] = Field(
        default=None, description="Gesamtpreis (Stueckpreis x Menge)"
    )
    merchant: Optional[str] = Field(
        default=None, description="Guenstigster Haendler"
    )
    num_offers: int = Field(default=0, description="Anzahl gefundener Angebote")
    insights: Optional[PriceInsights] = Field(
        default=None, description="Preis-Insights"
    )
    error: Optional[str] = Field(default=None, description="Fehlermeldung")
