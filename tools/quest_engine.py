"""Missões estruturadas: objetivos verificáveis, dependências, desbloqueio, prazos e consequências.

O status continua no frontmatter `status:` de quests/quest-*.md (fonte de verdade). A definição
estruturada e o histórico ficam em state/sim/quests.json:
  {"quests": {"quest-x": {
      "objectives": [{"id": "achar-mapa", "desc": "...", "done_conditions": [<condições>], "optional": false}],
      "depends_on": ["quest-y"],              # só conclui com estas concluídas; também liberam o desbloqueio
      "unlock_conditions": [<condições>],     # oculta → unlock_to quando verdadeiras (e dependências concluídas)
      "unlock_to": "disponivel",              # ou "descoberta"
      "deadline": 12345,                      # minutos absolutos (CLI: "+3d" ou "A/M/D HH:MM")
      "on_deadline": {"status": "expirada", "effects": [<efeitos>]},
      "rewards": [<efeitos>],                 # ao concluir, uma única vez
      "consequences": {"falhou": [<efeitos>]},# por status, uma única vez
      "flow": {"aceita": ["em_andamento"]},   # substitui as transições do estado de origem
      "auto_complete": false,                 # true: conclui sozinha quando objetivos obrigatórios cumpridos
      "on_dependency_failed": null,           # "falhou" | "cancelada" quando dependência termina sem sucesso
      "public": "texto para os jogadores", "gm": "notas do mestre",
      "manual": {"<obj>": {"done": true, "at": t, "by": "evt-…", "reason": "..."}},
      "history": [{"at", "de", "para", "motivo", "evento", "causa", "key"}]}},
   "_applied": [...], "_once": {"quest-x:concluida": "<chave do efeito que aplicou>"}}
"""
import json, re, sys
from pathlib import Path

import sim
from rpg import read_meta
from sim import SimError

STATES = ("oculta", "disponivel", "descoberta", "aceita", "em_andamento", "concluida", "falhou", "expirada", "cancelada")
LEGACY = {"ativa": "em_andamento", "abandonada": "cancelada"}
FINAL = {"concluida", "falhou", "expirada", "cancelada"}
OPEN = {"disponivel", "descoberta", "aceita", "em_andamento"}
_END = ["falhou", "expirada", "cancelada"]
FLOW = {"oculta": ["disponivel", "descoberta"] + _END,
        "disponivel": ["descoberta", "aceita", "em_andamento", "concluida"] + _END,
        "descoberta": ["aceita", "em_andamento", "concluida"] + _END,
        "aceita": ["em_andamento", "concluida"] + _END,
        "em_andamento": ["concluida"] + _END,
        **{s: [] for s in FINAL}}
DEF_KEYS = {"objectives", "depends_on", "unlock_conditions", "unlock_to", "deadline", "on_deadline", "rewards",
            "consequences", "flow", "auto_complete", "on_dependency_failed", "public", "gm"}
OID = re.compile(r"[a-z0-9][a-z0-9_-]*")


def qpath(cdir):
    return Path(cdir) / "state" / "sim" / "quests.json"


def load_q(cdir):
    Q = sim.load_json(qpath(cdir), {"quests": {}})
    Q.setdefault("quests", {})
    return Q


def norm(s):
    return LEGACY.get(s, s)


def status(w, q):
    """Status cru do frontmatter (relido do disco), ou None se a missão não existe."""
    p = w.entity_path(q) if q.startswith("quest-") else None
    return read_meta(p).get("status", "") if p and p.exists() else None


def objective_ok(w, d, o):
    m = d.get("manual", {}).get(o["id"])
    if m is not None:
        return m["done"], f"marcado manualmente por {m.get('by')}" + (f" ({m['reason']})" if m.get("reason") else "")
    if not o.get("done_conditions"):
        return False, "sem condição verificável — marque com o efeito quest.objective"
    return sim.check(w, {"type": "all", "of": o["done_conditions"]})


