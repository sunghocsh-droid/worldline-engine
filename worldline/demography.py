"""Period-based population reconciliation shared by audit and monthly demography."""
from .models import canonical_date

FLOWS = ("births", "deaths", "migration", "war_losses", "epidemic_losses", "disaster_losses")


def validate_account(account, today):
    issues = []
    try:
        days = (canonical_date(account["period_end"]) - canonical_date(account["period_start"])).days
        before, after = account["before"], account["after"]
        if days <= 0 or account["elapsed_days"] != days or account["period_end"] > today:
            issues.append("population: invalid settlement interval")
        if type(before) is not int or type(after) is not int or min(before, after) < 0:
            issues.append("population: invalid opening/closing balance")
        for key in FLOWS:
            if type(account[key]) is not int or key != "migration" and account[key] < 0:
                issues.append(f"population: invalid {key}")
        expected = before + account["births"] - account["deaths"] + account["migration"] - sum(account[k] for k in FLOWS[3:])
        if after != expected:
            issues.append("population: unexplained balance change")
        if account["births"] > before * .08 * max(days, 0) / 365 + 2:
            issues.append("population: implausible births for interval")
        if account["deaths"] > before * .12 * max(days, 0) / 365 + 2:
            issues.append("population: exceptional deaths require a loss record")
        if not isinstance(account["sources"], list) or not account["sources"] or not all(isinstance(v, str) for v in account["sources"]):
            issues.append("population: missing flow evidence")
    except (KeyError, TypeError, ValueError):
        issues.append("population: malformed settlement")
    return issues


def explains_change(world, old, country):
    account = world.population_accounts.get(country)
    if not account or validate_account(account, world.date):
        return False
    return (account["period_end"] == world.date and account["before"] == old.countries[country].population
            and account["after"] == world.countries[country].population
            and account != old.population_accounts.get(country))
