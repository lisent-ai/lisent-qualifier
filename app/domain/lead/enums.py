from enum import StrEnum


class LeadSource(StrEnum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"
    WEBSITE = "website"
    WHATSAPP = "whatsapp"
    OTHER = "other"


class ProjectType(StrEnum):
    RESIDENTIAL = "residential"       # konut
    COMMERCIAL = "commercial"         # ticari
    INDUSTRIAL = "industrial"         # endüstriyel
    RENOVATION = "renovation"         # tadilat
    LAND = "land"                     # arsa
    OTHER = "other"


class BudgetRange(StrEnum):
    UNDER_500K = "under_500k"
    RANGE_500K_1M = "500k_1m"
    RANGE_1M_3M = "1m_3m"
    RANGE_3M_10M = "3m_10m"
    OVER_10M = "over_10m"
    UNKNOWN = "unknown"


class DecisionAuthority(StrEnum):
    SOLE = "sole"             # tek karar verici
    JOINT = "joint"           # ortak karar
    INFLUENCER = "influencer" # etki sahibi, başkası karar verir
    UNKNOWN = "unknown"


class TimelineUrgency(StrEnum):
    IMMEDIATE = "immediate"   # < 1 ay
    SHORT = "short"           # 1-3 ay
    MEDIUM = "medium"         # 3-12 ay
    LONG = "long"             # > 12 ay
    UNKNOWN = "unknown"
