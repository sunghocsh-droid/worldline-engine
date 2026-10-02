from dataclasses import asdict, dataclass, field
from datetime import date
from .state_runtime import Record, detached

RISKS = ("war", "diplomatic_crisis", "revolution", "coup", "recession",
         "financial_crisis", "famine", "epidemic", "natural_disaster", "regional_rebellion")


def canonical_date(value):
    if not isinstance(value, str):
        raise ValueError("Date must be a YYYY-MM-DD string")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date must use canonical YYYY-MM-DD format")
    return parsed


@dataclass
class NPC(Record):
    goals: list[str] = field(default_factory=list)
    fears: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    allies: list[str] = field(default_factory=list)
    rivals: list[str] = field(default_factory=list)
    red_lines: list[str] = field(default_factory=list)
    risk_tolerance: float = 50
    ideology: str = ""
    leadership: dict = field(default_factory=dict)
    faction_power: float = 0


@dataclass
class Country(Record):
    name: str
    population: int = 0
    gdp: int = 0
    treasury: int = 0
    debt: int = 0
    industries: dict[str, int] = field(default_factory=dict)  # daily capacity
    technologies: list[str] = field(default_factory=list)
    infrastructure: dict[str, float] = field(default_factory=dict)
    resources: dict[str, int] = field(default_factory=dict)
    stability: float = 50
    npc: NPC = field(default_factory=NPC)


@dataclass
class Region(Record):
    name: str
    owner: str
    population: int = 0  # subset of national population
    x: float = 0  # km on scenario coordinate plane
    y: float = 0
    infrastructure: dict[str, float] = field(default_factory=dict)


@dataclass
class Army(Record):
    owner: str
    region: str
    personnel: int
    speed_km_day: float = 20


@dataclass
class Faction(Record):
    country: str
    name: str
    npc: NPC = field(default_factory=NPC)


@dataclass
class Report(Record):
    as_of: str
    source: str
    confidence: float
    data: dict


@dataclass
class Order(Record):
    id: str
    actor: str
    kind: str
    params: dict


@dataclass
class World(Record):
    date: str
    scenario_id: str = "custom"
    schema_version: int = 2
    countries: dict[str, Country] = field(default_factory=dict)
    regions: dict[str, Region] = field(default_factory=dict)
    armies: dict[str, Army] = field(default_factory=dict)
    relations: dict[str, dict] = field(default_factory=dict)
    factions: dict[str, Faction] = field(default_factory=dict)
    pressures: dict[str, dict] = field(default_factory=dict)
    canon: dict = field(default_factory=dict)
    production: dict[str, int] = field(default_factory=dict)
    tech_requirements: dict[str, list[str]] = field(default_factory=dict)
    projects: dict[str, dict] = field(default_factory=dict)
    population_accounts: dict[str, dict] = field(default_factory=dict)
    economy: dict | None = None
    npc_state: dict | None = None
    crisis_state: dict | None = None
    military_state: dict | None = None

    def to_dict(self):
        raw = detached(self)
        # Legacy games keep their existing serialization and rule versions.
        if self.npc_state is None:
            raw.pop("npc_state")
        if self.crisis_state is None:
            raw.pop("crisis_state")
        if self.military_state is None:
            raw.pop("military_state")
        return raw

    @classmethod
    def from_dict(cls, raw):
        raw = dict(raw)
        raw.pop("note", None)
        version = raw.get("schema_version", 1)
        if version not in (1, 2, 3, 4, 5):
            raise ValueError("Unsupported world schema")
        if (version in (3, 4, 5)) != (raw.get("npc_state") is not None):
            raise ValueError("World schema does not match NPC decision rules")
        if (version in (4, 5)) != (raw.get("crisis_state") is not None):
            raise ValueError("World schema does not match crisis/event rules")
        if (version == 5) != (raw.get("military_state") is not None):
            raise ValueError("World schema does not match military/logistics rules")
        raw["schema_version"] = version if version >= 3 else 2
        canonical_date(raw["date"])
        raw["countries"] = {k: Country(**{**v, "npc": NPC(**v.get("npc", {}))})
                            for k, v in raw.get("countries", {}).items()}
        raw["regions"] = {k: Region(**v) for k, v in raw.get("regions", {}).items()}
        raw["armies"] = {k: Army(**v) for k, v in raw.get("armies", {}).items()}
        raw["factions"] = {k: Faction(**{**v, "npc": NPC(**v.get("npc", {}))})
                           for k, v in raw.get("factions", {}).items()}
        return cls(**raw)
