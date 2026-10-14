"""Facções vivas: planos em etapas, posturas entre facções, reputação e territórios.

Estado: state/sim/factions.json = {"factions": {fac-id: {...}}, "_applied": [...], "_pending": {...}, "_ops": [...]}
  goals [str] · leader npc-id · stance {fac-y: {stance, score -100..100}} · reputation {entidade: -100..100}
  territories {loc-/reg-/dist-id: {control 0-100, security 0-100, situation, events [evt]}}
  plans [{id, goal, status, current (índice da etapa), attempt, on_fail interromper|retry, max_attempts,
          last_event, reason, steps [{id, desc, start_conditions, done_conditions, effects, duration, cost {res: qtd}}]}]
  history [{at, date, type, ..., cause}]
A existência da facção vem de world.md; membros vêm do frontmatter 'faction:' dos NPCs; saldos vêm de resources.json.
Etapa: quando start_conditions valem e há recursos, um evento "início" (agora) consome o custo e agenda a conclusão
(now+duration); na conclusão, done_conditions verdadeiras → aplica effects e avança; falsas → interrompe (ou retry).
"""
import re
from pathlib import Path

import sim
from rpg import camp_dir

PLAN_ST = ("ativo", "concluido", "interrompido", "falhou")
TERR_PREFIX = ("loc-", "reg-", "dist-")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
FIELDS = ("goals", "leader", "stance", "reputation", "territories", "plans")


def path(cdir):
    return Path(cdir) / "state" / "sim" / "factions.json"


def load(cdir):
    F = sim.load_json(path(cdir), {})
    for k, v in (("factions", {}), ("_applied", []), ("_pending", {}), ("_ops", [])):
        F.setdefault(k, v)
    return F


def save(cdir, F):
    sim.save_json(path(cdir), F)


def _fac(F, fid):
    x = F["factions"].setdefault(fid, {})
    for k, v in (("goals", []), ("stance", {}), ("reputation", {}), ("territories", {}), ("plans", []), ("history", [])):
        x.setdefault(k, v)
    return x


def _need_fac(w, fid):
    if fid not in w.ctx.factions:
        raise sim.SimError(f"facção '{fid}' não existe em world.md")


def _plan(F, fid, pid):
    p = next((p for p in _fac(F, fid)["plans"] if p["id"] == pid), None)
    if not p:
        raise sim.SimError(f"{fid} não tem plano '{pid}'")
    p.setdefault("current", 0)
    p.setdefault("attempt", 1)
    return p


def _hist(w, x, ev, **kw):
    x["history"].append(dict(kw, at=w.now, date=w.fmt(w.now), cause=ev["id"] if ev else None))


def _pct(v, what):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 100:
        raise sim.SimPermanent(f"{what} deve estar entre 0 e 100 (recebido {v!r})")
    return v


def _score(v):
    return max(-100, min(100, v))