def deps_ok(w, d):
    bad = [f"{x}={status(w, x)}" for x in d.get("depends_on", []) if norm(status(w, x)) != "concluida"]
    return not bad, ("dependências não concluídas: " + ", ".join(bad)) if bad else "dependências concluídas"


def can(w, q, to, Q=None):
    """→ (ok, motivo): fluxo, dependências e objetivos obrigatórios (só condições estruturadas, nunca texto)."""
    if to not in STATES:
        return False, f"status desconhecido '{to}' (válidos: {', '.join(STATES)})"
    cur = status(w, q)
    if cur is None:
        return False, f"missão '{q}' não existe"
    cur = norm(cur)
    d = (Q or load_q(w.dir))["quests"].get(q, {})
    flow = {**FLOW, **d.get("flow", {})}
    if cur not in flow:
        return False, f"{q} tem status desconhecido '{cur}' no frontmatter"
    if to not in flow[cur]:
        return False, f"transição {cur} → {to} não permitida (permitidas: {', '.join(flow[cur]) or 'nenhuma'})"
    if to == "concluida":
        ok, why = deps_ok(w, d)
        if not ok:
            return False, why
        pend = [o["id"] for o in d.get("objectives", []) if not o.get("optional") and not objective_ok(w, d, o)[0]]
        if pend:
            return False, "objetivos obrigatórios não satisfeitos: " + ", ".join(pend)
    return True, f"{cur} → {to} permitida"


# ---------------------------------------------------------------- efeitos e condições
@sim.effect("quest.status")
def _fx_status(w, f, key, ev):
    """Muda o status validando fluxo/dependências/objetivos; consequências do novo status uma única vez."""
    q, to = f["quest"], f["to"]
    Q = load_q(w.dir)
    d = Q["quests"].setdefault(q, {})
    h = next((x for x in d.get("history", []) if x.get("key") == key), None)
    if h and key in Q.get("_applied", []):
        return {"missao": q, "de": h["de"], "para": h["para"]}
    if not h:  # retomada após falha: a transição já foi validada e registrada
        ok, why = can(w, q, to, Q)
        if not ok:
            raise SimError(f"{q}: {why}")
        h = {"at": w.now, "de": status(w, q), "para": to, "motivo": f.get("reason", ""), "evento": ev["id"],
             "causa": ev.get("cause"), "key": key}
        if to == "concluida":
            h["objetivos"] = {o["id"]: objective_ok(w, d, o)[0] for o in d.get("objectives", [])}
        d.setdefault("history", []).append(h)
        sim.save_json(qpath(w.dir), Q)
    if status(w, q) != to:
        sim.set_meta(w.entity_path(q), "status", to)
        w.reload()
    effs = (d.get("rewards", []) if to == "concluida" else []) + d.get("consequences", {}).get(to, [])
    Q = load_q(w.dir)
    if Q.setdefault("_once", {}).setdefault(f"{q}:{to}", key) == key:  # só o 1º evento a chegar neste status aplica
        sim.save_json(qpath(w.dir), Q)
        for j, x in enumerate(effs):
            sim.apply(w, x, f"{key}/{j}", ev)
            w.reload()
    Q = load_q(w.dir)
    sim.once(Q, key)
    sim.save_json(qpath(w.dir), Q)
    return {"missao": q, "de": h["de"], "para": to, "consequencias": len(effs)}


sim.composite("quest.status")  # aplica recompensas/consequências por dentro


@sim.effect("quest.objective")
def _fx_objective(w, f, key, ev):
    """Marca objetivo sem condição verificável (registra evento e motivo)."""
    Q = load_q(w.dir)
    d = Q["quests"].get(f["quest"])
    if not d or f["objective"] not in [o["id"] for o in d.get("objectives", [])]:
        raise SimError(f"objetivo '{f['objective']}' não definido em {f['quest']}")
    if sim.once(Q, key):
        d.setdefault("manual", {})[f["objective"]] = {"done": bool(f.get("done", True)), "at": w.now, "by": ev["id"],
                                                      "reason": f.get("reason", "")}
        sim.save_json(qpath(w.dir), Q)
    return {"missao": f["quest"], "objetivo": f["objective"], "feito": bool(f.get("done", True))}


