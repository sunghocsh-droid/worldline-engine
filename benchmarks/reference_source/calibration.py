"""Game defaults/ranges, not historical measurements or scripted trajectories."""
SECTORS = ("agriculture", "mining", "light_industry", "heavy_industry", "machinery", "chemicals",
           "energy", "construction", "transport", "shipping", "services")
PROFILES = {
    "preindustrial": {"productivity": 1.0, "capital_efficiency": 1.0, "birth_rate": .036, "death_rate": .028, "depreciation": .03, "saving": .025},
    "early_industrial": {"productivity": 1.3, "capital_efficiency": 1.2, "birth_rate": .032, "death_rate": .024, "depreciation": .04, "saving": .035},
    "industrial": {"productivity": 1.8, "capital_efficiency": 1.5, "birth_rate": .025, "death_rate": .018, "depreciation": .05, "saving": .045},
    "advanced_industrial": {"productivity": 2.4, "capital_efficiency": 1.8, "birth_rate": .019, "death_rate": .012, "depreciation": .06, "saving": .055},
}
RANGES = {"productivity": (.5, 4), "capital_efficiency": (.5, 3), "birth_rate": (.01, .05),
          "death_rate": (.005, .04), "depreciation": (.01, .1), "saving": (0, .15)}
PROFILE_RANGES = {name: {key: [max(RANGES[key][0], value * .65), min(RANGES[key][1], value * 1.5)]
                       for key, value in defaults.items()} for name, defaults in PROFILES.items()}