# ---------------------------------------------------------------- validação (set + verificador)
def problems(w, fid, x):
    P = []
    if fid not in w.ctx.factions:
        P.append("não existe em world.md")
    if x.get("leader") and not (x["leader"].startswith("npc-") and w.exists(x["leader"])):
        P.append(f"líder '{x['leader']}' não é um NPC existente")
    for y, s in x.get("stance", {}).items():
        if y not in w.ctx.factions or y == fid:
            P.append(f"postura com facção inválida '{y}'")
        if not isinstance(s, dict) or not s.get("stance") or not isinstance(s.get("score", 0), (int, float)) \
                or not -100 <= s.get("score", 0) <= 100:
            P.append(f"postura com {y} precisa de 'stance' e 'score' entre -100 e 100")
    for who, v in x.get("reputation", {}).items():
        if not w.exists(who):
            P.append(f"reputação com entidade inexistente '{who}'")
        if not isinstance(v, (int, float)) or not -100 <= v <= 100:
            P.append(f"reputação com {who} fora de -100..100: {v!r}")
    for tid, t in x.get("territories", {}).items():
        if not tid.startswith(TERR_PREFIX) or not w.exists(tid):
            P.append(f"território inexistente '{tid}' (use loc-/reg-/dist- existente)")
        for k in ("control", "security"):
            try:
                _pct(t.get(k, 0), f"território {tid}: {k}")
            except sim.SimError as e:
                P.append(str(e))
    pids = [p.get("id") for p in x.get("plans", [])]
    if len(set(pids)) != len(pids):
        P.append("ids de plano repetidos")
    for p in x.get("plans", []):
        pre = f"plano {p.get('id')}"
        if not SLUG.match(str(p.get("id", ""))):
            P.append(f"{pre}: id inválido (minúsculas, números e hífen)")
        if p.get("status", "ativo") not in PLAN_ST:
            P.append(f"{pre}: status '{p.get('status')}' inválido ({', '.join(PLAN_ST)})")
        if p.get("on_fail", "interromper") not in ("interromper", "retry"):
            P.append(f"{pre}: on_fail deve ser 'interromper' ou 'retry'")
        steps = p.get("steps") or []
        if not steps:
            P.append(f"{pre}: sem etapas")
        if not 0 <= p.get("current", 0) <= len(steps):
            P.append(f"{pre}: etapa atual fora do intervalo")
        sids = [s.get("id") for s in steps]
        if len(set(sids)) != len(sids) or not all(SLUG.match(str(s)) for s in sids):
            P.append(f"{pre}: ids de etapa inválidos ou repetidos")
        for s in steps:
            for k, kind in (("start_conditions", "condition"), ("done_conditions", "condition"), ("effects", "effect")):
                for c in s.get(k, []):
                    try:
                        sim.check_shape(c, kind)
                    except sim.SimError as e:
                        P.append(f"{pre}/{s.get('id')}: {e}")
            P += [f"{pre}/{s.get('id')}: {x}" for x in sim.agency_problems(s.get("effects", []), "efeitos da etapa", w)]
            try:
                sim.parse_duration(s.get("duration", "0min"), w.cal)
            except sim.SimError as e:
                P.append(f"{pre}/{s.get('id')}: {e}")
            for r, q in (s.get("cost") or {}).items():
                if not isinstance(q, (int, float)) or isinstance(q, bool) or q <= 0:
                    P.append(f"{pre}/{s.get('id')}: custo de {r} deve ser > 0")
    return P


# ---------------------------------------------------------------- motor dos planos
def try_plan(w, fid, p, evs, trigger=None):
    """Inicia a etapa atual se elegível (idempotente). → (situação, detalhe)."""
    if p.get("status", "ativo") != "ativo":
        return "parado", p.get("status")
    i, n = p.get("current", 0), p.get("attempt", 1)
    if i >= len(p["steps"]):
        return "parado", "sem etapas restantes"
    st = p["steps"][i]
    base = f"fac:{fid}:{p['id']}:{st['id']}:{n}"
    mine = [e for e in evs if (e.get("gen_key") or "").rpartition("#")[0] == base]
    live = [e for e in mine if e["status"] != "cancelado"]
    if live:
        return "andamento", live[-1]["id"]
    ok, why = sim.check(w, {"type": "all", "of": st.get("start_conditions", [])})
    if not ok:
        return "aguardando", why
    cost = sorted((st.get("cost") or {}).items())
    ok, why = sim.check(w, {"type": "all", "of": [{"type": "resource.gte", "who": fid, "res": r, "qty": q} for r, q in cost]})
    if not ok:
        day = w.now // (w.cal["minutes_per_hour"] * w.cal["hours_per_day"])
        w.record(f"{fid} não pôde iniciar '{st.get('desc', st['id'])}' (plano {p['id']}): recursos insuficientes", "faccao",
                 hidden=True, faction=fid, cause=p.get("last_event"), result={"motivo": why},
                 gen_key=f"fac:{fid}:{p['id']}:semrecurso:{day}")  # no máximo 1 registro por dia por plano
        return "sem recursos", why
    done = {"type": "faccao", "text": f"{fid} conclui etapa '{st.get('desc', st['id'])}' (plano {p['id']})", "hidden": True,
            "faction": fid, "effects": [{"type": "faction.step.done", "faction": fid, "plan": p["id"], "step": st["id"], "attempt": n}]}
    effs = [{"type": "resource.consume", "who": fid, "res": r, "qty": q} for r, q in cost]
    effs.append({"type": "schedule", "in": st.get("duration", "0min"), "event": done})
    cause = trigger or p.get("last_event")  # causa = o que tornou a etapa possível
    srcs = [s for s in sim.knowledge_sources(w, st.get("start_conditions")) if s != cause]  # origem do que a facção sabia
    eid = w.schedule({"type": "faccao", "text": f"{fid} inicia etapa '{st.get('desc', st['id'])}' (plano {p['id']})",
                      "due": w.now, "priority": 0, "hidden": True, "faction": fid, "effects": effs, "sources": srcs},
                     cause=cause, gen_key=f"{base}#{len(mine)}")
    return "iniciada", eid