@sim.condition("quest")
def _c_quest(w, c):
    v = status(w, c["quest"])
    v = norm(v) if v is not None else None
    ok = v in [norm(x) for x in c["in"]] if "in" in c else v == norm(c.get("eq", ""))
    return ok, f"{c['quest']} está {v}"


@sim.condition("quest.objective")
def _c_objective(w, c):
    d = load_q(w.dir)["quests"].get(c["quest"], {})
    o = next((o for o in d.get("objectives", []) if o["id"] == c["objective"]), None)
    if not o:
        return False, f"objetivo {c['objective']} não existe em {c['quest']}"
    ok, why = objective_ok(w, d, o)
    return ok, f"{c['quest']}/{c['objective']}: {why}"


@sim.condition("quest.can")
def _c_can(w, c):
    return can(w, c["quest"], c["to"])


# ---------------------------------------------------------------- gatilhos e prazos
def _transition(w, q, to, why, cause=None, gen_key=None, due=None, priority=50, extra=(), declared=False):
    return w.schedule({"type": "missao", "text": f"Missão {q} → {to}: {why}", "due": w.now if due is None else due,
                       "priority": priority, "quest": q, "hidden": True,
                       "conditions": [{"type": "quest.can", "quest": q, "to": to}],
                       "effects": [{"type": "quest.status", "quest": q, "to": to, "reason": why}, *extra]},
                      cause=cause, gen_key=gen_key, declared=declared)


def reevaluate(w, cause=None):
    # ponytail: reavalia todas as missões definidas a cada evento; indexar por condição se houver centenas
    for q, d in load_q(w.dir)["quests"].items():
        try:
            st = norm(status(w, q) or "")
            if st == "oculta" and (d.get("unlock_conditions") or d.get("depends_on")):
                ok, why = sim.check(w, {"type": "all", "of": d.get("unlock_conditions", [])})
                if ok and deps_ok(w, d)[0]:
                    _transition(w, q, d.get("unlock_to", "disponivel"), f"desbloqueada ({why or 'dependências concluídas'})",
                                cause, f"quest-unlock:{q}")
            elif st in OPEN:
                objs = [o for o in d.get("objectives", []) if not o.get("optional")]
                if objs and deps_ok(w, d)[0] and all(objective_ok(w, d, o)[0] for o in objs):
                    if d.get("auto_complete"):
                        _transition(w, q, "concluida", "objetivos obrigatórios cumpridos", cause, f"quest-complete:{q}")
                    else:
                        w.record(f"Missão {q} pronta para concluir (objetivos obrigatórios cumpridos)", "missao",
                                 gen_key=f"quest-ready:{q}", cause=cause, quest=q, hidden=True)
            if st not in FINAL and d.get("on_dependency_failed"):
                bad = [x for x in d.get("depends_on", []) if norm(status(w, x) or "") in FINAL - {"concluida"}]
                if bad:
                    _transition(w, q, d["on_dependency_failed"], f"dependência {', '.join(bad)} não pode mais ser concluída",
                                cause, f"quest-depfail:{q}")
        except SimError as e:
            print(f"AVISO: missão {q} não reavaliada: {e}", file=sys.stderr)


@sim.listener
def _on_event(w, ev):
    for q in dict.fromkeys(f["quest"] for f in ev.get("effects", []) if f.get("type") == "quest.status" and f.get("quest")):
        _rebase_deadline(w, q, ev["id"])  # transição (de qualquer evento) depois de o prazo já estar agendado
    reevaluate(w, ev["id"])


def last_transition(w, q):
    """Evento da última transição de status registrada da missão (o que fundamenta o prazo), ou None."""
    h = load_q(w.dir)["quests"].get(q, {}).get("history", [])
    return next((x["evento"] for x in reversed(h) if x.get("evento") and w.event(x["evento"])), None)


