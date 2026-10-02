import argparse
import json
import sqlite3
from pathlib import Path
from .engine import Engine
from .models import World, Order


def main():
    p = argparse.ArgumentParser(description="Worldline Engine (local trusted operator CLI)")
    sub = p.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new")
    new.add_argument("db")
    new.add_argument("--scenario", default="scenarios/korea_1897.json")
    new.add_argument("--seed", type=int, default=1897)
    new.add_argument("--economy", action="store_true", help="Initialize a populated scenario economy")
    new.add_argument("--stabilized", action="store_true", help="Enable calibrated economy plus finite credit and regional labour")
    advance = sub.add_parser("advance")
    advance.add_argument("db")
    advance.add_argument("amount", type=int)
    advance.add_argument("unit", choices=["day", "week", "month"])
    advance.add_argument("--autosave", default="saves")
    for name in ("status", "ledger", "audit"):
        sub.add_parser(name).add_argument("db")
    save = sub.add_parser("save")
    save.add_argument("db")
    save.add_argument("name")
    save.add_argument("--directory", default="saves")
    load = sub.add_parser("load")
    load.add_argument("snapshot")
    load.add_argument("new_db")
    diff = sub.add_parser("diff")
    diff.add_argument("before")
    diff.add_argument("after")
    order = sub.add_parser("order")
    order.add_argument("db")
    order.add_argument("json_file")
    economy = sub.add_parser("economy-init")
    economy.add_argument("db")
    economy.add_argument("--profile", default="early_industrial", choices=["preindustrial", "early_industrial", "industrial", "advanced_industrial"])
    sub.add_parser("economy-upgrade").add_argument("db")
    sub.add_parser("economy-stabilize").add_argument("db")
    constraints = sub.add_parser("constraints")
    constraints.add_argument("db")
    constraints.add_argument("country")
    constraints.add_argument("amount", type=int)
    mobilize = sub.add_parser("mobilize")
    mobilize.add_argument("db")
    mobilize.add_argument("country")
    mobilize.add_argument("region")
    mobilize.add_argument("amount", type=int)
    explain = sub.add_parser("explain")
    explain.add_argument("db")
    explain.add_argument("country")
    policy = sub.add_parser("policy")
    policy.add_argument("db")
    policy.add_argument("country")
    policy.add_argument("json_file")
    project = sub.add_parser("plan-project")
    project.add_argument("db")
    project.add_argument("project_id")
    project.add_argument("country")
    project.add_argument("region")
    project.add_argument("kind", choices=["infrastructure", "factory", "education", "technology"])
    project.add_argument("target")
    project.add_argument("amount", type=float)
    fund = sub.add_parser("fund-project")
    fund.add_argument("db")
    fund.add_argument("project_id")
    control = sub.add_parser("control-project")
    control.add_argument("db")
    control.add_argument("project_id")
    control.add_argument("action", choices=["cancel", "disrupt", "resume"])
    shock = sub.add_parser("shock")
    shock.add_argument("db")
    shock.add_argument("shock_id")
    shock.add_argument("kind")
    shock.add_argument("region")
    shock.add_argument("severity", type=float)
    shock.add_argument("--days", type=int, default=90)
    args = p.parse_args()
    engine = None
    try:
        if args.command == "diff":
            result = Engine.diff(args.before, args.after)
        else:
            if args.command == "new":
                world = World.from_dict(json.loads(Path(args.scenario).read_text(encoding="utf-8")))
                engine = Engine.create(args.db, world, args.seed)
                if args.economy or args.stabilized: engine.enable_economy()
                if args.stabilized: engine.stabilize_economy()
            elif args.command == "load":
                engine = Engine.load(args.snapshot, args.new_db)
            else:
                engine = Engine(args.db)
            if args.command in ("new", "load", "status"):
                result = {"date": engine.true_state.date, "scenario": engine.true_state.scenario_id,
                          "countries": len(engine.true_state.countries), "ledger_entries": len(engine.ledger())}
            elif args.command == "advance":
                result = {"date": engine.advance(args.amount, args.unit, args.autosave)}
            elif args.command == "save":
                result = {"saved": str(engine.named_snapshot(args.directory, args.name))}
            elif args.command == "ledger":
                result = engine.ledger()
            elif args.command == "audit":
                result = {"issues": engine.auditor.check(engine.true_state), "warnings": engine.auditor.warnings}
            elif args.command == "order":
                result = engine.submit(Order(**json.loads(Path(args.json_file).read_text(encoding="utf-8"))))
            elif args.command == "economy-init":
                engine.enable_economy(args.profile)
                result = {"economy": "enabled", "profile": args.profile}
            elif args.command == "explain": result = engine.explain(args.country)
            elif args.command == "economy-upgrade": result = engine.upgrade_economy()
            elif args.command == "economy-stabilize": result = engine.stabilize_economy()
            elif args.command == "constraints": result = engine.policy_constraints(args.country, args.amount)
            elif args.command == "mobilize": result = engine.mobilize(args.country, args.region, args.amount)
            elif args.command == "policy":
                result = engine.set_policy(args.country, json.loads(Path(args.json_file).read_text(encoding="utf-8")))
            elif args.command == "plan-project":
                result = engine.plan_project(args.project_id, args.country, args.region, args.kind, args.target, args.amount)
            elif args.command == "fund-project":
                engine.fund_project(args.project_id)
                result = engine.true_state.projects[args.project_id]
            elif args.command == "control-project":
                engine.control_project(args.project_id, args.action)
                result = engine.true_state.projects[args.project_id]
            elif args.command == "shock":
                engine.apply_shock(args.shock_id, args.kind, args.region, args.severity, args.days)
                result = {"shock": args.shock_id, "status": "recorded"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
        p.exit(1, f"Error: {exc}\n")
    finally:
        if engine:
            engine.close()


if __name__ == "__main__":
    main()