def tick(w, trigger=None):
    if not path(w.dir).exists():
        return
    F, evs = load(w.dir), w.events()
    for fid, x in F["factions"].items():
        if fid in w.ctx.factions:
            for p in x.get("plans", []):
                if p.get("steps"):
                    try_plan(w, fid, p, evs, trigger)


@sim.generator
def _gen(w, t0, t1):
    tick(w)  # o restante acontece no ouvinte, à medida que o mundo muda durante o avanço


@sim.listener
def _listen(w, ev):
    tick(w, ev["id"])


# ---------------------------------------------------------------- efeitos
@sim.effect("faction.step.done")
def _fx_step_done(w, f, key, ev):
    F = load(w.dir)
    if key in F["_applied"]:
        return {"etapa": f["step"], "ja_aplicada": True}
    x, p = _fac(F, f["faction"]), _plan(F, f["faction"], f["plan"])
    cur = p["steps"][p["current"]]["id"] if p["current"] < len(p["steps"]) else None
    if p.get("status") != "ativo" or cur != f["step"] or p["attempt"] != f["attempt"]:
        sim.once(F, key)
        save(w.dir, F)
        return {"ignorado": "o plano mudou desde o início da etapa"}
    st = p["steps"][p["current"]]
    if key not in F["_pending"]:  # decisão (e a origem do conhecimento usado) gravada antes dos efeitos: a retomada não reavalia
        conds = st.get("done_conditions", [])
        ok, why = sim.check(w, {"type": "all", "of": conds})
        F["_pending"][key] = [ok, why, sim.knowledge_sources(w, conds) if ok else []]
        save(w.dir, F)
    ok, why = F["_pending"][key][:2]
    w.add_sources(ev["id"], F["_pending"][key][2] if len(F["_pending"][key]) > 2 else [])  # decisão antiga: sem origem
    if ok:
        for i, sub in enumerate(st.get("effects", [])):
            sim.apply(w, sub, f"{key}/{i}", ev)
            w.reload()
        F = load(w.dir)  # os efeitos podem ter alterado factions.json
        x, p = _fac(F, f["faction"]), _plan(F, f["faction"], f["plan"])
        p["current"] += 1
        if p["current"] >= len(p["steps"]):
            p["status"] = "concluido"
        _hist(w, x, ev, type="etapa", plan=p["id"], step=st["id"], result="concluida")
        res = {"etapa": st["id"], "concluida": True, "plano": p["status"]}
    else:
        if p.get("on_fail") == "retry" and p["attempt"] < p.get("max_attempts", 3):
            p["attempt"] += 1
            res = {"etapa": st["id"], "nova_tentativa": p["attempt"], "motivo": why}
        else:
            p["status"] = "falhou" if p.get("on_fail") == "retry" else "interrompido"
            p["reason"] = why
            res = {"etapa": st["id"], p["status"]: why}
        _hist(w, x, ev, type="etapa", plan=p["id"], step=st["id"], result=p["status"], reason=why)
        w.record(f"{f['faction']}: plano {p['id']} {p['status'] if p['status'] != 'ativo' else 'repete'} etapa "
                 f"'{st.get('desc', st['id'])}' — {why}", "faccao", hidden=True, faction=f["faction"], cause=ev["id"],
                 result={"motivo": why}, gen_key=f"{key}:falha")
    p["last_event"] = ev["id"]
    F["_pending"].pop(key, None)
    sim.once(F, key)
    save(w.dir, F)
    return res


sim.composite("faction.step.done")  # aplica os efeitos da etapa por dentro


@sim.precheck("faction.stance")
def _pc_stance(w, f):
    if f.get("a") == f.get("b"):
        raise sim.SimPermanent("postura de uma facção com ela mesma")
    if not isinstance(f.get("score_delta", 0), (int, float)) or isinstance(f.get("score_delta"), bool):
        raise sim.SimPermanent("score_delta deve ser número")


@sim.precheck("faction.reputation")
def _pc_rep(w, f):
    if not isinstance(f.get("delta"), (int, float)) or isinstance(f.get("delta"), bool):
        raise sim.SimPermanent("delta deve ser número")