def _deadline(w, q, d, gen_key, cause):
    od = d.get("on_deadline") or {}
    why = f"prazo vencido em {w.fmt(d['deadline'])}" + ("" if cause else " (sem transição anterior registrada: causa não identificável)")
    # prioridade 90: no mesmo instante, uma conclusão (50) é processada antes do prazo
    return _transition(w, q, od.get("status", "expirada"), why, cause, gen_key, due=max(d["deadline"], w.now), priority=90,
                       extra=od.get("effects", []))


def _rebase_deadline(w, q, eid):
    """Prazo já agendado antes de uma nova transição: cancela o pendente (registrado) e reagenda com a causa nova."""
    d = load_q(w.dir)["quests"].get(q, {})
    dl = d.get("deadline")
    if dl is None or norm(status(w, q) or "") in FINAL:
        return
    for e in w.events():
        gk = e.get("gen_key") or ""
        if e["status"] == "pendente" and gk.split(":")[:3] == ["quest-deadline", q, str(dl)] and e.get("cause") != eid:
            w.set_status(e["id"], "cancelado", {"motivo": f"prazo reancorado na transição {eid}"})
            _deadline(w, q, d, f"quest-deadline:{q}:{dl}:{eid}", eid)


@sim.generator
def _deadlines(w, t0, t1):
    for q, d in load_q(w.dir)["quests"].items():
        dl = d.get("deadline")
        if dl is None or dl > t1 or norm(status(w, q) or "") in FINAL:
            continue
        _deadline(w, q, d, f"quest-deadline:{q}:{dl}", last_transition(w, q))
    reevaluate(w)  # pega mudanças feitas fora de eventos (ex.: 'fact learn', 'map reveal'); sem causa


# ---------------------------------------------------------------- verificação
def find_cycle(defs):
    done = set()

    def visit(n, path):
        if n in path:
            return path[path.index(n):] + [n]
        if n in done:
            return None
        for m in defs.get(n, {}).get("depends_on", []):
            c = visit(m, path + [n])
            if c:
                return c
        done.add(n)
    return next((c for c in (visit(n, []) for n in defs) if c), None)


@sim.verifier
def _verify(w):
    P = []
    Q = load_q(w.dir)
    quests = {k: e for k, e in w.ctx.ent.items() if k.startswith("quest-")}
    for q, e in sorted(quests.items()):
        s = e["meta"].get("status")
        if s and norm(s) not in STATES:
            P.append(f"[missão] {q}: status '{s}' desconhecido (válidos: {', '.join(STATES)}, ativa, abandonada)")
    for q, d in sorted(Q["quests"].items()):
        if q not in quests:
            P.append(f"[missão] definição em quests.json para missão inexistente '{q}'")
            continue
        P += [f"[missão] {q}: depends_on '{x}' não existe" for x in d.get("depends_on", []) if x not in quests]
        st = norm(quests[q]["meta"].get("status", ""))
        dl = d.get("deadline")
        if w.clock and dl is not None and dl < w.now and st not in FINAL:
            P.append(f"[missão] {q}: prazo venceu em {w.fmt(dl)} e ela segue '{st}' — 'time process'")
        if st == "concluida":
            last = d.get("history", [])[-1:]
            snap = last[0].get("objetivos", {}) if last and last[0]["para"] == "concluida" else {}
            try:
                pend = [o["id"] for o in d.get("objectives", []) if not o.get("optional")
                        and not (snap[o["id"]] if o["id"] in snap else objective_ok(w, d, o)[0])]
            except SimError as e:
                pend = [f"(não verificável: {e})"]
            if pend:
                P.append(f"[missão] {q} concluída com objetivo obrigatório não satisfeito: {', '.join(pend)}")
    cyc = find_cycle(Q["quests"])
    if cyc:
        P.append(f"[missão] ciclo de dependências: {' → '.join(cyc)}")
    return P


# ---------------------------------------------------------------- CLI
def _effects(v, what):
    if not isinstance(v, list):
        raise SimError(f"{what} deve ser lista")
    for x in v:
        sim.check_shape(x, "effect")


def _conds(v, what):
    if not isinstance(v, list):
        raise SimError(f"{what} deve ser lista")
    for x in v:
        sim.check_shape(x, "condition")


