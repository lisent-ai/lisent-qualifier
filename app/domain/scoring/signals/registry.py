"""
Signal analyzer registry — returns the correct analyzer
for a given language + sector combination.

Usage:
    sentiment = SignalRegistry.get_sentiment_analyzer("tr")
    negative = SignalRegistry.get_negative_detector("tr", "construction")
    qualifier = SignalRegistry.get_sector_qualifier("construction")
"""
from __future__ import annotations

from app.domain.scoring.signals.base import (
    BuyingSignalDetector,
    NegativeSignalDetector,
    SentimentAnalyzer,
)
from app.domain.scoring.qualifiers.base import SectorQualifier


class SignalRegistry:
    """Factory for language-specific and sector-specific analyzers."""

    @staticmethod
    def get_sentiment_analyzer(language: str) -> SentimentAnalyzer:
        lang = language.lower().strip()
        if lang in ("tr", "turkish"):
            from app.domain.scoring.signals.languages.tr import TurkishSentimentAnalyzer
            return TurkishSentimentAnalyzer()
        if lang in ("en", "english"):
            from app.domain.scoring.signals.languages.en import EnglishSentimentAnalyzer
            return EnglishSentimentAnalyzer()
        # Fallback to English
        from app.domain.scoring.signals.languages.en import EnglishSentimentAnalyzer
        return EnglishSentimentAnalyzer()

    @staticmethod
    def get_buying_signal_detector(language: str) -> BuyingSignalDetector:
        lang = language.lower().strip()
        if lang in ("tr", "turkish"):
            from app.domain.scoring.signals.languages.tr import TurkishBuyingSignalDetector
            return TurkishBuyingSignalDetector()
        if lang in ("en", "english"):
            from app.domain.scoring.signals.languages.en import EnglishBuyingSignalDetector
            return EnglishBuyingSignalDetector()
        from app.domain.scoring.signals.languages.en import EnglishBuyingSignalDetector
        return EnglishBuyingSignalDetector()

    @staticmethod
    def get_negative_detector(language: str, sector: str) -> NegativeSignalDetector:
        sec = sector.lower().strip()
        if sec in ("construction", "insaat", "inşaat"):
            from app.domain.scoring.signals.sectors.construction import ConstructionNegativeDetector
            return ConstructionNegativeDetector()
        if sec in ("real_estate", "real-estate", "realestate", "emlak", "gayrimenkul"):
            from app.domain.scoring.signals.sectors.real_estate import RealEstateNegativeDetector
            return RealEstateNegativeDetector()
        from app.domain.scoring.signals.sectors.general import GeneralNegativeDetector
        return GeneralNegativeDetector()

    @staticmethod
    def get_sector_qualifier(sector: str) -> SectorQualifier:
        sec = sector.lower().strip()
        if sec in ("construction", "insaat", "inşaat"):
            from app.domain.scoring.qualifiers.construction import ConstructionQualifier
            return ConstructionQualifier()
        if sec in ("real_estate", "real-estate", "realestate", "emlak", "gayrimenkul"):
            from app.domain.scoring.qualifiers.real_estate import RealEstateQualifier
            return RealEstateQualifier()
        from app.domain.scoring.qualifiers.general import GeneralQualifier
        return GeneralQualifier()

    @staticmethod
    def get_seasonal_modifier(sector: str, month: int | None = None) -> int:
        sec = sector.lower().strip()
        if sec in ("construction", "insaat", "inşaat"):
            from app.domain.scoring.signals.sectors.construction import compute_seasonal_modifier
            return compute_seasonal_modifier(month)
        if sec in ("real_estate", "real-estate", "realestate", "emlak", "gayrimenkul"):
            from app.domain.scoring.signals.sectors.real_estate import compute_seasonal_modifier as re_seasonal
            return re_seasonal(month)
        from app.domain.scoring.signals.sectors.general import compute_seasonal_modifier as general_seasonal
        return general_seasonal(month)
