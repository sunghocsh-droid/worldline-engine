import calendar
import copy
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

from .audit import Auditor, numeric
from .models import Order, Report, World, RISKS, canonical_date
from .persistence import tuples, validate_engine, validate_snapshot, normalize_engine, SAVE_SCHEMA_VERSION, STABILIZED_SAVE_SCHEMA_VERSION, NPC_SAVE_SCHEMA_VERSION, CRISIS_SAVE_SCHEMA_VERSION, MILITARY_SAVE_SCHEMA_VERSION
from . import state_storage
from .state_runtime import Journal, detached, projection
from .incremental_audit import changed as changed_audit


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Engine:
    """Single-writer engine. Do not give this object or its database to an LLM."""

    def __init__(self, db_path, *, validation_mode='runtime'):
        self.path = Path(db_path).resolve()
        # Opening a misspelled path must not create an empty 'game'.
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)
        self.db.execute("PRAGMA foreign_keys=ON")
        # Validate the existing game before adding backward-compatible ledger guards.
        # An unrelated database must not be silently turned into a partial game.
        try:
            row = self.db.execute("SELECT payload FROM current_state WHERE id=1").fetchone()
            if row is None:
                raise ValueError("Use Engine.create for a new database")
            payload = state_storage.read(self.db, row[0])
            validate_engine(payload)
        except BaseException:
            self.db.close()
            raise
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS current_state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_no_replace BEFORE INSERT ON ledger
              WHEN EXISTS(SELECT 1 FROM ledger WHERE id=NEW.id)
              BEGIN SELECT RAISE(ABORT,'immutable ledger'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS crisis_event_once ON ledger(
              json_extract(payload,'$.kind'), json_extract(payload,'$.event_id'))
              WHERE kind='system_event' AND json_extract(payload,'$.system')='crisis'
                AND json_extract(payload,'$.kind') IN ('event_started','event_resolved');
            CREATE UNIQUE INDEX IF NOT EXISTS crisis_effect_once ON ledger(json_extract(payload,'$.effect_id'))
              WHERE kind='system_event' AND json_extract(payload,'$.system')='crisis'
                AND json_extract(payload,'$.kind')='effect_applied';
        """)
        self.auditor = Auditor()
        state_storage.ensure_schema(self.db)
        self._storage_cache = {}
        self._component_cache = {}
        self.validation_mode = validation_mode
        self._systems = []
        self._system_specs = []
        try:
            self._restore(payload)
            self._register_builtins()
            if self._world.crisis_state is not None and self.db.execute("SELECT 1 FROM ledger LIMIT 1").fetchone():
                # Opening SQLite must check its permanent facts too, even when no
                # JSON save is requested. This is a startup check, not a daily scan.
                validate_snapshot({"schema_version": MILITARY_SAVE_SCHEMA_VERSION if self._world.military_state is not None else CRISIS_SAVE_SCHEMA_VERSION,
                    "engine": payload, "ledger": self.ledger()})
        except BaseException:
            self.db.close()
            raise

    @classmethod
    def create(cls, path, world, seed=0, *, validation_mode='runtime'):
        if type(seed) is not int:
            raise ValueError("Seed must be an integer")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        db = None
        engine = None
        try:
            Auditor().check(world)
            rng = random.Random(seed)
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE current_state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            payload = {"world": world.to_dict(), "seed": seed, "rng": rng.getstate(),
                       "observed": {}, "orders": {}, "journeys": {}}
            if world.crisis_state is not None:
                payload["systems"] = [{"name": name, "version": "1"} for name in
                    ("worldline.projects", "worldline.economy", "worldline.npc", "worldline.crisis")]
                if world.military_state is not None:
                    payload['systems'].append({'name': 'worldline.military', 'version': '1'})
            db.execute("INSERT INTO current_state VALUES(1,?)", (encode(payload),))
            db.commit()
            db.close()
            db = None
            engine = cls(path, validation_mode=validation_mode)
            with engine._transaction():
                engine._event("game_created", {"seed": seed, "scenario": world.scenario_id})
                if world.crisis_state is not None:
                    engine._flush_crisis()
            return engine
        except BaseException:
            if db is not None:
                db.close()
            if engine is not None:
                engine.close()
            path.unlink(missing_ok=True)
            raise

    def close(self):
        self.db.close()

    @property
    def validation_mode(self):
        return self._validation_mode

    @validation_mode.setter
    def validation_mode(self, mode):
        if mode not in ('runtime', 'verification'):
            raise ValueError('validation_mode must be runtime or verification')
        self._validation_mode = mode

    def _audit(self, previous, frame, *, full=False, transaction=False):
        with changed_audit(self._world, previous, frame,
                           self.validation_mode == 'runtime' and not full):
            if transaction:
                self.auditor.check(self._world, history_previous=previous)
            else:
                self.auditor.check(self._world, previous)

    @property
    def true_state(self):
        return World.from_dict(self._stored_payload(field="world"))

    def _stored_payload(self, *, field=None):
        row = self.db.execute("SELECT payload FROM current_state WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Missing committed state")
        journal = getattr(self, '_journal', None)
        if journal is not None and not journal.external_dirty and self._database_unchanged(row[0]):
            frame = self._transaction_frame if self.db.in_transaction and hasattr(self, '_transaction_frame') else None
            if field in ('observed', 'orders', 'journeys'):
                # Select the committed root reference before reconstructing its
                # changed children. A metadata read never projects the World.
                root = journal.root if frame is None else frame.states.get(id(journal.root), (None, journal.root))[1]
                value = root[field] if frame is None else journal.view(root[field], frame)
                return json.loads(encode(value))
            if field in ('seed', 'rng', 'systems'):
                value = {'seed': self._committed_seed, 'rng': self._committed_rng,
                         'systems': self._committed_systems}[field]
                if field == 'systems' and not value:
                    raise KeyError(field)
                return json.loads(encode(value))
            world = journal.view(self._world, frame) if frame is not None else self._world
            if field == 'world':
                return json.loads(encode(projection(world)))
            meta = journal.view(journal.root, frame) if frame is not None else journal.root
            # JSON's C encoder/decoder preserves the original committed-read
            # representation (sorted string keys, tuple arrays, independent
            # dictionaries) without a Python recursive copy of every leaf.
            payload = {'world': projection(world),
                       'seed': self._committed_seed, 'rng': self._committed_rng,
                       **{k: meta[k] for k in ('observed', 'orders', 'journeys')}}
            if self._committed_systems:
                payload['systems'] = self._committed_systems
            payload = json.loads(encode(payload))
        else:
            # Preserve the full physical validation on stale/private/SQL edits.
            payload = state_storage.read(self.db, row[0])
        return payload if field is None else payload[field]

    def _database_unchanged(self, text):
        return (text == self._committed_text
                and self.db.execute('PRAGMA data_version').fetchone()[0] == self._committed_db_version
                and (self.db.total_changes == self._committed_changes or self.db.in_transaction
                     and hasattr(self, '_transaction_frame')))

    @property
    def orders(self):
        return self._stored_payload(field="orders")

    @property
    def observed(self):
        return self._stored_payload(field="observed")

    @property
    def journeys(self):
        return self._stored_payload(field="journeys")

    @property
    def rng(self):
        """Detached inspection copy. Actual draws occur inside engine transactions."""
        rng = random.Random()
        rng.setstate(tuples(self._stored_payload(field="rng")))
        return rng

    @property
    def systems(self):
        return tuple(self._systems)

    def refresh(self):
        if self.db.in_transaction:
            raise ValueError("Cannot refresh during a transaction")
        external = [(fn, spec) for fn, spec in zip(self._systems, self._system_specs)
                    if spec["name"] not in ("worldline.projects", "worldline.economy", "worldline.npc", "worldline.crisis", "worldline.military")]
        self._restore(self._stored_payload())
        self._systems, self._system_specs = [], []
        self._register_builtins()
        for fn, spec in external:
            self.register_system(fn, **spec)

    def _register_builtins(self):
        if self._world.projects or self._world.economy:
            from .projects import tick, VERSION
            self.register_system(tick, name="worldline.projects", version=VERSION)
        if self._world.economy:
            from .economy import tick, VERSION
            self.register_system(tick, name="worldline.economy", version=VERSION)
        if self._world.npc_state is not None:
            from .npc import tick, VERSION
            self.register_system(tick, name="worldline.npc", version=VERSION)
        if self._world.crisis_state is not None:
            from .crisis import tick, VERSION
            self.register_system(tick, name="worldline.crisis", version=VERSION)
        if self._world.military_state is not None:
            from .military import tick, VERSION
            self.register_system(tick, name='worldline.military', version=VERSION)

    def enable_military(self, config=None):
        from .military_state import initialize
        if self._required_systems and self._required_systems != self._system_specs:
            raise ValueError('Register all saved systems before enabling military')
        external = [(fn, spec) for fn, spec in zip(self._systems, self._system_specs)
            if spec['name'] not in ('worldline.projects', 'worldline.economy', 'worldline.npc', 'worldline.crisis', 'worldline.military')]
        required = [{'name': name, 'version': '1'} for name in
            ('worldline.projects', 'worldline.economy', 'worldline.npc', 'worldline.crisis', 'worldline.military')]
        with self._transaction():
            initialize(self._world, self._rng, config)
            self._required_systems = required + [spec for _, spec in external]
            self._event('military_enabled', {'version': 1, 'systems': self._required_systems})
        self._systems, self._system_specs = [], []
        self._register_builtins()
        for fn, spec in external:
            self.register_system(fn, **spec)
        return {'version': 1, 'save_schema_version': MILITARY_SAVE_SCHEMA_VERSION}

    def _military_call(self, module, method, *args, **kwargs):
        from importlib import import_module
        with self._transaction():
            result = getattr(import_module('.' + module, __package__), method)(self._world, *args, **kwargs)
            self._event('military_action', {'module': module, 'method': method, 'args': list(args), 'kwargs': kwargs})
        return copy.deepcopy(result)

    def register_military_actor(self, actor_id, **fields):
        return self._military_call('military_state', 'add_actor', actor_id, **fields)

    def raise_formation(self, formation_id, actor, manpower, region, **fields):
        return self._military_call('military_bridge', 'recruit', formation_id, actor, manpower, region, **fields)

    def demobilize_formation(self, formation_id, amount=None):
        return self._military_call('military_bridge', 'demobilize', formation_id, amount)

    def add_supply_link(self, *args, **kwargs):
        return self._military_call('military_logistics', 'add_link', *args, **kwargs)

    def add_supply_depot(self, *args, **kwargs):
        return self._military_call('military_logistics', 'add_depot', *args, **kwargs)

    def procure_supply(self, *args, **kwargs):
        return self._military_call('military_logistics', 'procure_depot', *args, **kwargs)

    def dispatch_supply(self, *args, **kwargs):
        return self._military_call('military_logistics', 'dispatch', *args, **kwargs)

    def add_transport_convoy(self, *args, **kwargs):
        return self._military_call('military_logistics', 'add_convoy', *args, **kwargs)

    def load_formation_supplies(self, *args, **kwargs):
        return self._military_call('military_logistics', 'transfer_to_formation', *args, **kwargs)

    def produce_military_equipment(self, *args, **kwargs):
        return self._military_call('military_bridge', 'produce_equipment', *args, **kwargs)

    def issue_military_equipment(self, *args, **kwargs):
        return self._military_call('military_bridge', 'issue_equipment', *args, **kwargs)

    def transfer_military_equipment(self, *args, **kwargs):
        return self._military_call('military_bridge', 'transfer_equipment', *args, **kwargs)

    def register_equipment_standard(self, *args, **kwargs):
        return self._military_call('military_innovation', 'register_standard', *args, **kwargs)

    def send_military_report(self, *args, **kwargs):
        return self._military_call('military_operations', 'queue_report', *args, **kwargs)

    def train_replacements(self, *args, **kwargs):
        return self._military_call('military_bridge', 'train_replacements', *args, **kwargs)

    def military_replacement_quote(self, *args, **kwargs):
        from .military_politics import replacement_quote
        return replacement_quote(self.true_state, *args, **kwargs)

    def decide_military_replacements(self, *args, **kwargs):
        return self._military_call('military_politics', 'decide_replacement', *args, **kwargs)

    def incorporate_replacements(self, *args, **kwargs):
        return self._military_call('military_bridge', 'incorporate_replacements', *args, **kwargs)

    def register_fortress(self, *args, **kwargs):
        return self._military_call('military_bridge', 'register_fortress', *args, **kwargs)

    def build_fortress(self, *args, **kwargs):
        return self._military_call('military_bridge', 'build_fortress', *args, **kwargs)

    def delegate_military_authority(self, *args, **kwargs):
        return self._military_call('military_bridge', 'delegate', *args, **kwargs)

    def plan_operation(self, *args, **kwargs):
        return self._military_call('military_operations', 'plan', *args, **kwargs)

    def command_operation(self, *args, **kwargs):
        return self._military_call('military_operations', 'send_command', *args, **kwargs)

    def propose_military_innovation(self, *args, **kwargs):
        return self._military_call('military_innovation', 'propose', *args, **kwargs)

    def advance_military_innovation(self, *args, **kwargs):
        return self._military_call('military_innovation', 'begin_stage', *args, **kwargs)

    def military_constraints(self, *args, **kwargs):
        from .military import constraints
        return constraints(self.true_state, *args, **kwargs)

    def enable_npc(self, config=None):
        from .npc import initialize, tick
        if self._required_systems and self._required_systems != self._system_specs:
            raise ValueError("Register all saved systems before enabling NPC decisions")
        external = [(fn, spec) for fn, spec in zip(self._systems, self._system_specs)
                    if spec["name"] not in ("worldline.projects", "worldline.economy", "worldline.npc", "worldline.crisis", "worldline.military")]
        specs = [{"name": "worldline.projects", "version": "1"},
                 {"name": "worldline.economy", "version": "1"},
                 {"name": "worldline.npc", "version": "1"}]
        with self._transaction():
            initialize(self._world, config)
            self._required_systems = specs + [copy.deepcopy(spec) for _, spec in external]
            self._event("npc_enabled", {"version": 1, "systems": self._required_systems})
        from .projects import tick as project_tick
        from .economy import tick as economy_tick
        self._systems = [project_tick, economy_tick, tick] + [fn for fn, _ in external]
        self._system_specs = specs + [spec for _, spec in external]
        return {"version": 1, "save_schema_version": NPC_SAVE_SCHEMA_VERSION}

    def enable_crisis(self, config=None):
        from .crisis import initialize, tick, VERSION
        if self._required_systems and self._required_systems != self._system_specs:
            raise ValueError("Register all saved systems before enabling crises")
        external = [(fn, spec) for fn, spec in zip(self._systems, self._system_specs)
                    if spec["name"] not in ("worldline.projects", "worldline.economy", "worldline.npc", "worldline.crisis", "worldline.military")]
        specs = [{"name": "worldline.projects", "version": "1"},
                 {"name": "worldline.economy", "version": "1"},
                 {"name": "worldline.npc", "version": "1"},
                 {"name": "worldline.crisis", "version": VERSION}]
        with self._transaction():
            initialize(self._world, config, self._rng)
            self._required_systems = specs + [spec for _, spec in external]
            self._event("crisis_enabled", {"version": 1, "systems": self._required_systems})
            self._flush_crisis()
        self._systems, self._system_specs = [], []
        self._register_builtins()
        for fn, spec in external:
            self.register_system(fn, **spec)
        return {"version": 1, "save_schema_version": CRISIS_SAVE_SCHEMA_VERSION}

    def _flush_crisis(self):
        from .crisis import drain_outbox
        for event in drain_outbox(self._world):
            self._event("system_event", event)

    def set_crisis_pressure(self, category, value, **scope):
        from .crisis_state import set_pressure
        with self._transaction():
            result = set_pressure(self._world, category, value, **scope)
            self._event("crisis_pressure_changed", result)
        return copy.deepcopy(result)

    def inject_event(self, event_type, region=None, countries=(), **fields):
        """Trusted scenario/event outcome hook; actors cannot write hidden truth."""
        from .crisis import create_event
        with self._transaction():
            event = create_event(self._world, event_type, region=region, countries=countries, **fields)
            self._flush_crisis()
        return copy.deepcopy(event)

    def respond_to_event(self, event_id, country, response, *, actor=None):
        from .crisis_response import respond
        with self._transaction():
            if self._world.crisis_state is None:
                raise ValueError("Enable Crisis Engine first")
            player = country in self._world.crisis_state["player_countries"]
            result = respond(self._world, event_id, country, response, actor=actor, player=player)
            self._event("crisis_response_order", result)
            for event in result.get("events", []):
                self._event("system_event", event)
            self._flush_crisis()
        return copy.deepcopy(result)

    def send_event_report(self, event_id, actor, **delivery):
        from .crisis_response import queue_report
        with self._transaction():
            result = queue_report(self._world, event_id, actor, **delivery)
            self._event("crisis_report_sent", result)
            self._flush_crisis()
        return copy.deepcopy(result)

    def pending_decisions(self, country):
        world = self.true_state
        if world.crisis_state is None:
            return []
        if country not in world.crisis_state["player_countries"]:
            raise ValueError("Country is not controlled by the player")
        return [copy.deepcopy(p) for p in world.crisis_state["pending_decisions"].values()
                if p["country"] == country and p["status"] == "pending"]

    def npc_report(self, actor, topic, data, *, as_of=None, source="external report", **metadata):
        from .npc_state import receive_report
        with self._transaction():
            if self._world.npc_state is None:
                raise ValueError("Enable NPC decision engine first")
            receive_report(self._world.npc_state, actor, topic, data, as_of or self._world.date,
                           source, **metadata)
            self._world.npc_state["adapter"]["pending"].setdefault(actor, []).append(topic)
            self._event("npc_report_received", {"actor": actor, "topic": topic,
                "report": copy.deepcopy(self._world.npc_state["reports"][actor][topic])})

    def npc_outcome(self, subject, category, facts, **delivery):
        from .npc import record_outcome
        with self._transaction():
            event = record_outcome(self._world, subject, category, facts, **delivery)
            self._event("npc_outcome_recorded", event)
        return copy.deepcopy(event)

    def npc_view(self, actor):
        from .npc_state import actor_view
        world = self.true_state
        if world.npc_state is None:
            raise ValueError("Enable NPC decision engine first")
        return actor_view(world.npc_state, actor, world.date)

    def npc_execute(self, actor, option):
        from .npc import execute_intent
        with self._transaction():
            result = execute_intent(self._world, actor, copy.deepcopy(option))
            self._event("npc_intent_executed", result)
        return copy.deepcopy(result)

    def enable_economy(self, profile="early_industrial", region_overrides=None, calibration=None):
        from .economy import initialize, tick as economy_tick
        from .projects import tick as project_tick
        external = [(fn, spec) for fn, spec in zip(self._systems, self._system_specs)
                    if spec["name"] not in ("worldline.projects", "worldline.economy")]
        if self._required_systems and self._required_systems != self._system_specs:
            raise ValueError("Register all saved systems before enabling economy")
        specs = [{"name": "worldline.projects", "version": "1"}, {"name": "worldline.economy", "version": "1"}]
        with self._transaction():
            initialize(self._world, profile, region_overrides, calibration)
            self._required_systems = specs + [copy.deepcopy(spec) for _, spec in external]
            self._event("economy_enabled", {"profile": profile, "systems": self._required_systems})
        self._systems = [project_tick, economy_tick] + [fn for fn, _ in external]
        self._system_specs = specs + [spec for _, spec in external]

    def set_policy(self, country, changes, order_id=None):
        return self.submit(Order(order_id or f"policy-{len(self._orders)+1}", country, "economic_policy", changes))

    def stabilize_economy(self):
        from .economy import enable_stabilization
        with self._transaction():
            enable_stabilization(self._world)
            self._event("economic_stabilization_enabled", {"stabilization_version": 1,
                "effective_after": self._world.date, "preserved": "assets, debt, projects, population and RNG"})
        return {"stabilization_version": 1}

    def policy_constraints(self, country, amount=0, *, project_id=None, perceived=None, **kwargs):
        from .guardrails import can_afford_project, estimated_fiscal_risk
        world = self.true_state
        if project_id is not None:
            if world.projects[project_id]["country"] != country: raise ValueError("Project belongs to another country")
            return can_afford_project(world, project_id, perceived=perceived, **kwargs)
        return estimated_fiscal_risk(world, country, amount, perceived=perceived, **kwargs)

    def mobilize(self, country, region, amount, order_id=None):
        return self.submit(Order(order_id or f"mobilization-{len(self._orders)+1}", country,
                                 "mobilize", {"region": region, "amount": amount}))

    def fiscal_action(self, country, amount, *, purpose="general", allow_emergency=False,
                      instruments=None, order_id=None):
        return self.submit(Order(order_id or f"fiscal-action-{len(self._orders)+1}", country, "fiscal_action",
            {"amount": amount, "purpose": purpose, "allow_emergency": allow_emergency, "instruments": instruments}))

    def repay_debt(self, country, amount, order_id=None):
        return self.submit(Order(order_id or f"repayment-{len(self._orders)+1}", country, "repay_debt", {"amount": amount}))

    def restructure_debt(self, country, order_id=None):
        return self.submit(Order(order_id or f"restructure-{len(self._orders)+1}", country, "restructure_debt", {}))

    def upgrade_economy(self):
        """Explicit operator migration; loading never reinterprets legacy rules."""
        from .calibration import MODEL_PARAMETERS
        with self._transaction():
            economy = self._world.economy
            if not economy or economy["version"] != 1:
                raise ValueError("Upgrade requires a legacy version 1 economy")
            economy.update(version=2, model_revision=2, model_parameters=dict(MODEL_PARAMETERS))
            self._event("economic_model_upgraded", {"from": 1, "to": 2, "effective_after": self._world.date,
                "preserved": "population, capital, debt, reserves, existing projects and RNG"})
        return {"version": 2, "model_revision": 2}

    def apply_shock(self, shock_id, kind, region, severity, duration_days=90):
        from .economy import add_shock
        with self._transaction():
            add_shock(self._world, shock_id, kind, region, severity, duration_days)
            self._event("economic_shock_received", copy.deepcopy(self._world.economy["shocks"][shock_id]))

    def add_trade_route(self, trade_id, exporter, importer, sector, monthly_quantity, mode="land"):
        from .economy import add_trade
        with self._transaction():
            add_trade(self._world, trade_id, exporter, importer, sector, monthly_quantity, mode)
            self._event("trade_route_added", copy.deepcopy(self._world.economy["trades"][trade_id]))

    def explain(self, country):
        economy = self.true_state.economy
        if not economy or country not in economy["countries"]:
            raise ValueError("Country has no economy")
        return copy.deepcopy(economy["countries"][country]["report"])

    def _enable_project_system(self):
        if any(s["name"] == "worldline.projects" for s in self._system_specs):
            return
        from .projects import tick, VERSION
        self._systems.insert(0, tick)
        self._system_specs.insert(0, {"name": "worldline.projects", "version": VERSION})

    def plan_project(self, project_id, country, region, kind, target, amount):
        from .projects import plan
        with self._transaction():
            p = plan(self._world, project_id, country, region, kind, target, amount)
            if self._required_systems and not any(s["name"] == "worldline.projects" for s in self._required_systems):
                self._required_systems.insert(0, {"name": "worldline.projects", "version": "1"})
            self._event("project_planned", p)
        self._enable_project_system()
        return copy.deepcopy(p)

    def fund_project(self, project_id, *, allow_emergency=False, instruments=None):
        from .projects import fund
        with self._transaction():
            fund(self._world, self._world.projects[project_id], allow_emergency=allow_emergency, instruments=instruments)
            self._event("project_funded", {"project": project_id})

    def control_project(self, project_id, action):
        with self._transaction():
            p = self._world.projects[project_id]
            if p["status"] in ("completed", "cancelled"):
                raise ValueError("Project is terminal")
            if action == "cancel": p["status"] = "cancelled"
            elif action == "disrupt":
                p["disruption"] = p["status"]
                p["status"] = "disrupted"
            elif action == "resume" and p["status"] == "disrupted":
                p["status"], p["disruption"] = p["disruption"], None
                p.pop("shock_id", None)
            else: raise ValueError("Invalid project action")
            self._event("project_controlled", {"project": project_id, "action": action})

    def _payload(self):
        payload = {"world": self._world.to_dict(), "seed": self.seed, "rng": self._rng.getstate(),
                   "observed": self._observed, "orders": self._orders, "journeys": self._journeys}
        if self._required_systems:
            payload["systems"] = copy.deepcopy(self._required_systems)
        return payload

    def _restore(self, payload):
        self._world = validate_engine(payload)
        self.seed = payload["seed"]
        self._rng = random.Random()
        self._rng.setstate(tuples(payload["rng"]))
        self._observed = copy.deepcopy(payload["observed"])
        self._orders = copy.deepcopy(payload["orders"])
        self._journeys = copy.deepcopy(payload["journeys"])
        self._required_systems = copy.deepcopy(payload.get("systems", []))
        self._journal = Journal({'world': self._world, 'observed': self._observed,
                                 'orders': self._orders, 'journeys': self._journeys})
        self._observed, self._orders, self._journeys = (self._journal.root[k] for k in ('observed', 'orders', 'journeys'))
        self._committed_text = self.db.execute('SELECT payload FROM current_state WHERE id=1').fetchone()[0]
        self._committed_rng = self._rng.getstate()
        self._committed_seed = self.seed
        self._committed_systems = copy.deepcopy(self._required_systems)
        self._committed_db_version = self.db.execute('PRAGMA data_version').fetchone()[0]
        self._committed_changes = self.db.total_changes
        self._revision = json.loads(self._committed_text).get('revision', 0)
        if hasattr(self, '_component_cache'):
            self._component_cache.clear()
            self._storage_cache.clear()

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        if self._journal.frames:
            self.db.rollback()
            raise ValueError('Nested engine transactions are unsupported')
        frame = self._journal.begin()
        self._transaction_frame = frame
        seed_before = self.seed
        rng_before, systems_before = self._rng.getstate(), copy.deepcopy(self._required_systems)
        try:
            text = self.db.execute('SELECT payload FROM current_state WHERE id=1').fetchone()[0]
            metadata_changed = (self.seed != self._committed_seed or self._rng.getstate() != self._committed_rng
                                or self._required_systems != self._committed_systems)
            if self._journal.external_dirty or metadata_changed or not self._database_unchanged(text) or self.db.total_changes != self._committed_changes:
                if encode(normalize_engine(state_storage.read(self.db, text))) != encode(self._payload()):
                    raise ValueError("Database changed by another writer; reopen the engine")
                self._journal.external_dirty = False
            yield
            if self._world.military_state is not None:
                from .military import publish
                for event in publish(self._world):
                    self._event('system_event', event)
            previous = self._journal.view(self._world, frame)
            before = {'world': projection(previous)}
            if self._world.economy and before["world"].get("economy"):
                if self._world.economy["last_settlement_date"] == before["world"]["economy"]["last_settlement_date"]:
                    for country, economy in self._world.economy["countries"].items():
                        finance = economy["finance"]
                        delta = self._world.countries[country].treasury - before["world"]["countries"][country]["treasury"]
                        prior_finance = before["world"]["economy"]["countries"][country]["finance"]
                        booked = finance.get("interim_net_cashflow", 0) - prior_finance.get("interim_net_cashflow", 0)
                        finance["interim_net_cashflow"] = finance.get("interim_net_cashflow", 0) + delta - booked
                        finance["reserves"] = self._world.countries[country].treasury
                        if self._world.economy.get("stabilization_version") == 1:
                            debt_delta = self._world.countries[country].debt - before["world"]["countries"][country]["debt"]
                            booked_debt = finance.get("interim_debt_change", 0) - prior_finance.get("interim_debt_change", 0)
                            finance["interim_debt_change"] = finance.get("interim_debt_change", 0) + debt_delta - booked_debt
            self._journal.barrier()
            previous = self._journal.view(self._world, frame)
            self._audit(previous, frame, transaction=True)
            for key, value in previous.canon.items():
                if key not in self._world.canon or self._world.canon[key] != value:
                    from .audit import AuditError
                    raise AuditError([f"canon: conflict {key}"])
            self._journal.barrier()
            after = {'world': projection(self._world), 'seed': self.seed, 'rng': self._rng.getstate(),
                     'observed': self._observed, 'orders': self._orders, 'journeys': self._journeys}
            if self._required_systems:
                after['systems'] = self._required_systems
            # The candidate World has just passed the existing invariants;
            # validate resumable metadata without auditing that World again.
            validate_engine(after, audit=False)
            physical = state_storage.compact(self.db, after, self._storage_cache)
            physical = state_storage.write_components(self.db, physical, after, self._component_cache, self._revision + 1)
            text = encode(physical)
            self.db.execute("UPDATE current_state SET payload=? WHERE id=1", (text,))
            db_version = self.db.execute('PRAGMA data_version').fetchone()[0]
            self.db.commit()
            self._journal.end(frame)
            self._committed_text, self._committed_rng = text, self._rng.getstate()
            self._committed_seed = self.seed
            self._committed_systems = copy.deepcopy(self._required_systems)
            self._committed_db_version, self._committed_changes = db_version, self.db.total_changes
            self._revision += 1
        except BaseException:
            self.db.rollback()
            self._storage_cache.clear()
            self._component_cache.clear()
            while self._journal.frames and self._journal.frames[-1] is not frame:
                self._journal.end(self._journal.frames[-1], rollback=True)
            self._journal.end(frame, rollback=True)
            self._rng.setstate(rng_before)
            self.seed = seed_before
            self._required_systems = systems_before
            self._committed_changes = self.db.total_changes
            raise
        finally:
            del self._transaction_frame

    def _event(self, kind, payload):
        if not isinstance(kind, str) or not kind or not isinstance(payload, dict):
            raise ValueError("Ledger events require a kind and a JSON object payload")
        self.db.execute("INSERT INTO ledger(day,kind,payload) VALUES(?,?,?)",
                        (self._world.date, kind, encode(payload)))

    def ledger(self):
        return [{"id": i, "day": d, "kind": k, "payload": json.loads(p)}
                for i, d, k, p in self.db.execute("SELECT * FROM ledger ORDER BY id")]

    def historical_state(self, *, kind, owner=None, item=None):
        """Trusted detached archive inspection, separate from NPC perception."""
        return state_storage.history(self.db, kind=kind, owner=owner, item=item)

    def register_system(self, callback, *, name=None, version="1"):
        """callback(world, tick, rng) -> JSON-compatible event list; no external side effects."""
        if not callable(callback):
            raise ValueError("System callback must be callable")
        module = getattr(callback, "__module__", type(callback).__module__)
        qualified_name = getattr(callback, "__qualname__", type(callback).__qualname__)
        name = name or f"{module}.{qualified_name}:{len(self._systems)}"
        spec = {"name": name, "version": version}
        if not all(isinstance(v, str) and v for v in spec.values()) or any(s["name"] == name for s in self._system_specs):
            raise ValueError("Invalid or duplicate system name/version")
        if self._required_systems:
            index = len(self._systems)
            if index >= len(self._required_systems) or self._required_systems[index] != spec:
                raise ValueError("Registered system differs from saved system order/version")
        self._systems.append(callback)
        self._system_specs.append(spec)

    def advance(self, amount=1, unit="day", autosave_dir=None):
        if type(amount) is not int or amount <= 0:
            raise ValueError("Time amount must be a positive integer")
        start = date.fromisoformat(self._world.date)
        if unit in ("day", "week"):
            target = start + timedelta(days=amount * (7 if unit == "week" else 1))
        elif unit == "month":
            month = start.year * 12 + start.month - 1 + amount
            year, month = divmod(month, 12)
            target = date(year, month + 1, min(start.day, calendar.monthrange(year, month + 1)[1]))
        else:
            raise ValueError("unit must be day, week, or month")
        with self._transaction():
            if self._required_systems and self._required_systems != self._system_specs:
                raise ValueError("Required saved systems must be registered before time advances")
            self._required_systems = copy.deepcopy(self._system_specs)
            while date.fromisoformat(self._world.date) < target:
                daily_frame = self._journal.begin()
                previous_date = self._world.date
                self._world.date = (date.fromisoformat(self._world.date) + timedelta(days=1)).isoformat()
                self._world.production.clear()
                for army, journey in list(self._journeys.items()):
                    journey["remaining"] -= 1
                    if journey["remaining"] == 0:
                        # Validate the whole journey against its departure, rather than only its final day.
                        a = self._world.armies[army]
                        a.region = journey["destination"]
                        self._event("army_arrived", {"army": army, **journey})
                        del self._journeys[army]
                tick = {"previous_date": previous_date, "date": self._world.date, "days": 1}
                for system in self._systems:
                    for event in system(self._world, copy.deepcopy(tick), self._rng) or []:
                        self._event("system_event", event)
                    if self._world.date != tick["date"]:
                        raise ValueError("Only the game clock may change the world date")
                for key, p in sorted(self._world.pressures.items()):
                    # Explicit MVP coefficient: maximum daily probability is 1%.
                    probability = 0.01 * (p["value"] / 100) ** 2
                    roll = self._rng.random()
                    if roll < probability:
                        self._event("risk_signal", {"pressure": key, "risk": p["risk"],
                                    "probability": probability, "roll": roll})
                self._journal.barrier()
                previous = self._journal.view(self._world, daily_frame)
                for army, state in daily_frame.states.get(id(self._journeys), (None, {}))[1].items():
                    if army not in self._journeys:
                        # The legacy scheduler separately validates departure duration.
                        previous.armies[army].region = self._world.armies[army].region
                self._audit(previous, daily_frame, full=self._world.date.endswith('-01'))
                self._journal.end(daily_frame)
                self._event("day_advanced", tick)
        if autosave_dir is not None:
            self.save(Path(autosave_dir) / "autosave.json", overwrite=True)
        return self._world.date

    def set_pressure(self, country, risk, value, causes=None):
        if risk not in RISKS:
            raise ValueError("Unknown risk")
        causes = copy.deepcopy(causes or [])
        if not isinstance(causes, list) or not all(isinstance(cause, str) for cause in causes):
            raise ValueError("Pressure causes must be a list of strings")
        with self._transaction():
            self._world.pressures[f"{country}:{risk}"] = {
                "country": country, "risk": risk, "value": value, "causes": causes}
            self.auditor.check(self._world)
            self._event("pressure_changed", {"country": country, "risk": risk, "value": value, "causes": causes or []})

    def record_observation(self, player, key, report: Report):
        canonical_date(report.as_of)
        if report.as_of > self._world.date or not numeric(report.confidence, 0, 1):
            raise ValueError("Invalid report date/confidence")
        with self._transaction():
            from dataclasses import asdict
            self._observed.setdefault(player, {})[key] = asdict(report)
            self._event("report_received", {"player": player, "key": key, "report": asdict(report)})

    def observed_state(self, player):
        return self._stored_payload()["observed"].get(player, {})

    def submit(self, order: Order):
        if not isinstance(order.id, str) or not order.id:
            raise ValueError("Order requires an ID")
        order = copy.deepcopy(order)
        if order.id in self._orders:
            raise ValueError("Duplicate order ID")
        from dataclasses import asdict
        with self._transaction():
            try:
                cost, operation = self._validate_order(order)
            except (ValueError, KeyError, TypeError) as exc:
                result = {"status": "rejected", "reason": str(exc), "order": asdict(order)}
            else:
                self._world.countries[order.actor].treasury -= cost
                operation()
                result = {"status": "accepted", "cost": cost, "order": asdict(order)}
            self._orders[order.id] = result
            self._event("order_decided", result)
            if self._world.npc_state is not None:
                from .npc import ingest_player_order, react_to_order
                for event in ingest_player_order(self._world, result) + react_to_order(self._world, result):
                    self._event("system_event", event)
        if order.kind == "plan_project" and result["status"] == "accepted":
            self._enable_project_system()
        return copy.deepcopy(result)

    def _validate_order(self, o):
        from .order_execution import validate_order
        return validate_order(self._world, o, self._journeys, self._required_systems, self._event)

    def save(self, path, overwrite=False):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.db.in_transaction:
            raise ValueError("Cannot save an uncommitted engine transaction")
        # One SQLite read transaction prevents state/ledger from different revisions mixing.
        self.db.execute("BEGIN")
        try:
            current = self._stored_payload()
            stabilized = current["world"].get("economy") and current["world"]["economy"].get("stabilization_version") == 1
            npc_enabled = current["world"].get("npc_state") is not None
            crisis_enabled = current["world"].get("crisis_state") is not None
            military_enabled = current['world'].get('military_state') is not None
            payload = {"schema_version": MILITARY_SAVE_SCHEMA_VERSION if military_enabled else (CRISIS_SAVE_SCHEMA_VERSION if crisis_enabled else (NPC_SAVE_SCHEMA_VERSION if npc_enabled else (STABILIZED_SAVE_SCHEMA_VERSION if stabilized else SAVE_SCHEMA_VERSION))),
                       "engine": current, "ledger": self.ledger()}
            archive = state_storage.export_archive(self.db)
            if archive is not None:
                payload["state_archive"] = archive
            payload = validate_snapshot(payload)
        finally:
            self.db.rollback()
        body = encode(payload)
        snapshot = {"payload": payload, "sha256": hashlib.sha256(body.encode()).hexdigest()}
        text = json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False)
        # Publish only complete files, using a unique temporary file per attempt.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if overwrite:
                os.replace(temporary, path)
            else:
                # Creating the final hard link is atomic and cannot replace an existing name.
                os.link(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path

    def named_snapshot(self, directory, name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("Snapshot name must use letters, digits, hyphen or underscore")
        return self.save(Path(directory) / f"{name}.json")

    @staticmethod
    def _read_snapshot(path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        payload = raw["payload"]
        if hashlib.sha256(encode(payload).encode()).hexdigest() != raw["sha256"]:
            raise ValueError("Unsupported or damaged snapshot")
        return validate_snapshot(payload)

    @classmethod
    def load(cls, snapshot, new_db, *, validation_mode='runtime'):
        payload = cls._read_snapshot(snapshot)
        path = Path(new_db)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        db = sqlite3.connect(path)
        engine = None
        try:
            db.executescript("""
                CREATE TABLE current_state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE ledger(id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            state_storage.ensure_schema(db)
            with db:
                if "state_archive" in payload:
                    state_storage.import_archive(db, payload["state_archive"])
                for event in payload["ledger"]:
                    db.execute("INSERT INTO ledger VALUES(?,?,?,?)", (event["id"], event["day"], event["kind"], encode(event["payload"])))
                db.execute("INSERT INTO current_state VALUES(1,?)", (encode(payload["engine"]),))
            db.close()
            db = None
            engine = cls(path, validation_mode=validation_mode)
            return engine
        except BaseException:
            if db is not None:
                db.close()
            if engine is not None:
                engine.close()
            path.unlink(missing_ok=True)
            raise

    @classmethod
    def diff(cls, before, after):
        a, b = cls._read_snapshot(before), cls._read_snapshot(after)
        changes = []
        def walk(x, y, path):
            if isinstance(x, dict) and isinstance(y, dict):
                for key in sorted(set(x) | set(y)):
                    if key not in x or key not in y:
                        changes.append({"path": f"{path}/{key}", "before": x.get(key), "after": y.get(key)})
                    else:
                        walk(x[key], y[key], f"{path}/{key}")
            elif x != y:
                changes.append({"path": path, "before": x, "after": y})
        walk(a, b, "")
        return changes