def validate(w, q, d):
    if not isinstance(d, dict):
        raise SimError("a definição precisa ser um objeto JSON")
    if q not in w.ctx.ent or not q.startswith("quest-"):
        raise SimError(f"missão '{q}' não existe (crie quests/{q}.md antes)")
    if set(d) - DEF_KEYS:
        raise SimError(f"campos desconhecidos: {', '.join(sorted(set(d) - DEF_KEYS))} (aceitos: {', '.join(sorted(DEF_KEYS))})")
    seen = set()
    for o in d.get("objectives", []):
        if not isinstance(o, dict) or not OID.fullmatch(str(o.get("id", ""))) or o["id"] in seen:
            raise SimError(f"objetivo inválido ou repetido: {o!r} (precisa de 'id' único, minúsculo)")
        if set(o) - {"id", "desc", "done_conditions", "optional"}:
            raise SimError(f"objetivo {o['id']}: campos aceitos são id, desc, done_conditions, optional")
        seen.add(o["id"])
        _conds(o.get("done_conditions", []), f"{o['id']}.done_conditions")
    for x in d.get("depends_on", []):
        if x == q or x not in w.ctx.ent or not x.startswith("quest-"):
            raise SimError(f"depends_on: '{x}' não é outra missão existente")
    _conds(d.get("unlock_conditions", []), "unlock_conditions")
    if d.get("unlock_to", "disponivel") not in ("disponivel", "descoberta"):
        raise SimError("unlock_to deve ser 'disponivel' ou 'descoberta'")
    if isinstance(d.get("deadline"), str):
        if not w.clock:
            raise SimError("prazo exige relógio iniciado ('rpg.py time init')")
        d["deadline"] = sim.parse_when(d["deadline"], w.cal, w.now)
    if d.get("deadline") is not None and (not isinstance(d["deadline"], int) or (w.clock and d["deadline"] < w.now)):
        raise SimError(f"prazo inválido ou no passado: {d['deadline']!r}")
    od = d.get("on_deadline", {})
    if not isinstance(od, dict) or set(od) - {"status", "effects"} or od.get("status", "expirada") not in STATES:
        raise SimError("on_deadline deve ser {\"status\": <estado>, \"effects\": [...]}")
    if od.get("status") in sim.PC_CHOICE_STATES:
        raise SimError(f"on_deadline não pode pôr a missão em '{od['status']}': aceitar/começar é decisão do jogador")
    _effects(od.get("effects", []), "on_deadline.effects")
    _effects(d.get("rewards", []), "rewards")
    for s, v in d.get("consequences", {}).items():
        if s not in STATES:
            raise SimError(f"consequences: estado desconhecido '{s}'")
        _effects(v, f"consequences.{s}")
    for s, v in d.get("flow", {}).items():
        if s not in STATES or not isinstance(v, list) or any(x not in STATES for x in v):
            raise SimError(f"flow: '{s}' → {v!r} usa estado desconhecido (válidos: {', '.join(STATES)})")
    if d.get("on_dependency_failed") not in (None, "falhou", "cancelada"):
        raise SimError("on_dependency_failed deve ser 'falhou', 'cancelada' ou null")
    if not isinstance(d.get("auto_complete", False), bool):
        raise SimError("auto_complete deve ser true/false")
    for k in ("public", "gm"):
        if not isinstance(d.get(k, ""), str):
            raise SimError(f"{k} deve ser texto")
    P = sim.agency_problems([od.get("effects", []), d.get("rewards", []), d.get("consequences", {})], "missão", w)
    if P:
        raise SimError("; ".join(P) + " (consequência de missão não tem autorização do jogador; o efeito fica bloqueado)")