@sim.precheck("faction.territory")
def _pc_terr(w, f):
    if not str(f.get("territory", "")).startswith(TERR_PREFIX):
        raise sim.SimPermanent(f"território '{f.get('territory')}' precisa ser loc-/reg-/dist-")
    for k in ("control", "security"):
        if k in f:
            _pct(f[k], k)


@sim.effect("faction.stance")
def _fx_stance(w, f, key, ev):
    a, b = f["a"], f["b"]
    _need_fac(w, a)
    _need_fac(w, b)
    if a == b:
        raise sim.SimError("postura de uma facção com ela mesma")
    if not isinstance(f.get("score_delta", 0), (int, float)):
        raise sim.SimError("score_delta deve ser número")
    F = load(w.dir)
    if sim.once(F, key):  # não toca em relations.jsonl: relações antigas seguem como estão
        for x, y in [(a, b), (b, a)] if f.get("symmetric", True) else [(a, b)]:
            s = _fac(F, x)["stance"].setdefault(y, {"stance": "neutra", "score": 0})
            old = dict(s)
            s["stance"] = f.get("stance") or s["stance"]
            s["score"] = _score(s["score"] + f.get("score_delta", 0))
            _hist(w, _fac(F, x), ev, type="stance", com=y, de=old, para=dict(s), reason=f.get("reason", ""))
        save(w.dir, F)
    s = _fac(F, a)["stance"][b]
    return {"postura": f"{a}→{b}", "stance": s["stance"], "score": s["score"]}


@sim.effect("faction.reputation")
def _fx_rep(w, f, key, ev):
    _need_fac(w, f["faction"])
    if not w.exists(f["who"]):
        raise sim.SimError(f"entidade '{f['who']}' não existe")
    F = load(w.dir)
    x = _fac(F, f["faction"])
    if sim.once(F, key):
        old = x["reputation"].get(f["who"], 0)
        x["reputation"][f["who"]] = _score(old + f["delta"])
        _hist(w, x, ev, type="reputacao", who=f["who"], de=old, para=x["reputation"][f["who"]], reason=f.get("reason", ""))
        save(w.dir, F)
    return {"reputacao": f["who"], "faccao": f["faction"], "valor": x["reputation"][f["who"]]}


@sim.effect("faction.territory")
def _fx_terr(w, f, key, ev):
    fid, tid = f["faction"], f["territory"]
    _need_fac(w, fid)
    if not tid.startswith(TERR_PREFIX) or not w.exists(tid):
        raise sim.SimError(f"território '{tid}' não existe (use loc-/reg-/dist- existente)")
    for k in ("control", "security"):
        if k in f:
            _pct(f[k], k)
    F = load(w.dir)
    x = _fac(F, fid)
    t = x["territories"].setdefault(tid, {"control": 0, "security": 0, "situation": "", "events": []})
    if sim.once(F, key):
        old = {k: t.get(k) for k in ("control", "security", "situation")}
        t.update({k: f[k] for k in ("control", "security", "situation") if k in f})
        t.setdefault("events", []).append(ev["id"])
        _hist(w, x, ev, type="territorio", territory=tid, de=old, para={k: t.get(k) for k in old})
        save(w.dir, F)
    return {"territorio": tid, "faccao": fid, "control": t.get("control"), "security": t.get("security")}


@sim.effect("faction.plan.start")
def _fx_plan_start(w, f, key, ev):
    _need_fac(w, f["faction"])
    F = load(w.dir)
    p = _plan(F, f["faction"], f["plan"])
    if sim.once(F, key) and p.get("status") != "ativo":
        if p.get("status") == "concluido":
            p["current"] = 0
        p.update(status="ativo", attempt=p["attempt"] + 1, last_event=ev["id"])
        p.pop("reason", None)
        _hist(w, _fac(F, f["faction"]), ev, type="plano", plan=p["id"], result="iniciado")
        save(w.dir, F)
    return {"plano": p["id"], "status": p["status"]}


# ---------------------------------------------------------------- condições
@sim.condition("faction.stance")
def _c_stance(w, c):
    s = load(w.dir)["factions"].get(c["a"], {}).get("stance", {}).get(c["b"], {}).get("stance", "neutra")
    return s in c["in"], f"{c['a']}→{c['b']} está '{s}'"


