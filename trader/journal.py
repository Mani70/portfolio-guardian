"""Everything the engine decides or does is written here first (SQLite, standard library only).

Tables: signals, orders, positions, trades, plans, events. Paper and live rows are kept apart by
the `mode` column, so a paper record can be judged on its own before any money is used.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from .models import Order, OrderRequest, Position, Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (id TEXT, mode TEXT, time TEXT, strategy TEXT, symbol TEXT, side TEXT,
    kind TEXT, strength REAL, ref_price REAL, stop REAL, target REAL, product TEXT, reason TEXT,
    acted INTEGER, note TEXT, PRIMARY KEY (id, mode));
CREATE TABLE IF NOT EXISTS orders (tag TEXT, mode TEXT, created TEXT, strategy TEXT, symbol TEXT,
    security_id TEXT, side TEXT, qty INTEGER, product TEXT, kind TEXT, limit_price REAL, stop REAL, target REAL,
    ref_price REAL, amo INTEGER, signal_id TEXT, broker_id TEXT, child_id TEXT, status TEXT, filled_qty INTEGER,
    avg_price REAL, charges REAL, message TEXT, updated TEXT, PRIMARY KEY (tag, mode));
CREATE TABLE IF NOT EXISTS positions (mode TEXT, strategy TEXT, symbol TEXT, product TEXT, qty INTEGER,
    avg_price REAL, entry_time TEXT, entry_tag TEXT, security_id TEXT, stop REAL, target REAL,
    entry_charges REAL, child_id TEXT, meta TEXT, PRIMARY KEY (mode, strategy, symbol, product));
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, strategy TEXT, symbol TEXT,
    product TEXT, side TEXT, qty INTEGER, entry_time TEXT, entry_px REAL, exit_time TEXT, exit_px REAL,
    gross REAL, charges REAL, net REAL, ret_pct REAL, entry_tag TEXT, exit_tag TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS plans (tag TEXT, mode TEXT, for_date TEXT, created TEXT, body TEXT,
    status TEXT, PRIMARY KEY (tag, mode));
CREATE TABLE IF NOT EXISTS events (time TEXT, mode TEXT, level TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS kv (mode TEXT, key TEXT, value TEXT, PRIMARY KEY (mode, key));
"""