def cmd_define(a):
    w = sim._w(a, False)
    js = sim._json_arg(a.json, "--json")
    if len(js) != 1:
        raise SimError("--json deve ser UM objeto com a definição")
    d = js[0]
    validate(w, a.id, d)
    Q = load_q(w.dir)
    old = Q["quests"].get(a.id, {})
    d.update({k: old[k] for k in ("history", "manual") if k in old})
    cyc = find_cycle({**Q["quests"], a.id: d})
    if cyc:
        raise SimError(f"ciclo de dependências: {' → '.join(cyc)}")
    Q["quests"][a.id] = d
    sim.save_json(qpath(w.dir), Q)
    gk = f"quest-deadline:{a.id}:{d.get('deadline')}"
    for e in w.events():  # prazo redefinido: o evento antigo não pode mais disparar
        if (e.get("gen_key") or "").startswith(f"quest-deadline:{a.id}:") and e["gen_key"].split(":")[2] != str(d.get("deadline")) \
                and e["status"] == "pendente":
            w.set_status(e["id"], "cancelado", {"motivo": "prazo redefinido"})
    print(f"Missão {a.id} definida ({len(d.get('objectives', []))} objetivo(s)"
          + (f", prazo {w.fmt(d['deadline'])}" if d.get("deadline") is not None else "") + ")")


def _get(w, q):
    if status(w, q) is None:
        raise SimError(f"missão '{q}' não existe")
    return load_q(w.dir)["quests"].get(q, {})


def _check_lines(w, q, d):
    out = [f"Dependências: {deps_ok(w, d)[1]}" if d.get("depends_on") else "Dependências: nenhuma"]
    if d.get("unlock_conditions"):
        ok, why = sim.check(w, {"type": "all", "of": d["unlock_conditions"]})
        out.append(f"Desbloqueio (→ {d.get('unlock_to', 'disponivel')}): {'SIM' if ok else 'não'} — {why}")
    out.append("Objetivos:" if d.get("objectives") else "Objetivos: nenhum estruturado")
    for o in d.get("objectives", []):
        ok, why = objective_ok(w, d, o)
        out.append(f"  [{'x' if ok else ' '}] {o['id']}{' (opcional)' if o.get('optional') else ''}: {o.get('desc', '')} — {why}")
    ok, why = can(w, q, "concluida")
    out.append(f"Pode concluir agora: {'SIM' if ok else 'não'} — {why}")
    return out


def _j(x):
    return json.dumps(x, ensure_ascii=False)


def cmd_show(a):
    w = sim._w(a, False)
    d = _get(w, a.id)
    e = w.ctx.ent[a.id]
    st = status(w, a.id)
    if a.public:
        if norm(st) == "oculta":
            raise SimError(f"{a.id} é oculta — os jogadores não sabem dela")
        print(f"{e['meta'].get('name', a.id)} — {st}")
        txt = d.get("public") or e["known"]
        if txt:
            print(txt)
        for o in d.get("objectives", []):
            print(f"  [{'x' if objective_ok(w, d, o)[0] else ' '}] {o.get('desc') or o['id']}{' (opcional)' if o.get('optional') else ''}")
        return
    m = e["meta"]
    print(f"{a.id} — {m.get('name', '?')} [{st}] | giver: {m.get('giver', '-')} | local: {m.get('location', '-')}")
    if not d:
        print("(sem definição estruturada em quests.json — use 'quest define')")
    print(f"Público: {d.get('public') or e['known'] or '-'}")
    if d.get("gm"):
        print(f"[MESTRE] {d['gm']}")
    print("\n".join(_check_lines(w, a.id, d)))
    for o in d.get("objectives", []):
        if o.get("done_conditions"):
            print(f"  condições de {o['id']}: {_j(o['done_conditions'])}")
    if d.get("deadline") is not None:
        od = d.get("on_deadline") or {}
        print(f"Prazo: {w.fmt(d['deadline'])} → {od.get('status', 'expirada')}" + (f" + {_j(od['effects'])}" if od.get("effects") else ""))
    if d.get("rewards"):
        print(f"Recompensas: {_j(d['rewards'])}")
    for s, v in d.get("consequences", {}).items():
        print(f"Consequências ({s}): {_j(v)}")
    flow = {**FLOW, **d.get("flow", {})}
    print(f"Transições a partir de '{norm(st)}': {', '.join(flow.get(norm(st), [])) or 'nenhuma'}"
          f" | auto_complete: {d.get('auto_complete', False)} | dependência falhou → {d.get('on_dependency_failed') or 'nada'}")
    print(f"Histórico: {len(d.get('history', []))} transição(ões) — 'quest history {a.id}'")