@sim.condition("faction.control")
def _c_control(w, c):
    v = load(w.dir)["factions"].get(c["faction"], {}).get("territories", {}).get(c["territory"], {}).get("control", 0)
    return v >= c["gte"], f"{c['faction']} controla {c['territory']} em {v} (precisa {c['gte']})"


@sim.condition("faction.plan")
def _c_plan(w, c):
    p = next((p for p in load(w.dir)["factions"].get(c["faction"], {}).get("plans", []) if p["id"] == c["plan"]), None)
    st = p.get("status", "ativo") if p else None
    return st == c.get("status", "concluido"), f"plano {c['faction']}/{c['plan']} está {st}"


# ---------------------------------------------------------------- verificador
@sim.verifier
def _verify(w):
    if not path(w.dir).exists():
        return []
    return [f"[facções] {fid}: {m}" for fid, x in sorted(load(w.dir)["factions"].items()) for m in problems(w, fid, x)]


# ---------------------------------------------------------------- CLI
def _w(a, clock=True):
    sim.load_modules()
    return sim.World(camp_dir(a.campaign), clock)


def _step_line(w, fid, p, evs):
    if p.get("status", "ativo") != "ativo" or p.get("current", 0) >= len(p["steps"]):
        return p.get("status", "?") + (f" — {p['reason']}" if p.get("reason") else "")
    st = p["steps"][p.get("current", 0)]
    base = f"fac:{fid}:{p['id']}:{st['id']}:{p.get('attempt', 1)}"
    run = [e for e in evs if (e.get("gen_key") or "").rpartition("#")[0] == base and e["status"] != "cancelado"]
    return f"ativo, etapa {p.get('current', 0) + 1}/{len(p['steps'])} '{st['id']}'" + (" (em andamento)" if run else "")


def _rels(w, fid):
    return [f"{r['a']} —{r['type']}→ {r['b']}" + (" [MESTRE]" if r.get("secret") else "")
            for r in w.ctx.relations() if fid in (r["a"], r["b"])]


def cmd_show(a):
    w = _w(a, False)
    _need_fac(w, a.fac)
    x = _fac(load(w.dir), a.fac)
    evs = w.events()
    print(f"# {a.fac} | líder: {x.get('leader', '-')}")
    print("Objetivos: " + ("; ".join(x["goals"]) or "-"))
    print("Membros: " + (", ".join(k for k, e in sorted(w.ctx.ent.items())
                                   if k.startswith("npc-") and e["meta"].get("faction") == a.fac) or "-"))
    h = sim.load_res(w.dir)["holdings"].get(a.fac, {})
    print("Recursos: " + (", ".join(f"{k}={v:g}" for k, v in sorted(h.items())) or "-"))
    for tid, t in sorted(x["territories"].items()):
        print(f"Território {tid}: controle {t.get('control', 0)}, segurança {t.get('security', 0)}"
              + (f" — {t['situation']}" if t.get("situation") else ""))
    for p in x["plans"]:
        print(f"Plano {p['id']} ({p.get('goal', '')}): {_step_line(w, a.fac, p, evs)}")
    for y, s in sorted(x["stance"].items()):
        print(f"Postura com {y}: {s['stance']} ({s['score']:+g})")
    for ln in _rels(w, a.fac):
        print(f"Relação: {ln}")


def cmd_relations(a):
    w = _w(a, False)
    F = load(w.dir)
    fids = [a.fac] if a.fac else w.ctx.factions
    if a.fac:
        _need_fac(w, a.fac)
    for fid in fids:
        x = F["factions"].get(fid, {})
        for y, s in sorted(x.get("stance", {}).items()):
            print(f"{fid} → {y}: {s['stance']} ({s['score']:+g})")
        for ln in _rels(w, fid):
            print(f"{fid}: {ln}")


def cmd_plans(a):
    w = _w(a, False)
    _need_fac(w, a.fac)
    x, evs = _fac(load(w.dir), a.fac), w.events()
    if not x["plans"]:
        print("Nenhum plano.")
    for p in x["plans"]:
        print(f"## {p['id']} — {p.get('goal', '')}: {_step_line(w, a.fac, p, evs)} (tentativa {p.get('attempt', 1)})")
        for i, s in enumerate(p["steps"]):
            mark = "✓" if i < p.get("current", 0) else ("→" if i == p.get("current", 0) and p.get("status", "ativo") == "ativo" else " ")
            cost = ", ".join(f"{k}={v:g}" for k, v in (s.get("cost") or {}).items())
            print(f"  {mark} {s['id']}: {s.get('desc', '')} | duração {s.get('duration', '0min')}" + (f" | custo {cost}" if cost else ""))