class Journal:
    def __init__(self, path: Path, mode: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        # intraday and swing-check can run at the same time (separate processes): wait for the other's lock
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------- signals ----------
    def record_signal(self, s: Signal, acted: bool, note: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (s.id, self.mode, s.time.isoformat(), s.strategy, s.symbol, s.side, s.kind, s.strength,
                         s.ref_price, s.stop, s.target, s.product, s.reason, int(acted), note))
        self.db.commit()

    def signal_seen(self, signal_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM signals WHERE id=? AND mode=?", (signal_id, self.mode)).fetchone() is not None

    # ---------- orders ----------
    def save_order(self, o: Order) -> None:
        r = o.req
        self.db.execute("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r.tag, self.mode, r.created.isoformat(), r.strategy, r.symbol, r.security_id, r.side, r.qty,
                         r.product, r.kind, r.limit_price, r.stop, r.target, r.ref_price, int(r.amo), r.signal_id,
                         o.broker_id,
                         o.child_id, o.status, o.filled_qty, o.avg_price, o.charges, o.message,
                         (o.updated or r.created).isoformat()))
        self.db.commit()

    def order_exists(self, tag: str) -> bool:
        return self.db.execute("SELECT 1 FROM orders WHERE tag=? AND mode=?", (tag, self.mode)).fetchone() is not None

    def active_orders(self, strategy: str = None, symbol: str = None, kind: str = None, product: str = None):
        from .models import ACTIVE
        out = []
        for o in self.load_orders(statuses=ACTIVE):
            r = o.req
            if (strategy and r.strategy != strategy) or (symbol and r.symbol != symbol) or \
                    (kind and r.kind != kind) or (product and r.product != product):
                continue
            out.append(o)
        return out

    def entry_sent_today(self, strategy: str, symbol: str, product: str, day: date) -> bool:
        """An entry for this strategy/symbol was already sent today (any state except rejected)."""
        return self.db.execute("SELECT 1 FROM orders WHERE mode=? AND strategy=? AND symbol=? AND product=? "
                               "AND kind='entry' AND status!='REJECTED' AND substr(created,1,10)=?",
                               (self.mode, strategy, symbol, product, day.isoformat())).fetchone() is not None

    def order(self, tag: str) -> Optional[Order]:
        got = self.load_orders(tag=tag)
        return got[0] if got else None

    def load_orders(self, statuses=None, day: Optional[date] = None, tag: Optional[str] = None) -> List[Order]:
        q, args = "SELECT * FROM orders WHERE mode=?", [self.mode]
        if tag:
            q += " AND tag=?"
            args.append(tag)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        if day:
            q += " AND substr(created,1,10)=?"
            args.append(day.isoformat())
        out = []
        for row in self.db.execute(q, args):
            req = OrderRequest(tag=row["tag"], strategy=row["strategy"], symbol=row["symbol"],
                               security_id=row["security_id"], side=row["side"], qty=row["qty"],
                               product=row["product"], limit_price=row["limit_price"], signal_id=row["signal_id"],
                               kind=row["kind"], created=datetime.fromisoformat(row["created"]), stop=row["stop"],
                               target=row["target"], ref_price=row["ref_price"] or 0.0, amo=bool(row["amo"]))
            out.append(Order(req=req, status=row["status"], broker_id=row["broker_id"] or "",
                             child_id=row["child_id"] or "", filled_qty=row["filled_qty"] or 0,
                             avg_price=row["avg_price"] or 0.0, charges=row["charges"] or 0.0,
                             message=row["message"] or "",
                             updated=datetime.fromisoformat(row["updated"]) if row["updated"] else None))
        return out

    def orders_today(self, day: date) -> int:
        return self.db.execute("SELECT COUNT(*) FROM orders WHERE mode=? AND substr(created,1,10)=?",
                               (self.mode, day.isoformat())).fetchone()[0]

    # ---------- positions ----------
    def save_position(self, p: Position) -> None:
        self.db.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.mode, p.strategy, p.symbol, p.product, p.qty, p.avg_price, p.entry_time.isoformat(),
                         p.entry_tag, p.security_id, p.stop, p.target, p.entry_charges, p.child_id,
                         json.dumps(p.meta)))
        self.db.commit()

    def update_position_meta(self, p: Position) -> None:
        """Change only the position's meta (another process may be recording a fill for it)."""
        self.db.execute("UPDATE positions SET meta=? WHERE mode=? AND strategy=? AND symbol=? AND product=?",
                        (json.dumps(p.meta), self.mode, p.strategy, p.symbol, p.product))
        self.db.commit()

    def delete_position(self, p: Position) -> None:
        self.db.execute("DELETE FROM positions WHERE mode=? AND strategy=? AND symbol=? AND product=?",
                        (self.mode, p.strategy, p.symbol, p.product))
        self.db.commit()

    def positions(self, product: Optional[str] = None, strategy: Optional[str] = None) -> List[Position]:
        q, args = "SELECT * FROM positions WHERE mode=?", [self.mode]
        if product:
            q += " AND product=?"
            args.append(product)
        if strategy:
            q += " AND strategy=?"
            args.append(strategy)
        return [Position(strategy=r["strategy"], symbol=r["symbol"], product=r["product"], qty=r["qty"],
                         avg_price=r["avg_price"], entry_time=datetime.fromisoformat(r["entry_time"]),
                         entry_tag=r["entry_tag"], security_id=r["security_id"] or "", stop=r["stop"],
                         target=r["target"], entry_charges=r["entry_charges"] or 0.0, child_id=r["child_id"] or "",
                         meta=json.loads(r["meta"] or "{}"))
                for r in self.db.execute(q, args)]

    # ---------- trades ----------
    def record_trade(self, p: Position, qty: int, exit_px: float, exit_time: datetime, exit_charges: float,
                     exit_tag: str, reason: str) -> float:
        sign = 1 if p.qty > 0 else -1
        entry_charges = p.entry_charges * qty / abs(p.qty) if p.qty else 0.0
        gross = (exit_px - p.avg_price) * qty * sign
        charges = entry_charges + exit_charges
        net = gross - charges
        invested = p.avg_price * qty
        self.db.execute("INSERT INTO trades (mode,strategy,symbol,product,side,qty,entry_time,entry_px,exit_time,"
                        "exit_px,gross,charges,net,ret_pct,entry_tag,exit_tag,reason) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (self.mode, p.strategy, p.symbol, p.product, p.side, qty, p.entry_time.isoformat(),
                         p.avg_price, exit_time.isoformat(), exit_px, gross, charges, net,
                         net / invested * 100 if invested else 0.0, p.entry_tag, exit_tag, reason))
        self.db.commit()
        return net

    def trades(self, strategy: Optional[str] = None, mode: Optional[str] = None):
        import pandas as pd
        q, args = "SELECT * FROM trades WHERE mode=?", [mode or self.mode]
        if strategy:
            q += " AND strategy=?"
            args.append(strategy)
        return pd.read_sql_query(q, self.db, params=args)

    def realized_today(self, day: date, product: Optional[str] = None, strategy: Optional[str] = None) -> float:
        q, args = "SELECT COALESCE(SUM(net),0) FROM trades WHERE mode=? AND substr(exit_time,1,10)=?", [self.mode, day.isoformat()]
        if product:
            q += " AND product=?"
            args.append(product)
        if strategy:
            q += " AND strategy=?"
            args.append(strategy)
        return float(self.db.execute(q, args).fetchone()[0])

    def realized_total(self, strategy: str) -> float:
        return float(self.db.execute("SELECT COALESCE(SUM(net),0) FROM trades WHERE mode=? AND strategy=?",
                                     (self.mode, strategy)).fetchone()[0])

    def entries_today(self, day: date, product: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM orders WHERE mode=? AND product=? AND kind='entry' "
                               "AND status NOT IN ('REJECTED') AND substr(created,1,10)=?",
                               (self.mode, product, day.isoformat())).fetchone()[0]

    # ---------- swing plans (decided after the close, executed next morning) ----------
    def save_plan(self, tag: str, for_date: date, body: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO plans VALUES (?,?,?,?,?,?)",
                        (tag, self.mode, for_date.isoformat(), datetime.now().isoformat(), json.dumps(body), "planned"))
        self.db.commit()

    def plans(self, for_date: date, status: str = "planned") -> List[dict]:
        rows = self.db.execute("SELECT tag, body FROM plans WHERE mode=? AND for_date=? AND status=?",
                               (self.mode, for_date.isoformat(), status)).fetchall()
        return [dict(json.loads(r["body"]), tag=r["tag"]) for r in rows]

    def set_plan_status(self, tag: str, status: str) -> None:
        self.db.execute("UPDATE plans SET status=? WHERE tag=? AND mode=?", (status, tag, self.mode))
        self.db.commit()

    def cancel_plans(self, for_date: date) -> int:
        cur = self.db.execute("UPDATE plans SET status='cancelled' WHERE mode=? AND for_date=? AND status='planned'",
                              (self.mode, for_date.isoformat()))
        self.db.commit()
        return cur.rowcount

    # ---------- small persistent state for strategies ----------
    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE mode=? AND key=?", (self.mode, key)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (self.mode, key, json.dumps(value)))
        self.db.commit()

    # ---------- audit ----------
    def event(self, level: str, message: str) -> None:
        self.db.execute("INSERT INTO events VALUES (?,?,?,?)", (datetime.now().isoformat(), self.mode, level, message))
        self.db.commit()