def cmd_check(a):
    w = sim._w(a, False)
    d = _get(w, a.id)
    print(f"{a.id} [{status(w, a.id)}] — verificação agora ({w.fmt(w.now)}), nada foi alterado")
    print("\n".join(_check_lines(w, a.id, d)))


def cmd_list(a):
    w = sim._w(a, False)
    Q = load_q(w.dir)["quests"]
    rows = sorted((k, e) for k, e in w.ctx.ent.items() if k.startswith("quest-"))
    if a.public:
        rows = [(k, e) for k, e in rows if norm(e["meta"].get("status", "")) != "oculta"]
    if not rows:
        print("Nenhuma missão.")
    for k, e in rows:
        line = f"{k} — {e['meta'].get('name', '?')} [{e['meta'].get('status', '?')}]"
        if not a.public:
            d = Q.get(k, {})
            line += (" (estruturada)" if set(d) & DEF_KEYS else "") + (f" | prazo {w.fmt(d['deadline'])}" if d.get("deadline") is not None else "")
        print(line)


def cmd_deadlines(a):
    w = sim._w(a, False)
    rows = sorted((d["deadline"], q) for q, d in load_q(w.dir)["quests"].items() if d.get("deadline") is not None)
    if not rows:
        print("Nenhuma missão com prazo.")
    for dl, q in rows:
        st = status(w, q)
        tag = f"encerrada ({st})" if norm(st or "") in FINAL else ("VENCIDO, não processado" if w.clock and dl < w.now else "pendente")
        print(f"{w.fmt(dl)} {q} [{st}] — {tag}")


def cmd_history(a):
    w = sim._w(a, False)
    d = _get(w, a.id)
    if not d.get("history"):
        print(f"{a.id}: nenhuma transição registrada pelo motor (status atual: {status(w, a.id)}).")
    for h in d.get("history", []):
        print(f"{w.fmt(h['at'])} {h['de']} → {h['para']} — motivo: {h.get('motivo') or '-'} | evento {h['evento']}")
        c, n = w.event(h.get("causa") or ""), 0
        while c and n < 6:
            print(f"    causa: {c['id']} [{c.get('date', '?')}] {c.get('text', '')}")
            c, n = w.event(c.get("cause") or ""), n + 1
    for oid, m in d.get("manual", {}).items():
        print(f"objetivo {oid} marcado {'feito' if m['done'] else 'não feito'} em {w.fmt(m['at'])} por {m['by']}"
              + (f" — {m['reason']}" if m.get("reason") else ""))


def _op_event(a, check, make, sig=None):
    """Padrão dos comandos que mudam missão: valida, agenda evento (idempotente por --op), processa.
    sig(evento) diz se o evento já gravado para este --op tem os MESMOS dados (senão: conflito, nada muda)."""
    w = sim._w(a)
    gk = f"quest-op:{a.op}" if a.op else None
    prev = next((e for e in w.events() if gk and e.get("gen_key") == gk), None)
    if prev and sig and (not sig(prev) or (prev.get("cause") or None) != (a.cause or None)):
        raise SimError(f"a operação '{a.op}' já foi usada com outros dados ({prev['id']}); use outro --op")
    if prev and prev["status"] == "processado":
        print(f"Operação '{a.op}' já aplicada em {prev['id']} — nada mudou.")
        return None
    if prev and prev["status"] == "cancelado":
        raise SimError(f"operação '{a.op}' foi cancelada em {prev['id']}: {prev.get('result')}")
    if not prev:
        ok, why = check(w)
        if not ok:
            raise SimError(f"recusado: {why}")
        if a.cause and not w.event(a.cause):
            raise SimError(f"evento '{a.cause}' não existe")
    eid = prev["id"] if prev else make(w, gk)
    w2, log = sim.process_due(w.dir)
    e = w2.event(eid)
    if e["status"] != "processado":
        raise SimError(f"{eid} ficou '{e['status']}': {e.get('result')}")
    sim._print_log(w2, [x for x in log if x[0]["id"] != eid])
    return eid