def cmd_step(a):
    w = _w(a)
    _need_fac(w, a.fac)
    F = load(w.dir)
    if a.op and a.op in F["_ops"]:
        raise sim.SimError(f"operação '{a.op}' já foi aplicada — nada foi feito de novo")
    p = _plan(F, a.fac, a.plan)
    if not p.get("steps"):
        raise sim.SimError(f"plano {a.plan} sem etapas")
    st, info = try_plan(w, a.fac, p, w.events())
    if st == "iniciada":
        sim.process_event(w, w.event(info))  # consome o custo e agenda a conclusão agora
        info = f"{info}; " + "; ".join(f"{e['id']} para {w.fmt(e['due'])}" for e in w.events()
                                       if e.get("cause") == info and e["status"] == "pendente")
    if a.op:
        F = load(w.dir)
        F["_ops"].append(a.op)
        save(w.dir, F)
    print(f"{a.fac}/{a.plan}: {st} — {info}")


def cmd_set(a):
    w = _w(a, False)
    _need_fac(w, a.fac)
    v = sim._json_arg(a.json, "--json")
    if len(v) != 1 or not isinstance(v[0], dict):
        raise sim.SimError("--json deve ser um objeto")
    bad = set(v[0]) - set(FIELDS)
    if bad:
        raise sim.SimError(f"campos desconhecidos: {', '.join(sorted(bad))} (aceitos: {', '.join(FIELDS)})")
    F = load(w.dir)
    x = _fac(F, a.fac)
    x.update(v[0])
    for p in x["plans"]:
        p.setdefault("status", "ativo")
        p.setdefault("current", 0)
        p.setdefault("attempt", 1)
    for t in x["territories"].values():
        t.setdefault("events", [])
    P = problems(w, a.fac, x)
    if P:
        raise sim.SimError("nada foi gravado:\n  " + "\n  ".join(P))
    x["history"].append({"at": w.now, "date": w.fmt(w.now), "type": "set", "campos": sorted(v[0]), "cause": None})
    save(w.dir, F)
    print(f"{a.fac} atualizada: {', '.join(sorted(v[0]))}")


def cmd_territory(a):
    import maps
    w = _w(a, False)
    if not w.exists(a.id):
        raise sim.SimError(f"'{a.id}' não existe")
    rows = sorted(((t.get("control", 0), fid, t) for fid, x in load(w.dir)["factions"].items()
                   for tid, t in x.get("territories", {}).items() if tid == a.id), key=lambda r: -r[0])
    if not rows:
        print(f"Nenhuma facção controla ou influencia {a.id}.")
    for i, (c, fid, t) in enumerate(rows):
        print(f"{'controla' if i == 0 and c > 0 else 'influencia'}: {fid} (controle {c}, segurança {t.get('security', 0)})"
              + (f" — {t['situation']}" if t.get("situation") else ""))
    for m in maps.load_all(w.dir):  # somente leitura
        for e in m.get("points", []) + m.get("areas", []):
            if a.id in (e["id"], e.get("ref")):
                print(f"No mapa {m['id']} ({m.get('name', '')}): {e['id']} {e.get('name', '')}")


def register(sp):
    w = sim._wrap
    t = sp.add_parser("faction", help="facções: planos, posturas, reputação e territórios").add_subparsers(dest="fcmd", required=True)
    c = t.add_parser("show", help="estado, membros, recursos, territórios, planos e relações")
    c.add_argument("fac"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_show))
    c = t.add_parser("relations", help="posturas entre facções e relações do grafo")
    c.add_argument("fac", nargs="?"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_relations))
    c = t.add_parser("plans", help="planos e etapas"); c.add_argument("fac"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_plans))
    c = t.add_parser("step", help="inicia agora a próxima etapa elegível (mesmas regras do gerador)")
    c.add_argument("fac"); c.add_argument("plan"); c.add_argument("--op", help="id único da ação (evita repetir)")
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_step))
    c = t.add_parser("set", help="grava campos da facção (valida antes)")
    c.add_argument("fac"); c.add_argument("--json", required=True, help="JSON ou @arquivo.json"); c.add_argument("--campaign")
    c.set_defaults(f=w(cmd_set))
    c = t.add_parser("territory", help="quem controla/influencia um território (e em qual mapa está)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_territory))