def cmd_set_status(a):
    eid = _op_event(a, lambda w: can(w, a.id, a.status),
                    lambda w, gk: _transition(w, a.id, a.status, a.reason, a.cause, gk, declared=True),
                    lambda e: e.get("effects", [{}])[0] == {"type": "quest.status", "quest": a.id, "to": a.status, "reason": a.reason})
    if eid:
        print(f"{a.id}: agora '{a.status}' ({eid}; motivo: {a.reason})")


def cmd_objective(a):
    def check(w):
        d = _get(w, a.id)
        if a.objective not in [o["id"] for o in d.get("objectives", [])]:
            return False, f"objetivo '{a.objective}' não definido em {a.id}"
        if norm(status(w, a.id)) in FINAL:
            return False, f"{a.id} já está encerrada ({status(w, a.id)})"
        return True, ""

    def make(w, gk):
        return w.schedule({"type": "missao", "text": f"Missão {a.id}: objetivo {a.objective} {'feito' if a.done else 'desfeito'} ({a.reason})",
                           "quest": a.id, "hidden": True,
                           "effects": [{"type": "quest.objective", "quest": a.id, "objective": a.objective,
                                        "done": a.done, "reason": a.reason}]}, cause=a.cause, gen_key=gk)
    eid = _op_event(a, check, make, lambda e: e.get("effects", [{}])[0] == {
        "type": "quest.objective", "quest": a.id, "objective": a.objective, "done": a.done, "reason": a.reason})
    if eid:
        print(f"{a.id}/{a.objective}: marcado {'feito' if a.done else 'não feito'} ({eid}; motivo: {a.reason})")


def register(sp):
    W = sim._wrap
    q = sp.add_parser("quest", help="missões estruturadas: objetivos, dependências, prazos, consequências").add_subparsers(dest="qcmd", required=True)
    c = q.add_parser("define", help="define/atualiza a estrutura da missão (preserva histórico)")
    c.add_argument("id"); c.add_argument("--json", required=True, help="JSON ou @arquivo.json"); c.add_argument("--campaign")
    c.set_defaults(f=W(cmd_define))
    c = q.add_parser("show", help="tudo da missão (mestre) ou só o público (--public)")
    c.add_argument("id"); c.add_argument("--public", action="store_true"); c.add_argument("--campaign"); c.set_defaults(f=W(cmd_show))
    c = q.add_parser("list", help="missões e status (--public exclui ocultas)")
    c.add_argument("--public", action="store_true"); c.add_argument("--campaign"); c.set_defaults(f=W(cmd_list))
    c = q.add_parser("check", help="avalia objetivos/dependências/desbloqueio agora (somente leitura)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=W(cmd_check))
    c = q.add_parser("set-status", help="muda o status via evento (validado, com histórico causal)")
    c.add_argument("id"); c.add_argument("status", choices=STATES); c.add_argument("--reason", required=True)
    c.add_argument("--op", help="id único da ação (idempotência)"); c.add_argument("--cause", help="evt-* que causou")
    c.add_argument("--campaign"); c.set_defaults(f=W(cmd_set_status))
    c = q.add_parser("objective", help="marca objetivo sem condição verificável via evento (com histórico causal)")
    c.add_argument("id"); c.add_argument("objective"); g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--done", dest="done", action="store_true"); g.add_argument("--undone", dest="done", action="store_false")
    c.add_argument("--reason", required=True); c.add_argument("--op", help="id único da ação (idempotência)")
    c.add_argument("--cause", help="evt-* que causou"); c.add_argument("--campaign"); c.set_defaults(f=W(cmd_objective))
    c = q.add_parser("deadlines", help="prazos em ordem"); c.add_argument("--campaign"); c.set_defaults(f=W(cmd_deadlines))
    c = q.add_parser("history", help="por que o status mudou (transições e eventos causa)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=W(cmd_history))
