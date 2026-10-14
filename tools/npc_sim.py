"""NPCs autônomos: rotina (nível 1), decisão por objetivos (nível 2), propostas do mestre (nível 3) e viagens.

Estado OPCIONAL por NPC em state/sim/npcs.json (local/status continuam no frontmatter; conhecimento em facts.json):
  {"npcs": {"npc-x": {"tier": 1|2|3,
                      "routine": [{"from": "HH:MM", "to": "HH:MM", "loc": "loc-x", "activity": "...",
                                   "days": ["segunda", ...] (opcional; exige semana no calendário),
                                   "skip_rest": false (opcional; pula folgas/feriados)}],
                      "goals": [{"id", "desc", "priority", "actions": [{"id", "desc", "preconditions": [condições],
                                 "effects": [efeitos], "utility", "risk", "duration": "2h"}]}],
                      "needs": "...", "fears": "...", "motivations": "...",
                      "decisions": ["evt-..."],
                      "travel": {"from", "to", "depart", "arrive", "event"}}},   # só enquanto viaja
   "_applied": [chaves de efeitos já aplicados]}

Rotina: no início de cada bloco ("from") o NPC vai para "loc"; entre blocos fica onde está.
Decisão: candidatas = ações com TODAS as precondições verdadeiras; escore = prioridade*utilidade - risco;
desempate determinístico por w.rng(f"decide:{npc}:{agora}"). Tudo que este módulo grava é hidden (mestre).
"""
import math, re
from pathlib import Path

import sim
import world
from rpg import ID_RE

KM_POR_DIA = 30   # velocidade a pé usada para estimar viagens pelo mapa (km por dia fictício)
DECIDE_HOUR = 6   # hora fictícia em que NPCs de nível >= 2 reavaliam objetivos (uma vez por dia)
ACTIVE = ("pendente", "processando", "falhou")


def st_path(cdir):
    return Path(cdir) / "state" / "sim" / "npcs.json"


def load(cdir):
    S = sim.load_json(st_path(cdir), {"npcs": {}})
    S.setdefault("npcs", {})
    return S


def _npc(w, npc):
    if not npc.startswith("npc-") or npc not in w.ctx.ent:
        raise sim.SimError(f"NPC '{npc}' não existe")
    return w.ctx.ent[npc]


def dead(w, npc):
    e = w.ctx.ent.get(npc)
    return bool(e) and e["meta"].get("status", "").startswith("mort")


def day_len(cal):
    return cal["minutes_per_hour"] * cal["hours_per_day"]


hhmm, daily = sim.hhmm, sim.daily  # recorrência diária vive no núcleo (sim.py)


def remember(w, npc, eid):
    S = load(w.dir)
    d = S["npcs"].setdefault(npc, {}).setdefault("decisions", [])
    if eid not in d:
        d.append(eid)
        sim.save_json(st_path(w.dir), S)


# ---------------------------------------------------------------- validação
def _walk(x, k=None):
    if isinstance(x, dict):
        for kk, v in x.items():
            yield from _walk(v, kk)
    elif isinstance(x, list):
        for v in x:
            yield from _walk(v, k)
    elif isinstance(x, str):
        yield k, x


def check_action(w, a):
    P = []
    if not isinstance(a.get("effects"), list) or not a["effects"]:
        P.append("precisa de 'effects' (lista não vazia)")
    for kind, key in (("condition", "preconditions"), ("effect", "effects")):
        xs = a.get(key, [])
        if not isinstance(xs, list):
            P.append(f"'{key}' precisa ser lista")
            continue
        for x in xs:
            try:
                sim.check_shape(x, kind)
            except sim.SimError as e:
                P.append(str(e))
    if a.get("duration"):
        try:
            sim.parse_duration(str(a["duration"]), w.cal)
        except sim.SimError as e:
            P.append(str(e))
    for k in ("utility", "risk"):
        if not isinstance(a.get(k, 0), (int, float)) or isinstance(a.get(k), bool):
            P.append(f"'{k}' precisa ser número")
    for _, v in _walk([a.get("preconditions", []), a.get("effects", [])]):
        if world.ID_ANY.fullmatch(v) and not w.exists(v):
            P.append(f"referência inexistente: {v}")
    return P + sim.agency_problems(a.get("effects", []), f"ação '{a.get('id', a.get('desc', '?'))}'", w)


knowledge_sources = sim.knowledge_sources  # vive no núcleo (usada também pelas facções)


def check_goals(w, goals):
    P, gids = [], set()
    for g in goals if isinstance(goals, list) else [goals]:
        if not isinstance(g, dict) or not ID_RE.match(str(g.get("id", ""))):
            P.append(f"objetivo precisa de 'id' (minúsculas/hífen): {g!r}")
            continue
        if g["id"] in gids:
            P.append(f"objetivo duplicado: {g['id']}")
        gids.add(g["id"])
        if not isinstance(g.get("priority", 1), (int, float)):
            P.append(f"{g['id']}: 'priority' precisa ser número")
        aids = set()
        for a in g.get("actions", []):
            if not isinstance(a, dict) or not ID_RE.match(str(a.get("id", ""))):
                P.append(f"{g['id']}: ação precisa de 'id' (minúsculas/hífen): {a!r}")
                continue
            if a["id"] in aids:
                P.append(f"{g['id']}: ação duplicada {a['id']}")
            aids.add(a["id"])
            P += [f"{g['id']}/{a['id']}: {p}" for p in check_action(w, a)]
    return P


def check_routine(w, r):
    P, froms = [], set()
    for b in r if isinstance(r, list) else [r]:
        if not isinstance(b, dict) or not {"from", "to", "loc"} <= b.keys():
            P.append(f"bloco de rotina precisa de from, to e loc: {b!r}")
            continue
        try:
            f = hhmm(b["from"], w.cal)
            hhmm(b["to"], w.cal)
        except sim.SimError as e:
            P.append(str(e))
            continue
        try:
            sim.check_days(b.get("days"), w.cal)
        except sim.SimError as e:
            P.append(str(e))
        days = set(b.get("days") or ["*"])  # "*" = todo dia; colide com qualquer bloco no mesmo horário
        if any(x == f and (d == "*" or "*" in days or d in days) for x, d in froms):
            P.append(f"dois blocos começam às {b['from']}")
        froms |= {(f, d) for d in days}
        if not w.exists(b["loc"]):
            P.append(f"local '{b['loc']}' não existe")
    return P


# ---------------------------------------------------------------- condição / efeitos
@sim.condition("npc.free")
def _c_free(w, c):
    npc = c["npc"]
    if dead(w, npc):
        return False, f"{npc} está morto"
    t = load(w.dir)["npcs"].get(npc, {}).get("travel")
    if t:
        return False, f"{npc} está em viagem para {t['to']} (chega {w.fmt(t['arrive'])})"
    return True, f"{npc} disponível"


def travel_minutes(w, a, b):
    """Duração pela distância no mapa de menor escala que tenha os dois locais (pontos com ref); None se não houver."""
    import maps
    best = None
    for m in maps.load_all(w.dir):
        pts = {p["ref"]: p for p in m.get("points", []) if p.get("ref")}
        if a in pts and b in pts:
            km_cell = m["size"]["per_cell"] / (1000 if m["size"]["unit"] == "m" else 1)
            if best is None or km_cell < best[0]:
                best = (km_cell, math.dist((pts[a]["x"], pts[a]["y"]), (pts[b]["x"], pts[b]["y"])) * km_cell)
    return None if best is None else max(1, math.ceil(best[1] / KM_POR_DIA * day_len(w.cal)))


@sim.effect("npc.travel")
def _fx_travel(w, f, key, ev):
    """Parte agora; a chegada (entity.set location) é agendada. Durante a viagem o frontmatter mantém a origem."""
    npc, to = f["npc"], f["to"]
    _npc(w, npc)
    if not w.exists(to):
        raise sim.SimError(f"destino '{to}' não existe")
    S = load(w.dir)
    if key in S.get("_applied", []):
        return {"viajante": npc, "para": to}
    ok, why = _c_free(w, f)
    if not ok:
        raise sim.SimError(f"{npc} não pode viajar: {why}")
    frm = w.ctx.ent[npc]["meta"].get("location")
    mins = sim.parse_duration(str(f["duration"]), w.cal) if f.get("duration") else travel_minutes(w, frm, to)
    if mins is None:
        raise sim.SimError(f"nenhum mapa tem {frm} e {to} — informe 'duration' na viagem")
    arr = w.schedule({"type": "npc.chegada", "text": f"{npc} chega a {to}", "due": w.now + mins, "entities": [npc],
                      "loc": to, "npc": npc, "hidden": True,
                      "effects": [{"type": "entity.set", "id": npc, "field": "location", "value": to},
                                  {"type": "npc.arrive", "npc": npc}]}, cause=ev["id"], gen_key=f"travel:{key}")
    sim.once(S, key)
    S["npcs"].setdefault(npc, {})["travel"] = {"from": frm, "to": to, "depart": w.now, "arrive": w.now + mins, "event": arr}
    sim.save_json(st_path(w.dir), S)
    return {"viajante": npc, "de": frm, "para": to, "chegada": arr, "em": w.fmt(w.now + mins)}


@sim.effect("npc.arrive")
def _fx_arrive(w, f, key, ev):
    S = load(w.dir)
    t = S["npcs"].get(f["npc"], {}).get("travel")
    if t and t["event"] == ev["id"]:  # idempotente por natureza
        S["npcs"][f["npc"]].pop("travel")
        sim.save_json(st_path(w.dir), S)
    return {"chegou": f["npc"]}


@sim.effect("npc.decide")
def _fx_decide(w, f, key, ev):
    did, res, act = decide(w, f["npc"], f"npc-decide:{key}", ev["id"])
    return {"decisao": did, "acao": act}


# ---------------------------------------------------------------- decisão
def evaluate(w, npc):
    """Só lê: avalia as ações dos objetivos e devolve o raciocínio completo."""
    _npc(w, npc)
    st = load(w.dir)["npcs"].get(npc, {})
    free, why_free = _c_free(w, {"npc": npc})
    busy = {e["gen_key"].rsplit(":", 1)[0]: e["id"] for e in w.events()
            if e["status"] in ACTIVE and (e.get("gen_key") or "").startswith(f"npc-action:{npc}:")}
    rng = w.rng(f"decide:{npc}:{w.now}")
    cands, rej, acts = [], [], {}
    for g in st.get("goals", []):
        for a in g.get("actions", []):
            tb = rng.random()  # sempre sorteia, para o desempate não depender de quais ações falharam
            base = {"goal": g["id"], "action": a["id"], "desc": a.get("desc", a["id"])}
            checks = [sim.check(w, c) for c in a.get("preconditions", [])]
            why = [("ok: " if ok else "FALHOU: ") + m for ok, m in checks]
            k = f"npc-action:{npc}:{g['id']}:{a['id']}"
            if not free:
                fail = why_free
            elif k in busy:
                fail = f"já em andamento ({busy[k]})"
            elif not all(ok for ok, _ in checks):
                fail = "precondição falsa"
            else:
                score = g.get("priority", 1) * a.get("utility", 1) - a.get("risk", 0)
                cands.append(dict(base, score=score, desempate=round(tb, 6), verificado=why))
                acts[(g["id"], a["id"])] = a
                continue
            rej.append(dict(base, motivo=fail, verificado=why))
    cands.sort(key=lambda c: (-c["score"], c["desempate"]))
    ch = None
    if cands:
        a = acts[(cands[0]["goal"], cands[0]["action"])]
        ch = dict(cands[0], **{k: a[k] for k in ("preconditions", "effects", "duration") if k in a})
    return {"npc": npc, "em": w.fmt(w.now), "escolha": ch, "candidatas": cands, "descartadas": rej,
            "motivo": None if ch else ("sem ação possível: " + (why_free if not free else
                                       "nenhuma ação com todas as precondições" if rej else "sem objetivos/ações"))}


def schedule_action(w, npc, a, cause, gen_key):
    due = w.now + (sim.parse_duration(str(a["duration"]), w.cal) if a.get("duration") else 0)
    return w.schedule({"type": "npc.acao", "text": f"{npc}: {a['desc']}", "due": due, "entities": [npc], "npc": npc,
                       "hidden": True, "conditions": [{"type": "npc.free", "npc": npc}] + a.get("preconditions", []),
                       "effects": a["effects"]}, cause=cause, gen_key=gen_key)


def decide(w, npc, gen_key, cause=None):
    """Avalia, registra a decisão e agenda a ação escolhida. Idempotente por gen_key (reusa o raciocínio gravado)."""
    old = next((e for e in w.events() if e.get("gen_key") == gen_key), None)
    if old and (old.get("cause") or None) != (cause or None):
        raise sim.SimError(f"a operação já foi usada com outra causa ({old['id']}); use outro --op")
    res = (old or {}).get("result") or evaluate(w, npc)
    ch = res["escolha"]
    srcs = [s for s in knowledge_sources(w, ch.get("preconditions")) if s != cause] if ch and not old else []
    did = w.record(f"{npc} decide: {ch['desc']}" if ch else f"{npc}: {res['motivo']}", "npc.decisao", entities=[npc],
                   npc=npc, hidden=True, cause=cause, gen_key=gen_key, result=res, sources=srcs)
    act = schedule_action(w, npc, ch, did, f"npc-action:{npc}:{ch['goal']}:{ch['action']}:{did}") if ch else None
    remember(w, npc, did)
    return did, res, act


def propose(w, npc, act, gen_key=None, cause=None):
    """Nível 3: o mestre sugere; o sistema valida formato, referências, disponibilidade e precondições."""
    _npc(w, npc)
    if not isinstance(act, dict):
        raise sim.SimError("proposta recusada: --action precisa ser UM objeto JSON")
    old = next((e for e in w.events() if gen_key and e.get("gen_key") == gen_key), None)
    if old:  # mesma --op: só repete com a MESMA proposta (não revalida: o que vale é o que foi validado antes)
        prev = {k: v for k, v in ((old.get("result") or {}).get("escolha") or {}).items() if k not in ("goal", "action", "verificado")}
        if prev != act or (old.get("cause") or None) != (cause or None):
            raise sim.SimError(f"a operação já foi usada com outra proposta ({old['id']}); use outro --op")
        return old["id"], schedule_action(w, npc, act, old["id"], f"npc-proposal-action:{old['id']}")
    P = check_action(w, act) + ([] if act.get("desc") else ["precisa de 'desc'"])
    if P:
        raise sim.SimError("proposta recusada (formato/referências): " + "; ".join(P))
    free, why = _c_free(w, {"npc": npc})
    checks = [sim.check(w, c) for c in act.get("preconditions", [])]
    bad = ([] if free else [why]) + [m for ok, m in checks if not ok]
    if bad:
        raise sim.SimError("proposta recusada (precondições falsas): " + "; ".join(bad))
    ch = dict(act, goal="mestre", action="proposta", verificado=["ok: " + m for _, m in checks])
    res = {"npc": npc, "em": w.fmt(w.now), "origem": "proposta do mestre", "escolha": ch, "candidatas": [], "descartadas": []}
    srcs = [s for s in knowledge_sources(w, act.get("preconditions")) if s != cause]
    pid = w.record(f"{npc}: proposta do mestre validada — {act['desc']}", "npc.proposta", entities=[npc], npc=npc,
                   hidden=True, cause=cause, gen_key=gen_key, result=res, sources=srcs)
    aid = schedule_action(w, npc, act, pid, f"npc-proposal-action:{pid}")
    remember(w, npc, pid)
    return pid, aid


# ---------------------------------------------------------------- geradores
@sim.generator
def _g_routine(w, t0, t1):
    # ponytail: um evento por fronteira de bloco; avanços muito longos (meses) geram muitos eventos — resumir se pesar
    for npc, st in sorted(load(w.dir)["npcs"].items()):
        if not st.get("routine") or npc not in w.ctx.ent or dead(w, npc):
            continue
        for b in st["routine"]:
            for t in sim.occurrences(t0, t1, b["from"], w.cal, b.get("days"), b.get("skip_rest", False)):
                w.schedule({"type": "npc.rotina", "text": f"{npc}: {b.get('activity') or 'rotina'} em {b['loc']}",
                            "due": t, "priority": 40, "entities": [npc], "loc": b["loc"], "npc": npc, "hidden": True,
                            "conditions": [{"type": "npc.free", "npc": npc}],
                            "effects": [{"type": "entity.set", "id": npc, "field": "location", "value": b["loc"]}]},
                           gen_key=f"routine:{npc}:{t}")


@sim.generator
def _g_daily(w, t0, t1):
    mpd, mph = day_len(w.cal), w.cal["minutes_per_hour"]
    off = min(DECIDE_HOUR, w.cal["hours_per_day"] - 1) * mph
    for npc, st in sorted(load(w.dir)["npcs"].items()):
        if st.get("tier", 1) < 2 or not st.get("goals") or npc not in w.ctx.ent or dead(w, npc):
            continue
        for t in daily(t0, t1, off, mpd):
            w.schedule({"type": "npc.gatilho", "text": f"{npc} reavalia seus objetivos", "due": t, "priority": 60,
                        "entities": [npc], "npc": npc, "hidden": True, "effects": [{"type": "npc.decide", "npc": npc}]},
                       gen_key=f"npc-daily:{npc}:{t // mpd}")


# ---------------------------------------------------------------- verificador
@sim.verifier
def _v_npcs(w):
    P = []
    S = load(w.dir)
    pend = [e for e in w.events() if e["status"] in ACTIVE]
    for npc, st in sorted(S["npcs"].items()):
        if npc not in w.ctx.ent:
            P.append(f"[npc] estado em npcs.json para '{npc}', que não existe")
            continue
        if dead(w, npc):
            if st.get("routine"):
                P.append(f"[npc] {npc} está morto mas ainda tem rotina")
            P += [f"[npc] {npc} está morto mas tem evento pendente {e['id']} ({e.get('type')})" for e in pend if e.get("npc") == npc]
        P += [f"[npc] {npc} rotina: {p}" for p in check_routine(w, st.get("routine", []))]
        P += [f"[npc] {npc} objetivos: {p}" for p in check_goals(w, st.get("goals", []))]
        t = st.get("travel")
        if t and not any(e["id"] == t.get("event") for e in pend):
            P.append(f"[npc] {npc} em viagem para {t.get('to')} sem evento de chegada pendente ({t.get('event')})")
    return P


# ---------------------------------------------------------------- CLI
def _print_reason(res):
    print(f"Decisão de {res['npc']} em {res['em']}" + (f" ({res['origem']})" if res.get("origem") else "") + ":")
    ch = res.get("escolha")
    if ch:
        print(f"  ESCOLHA: {ch['goal']}/{ch['action']} — {ch['desc']}" + (f" (escore {ch['score']})" if "score" in ch else "")
              + (f" | duração {ch['duration']}" if ch.get("duration") else ""))
        for v in ch.get("verificado", []):
            print(f"    {v}")
    else:
        print(f"  {res.get('motivo') or 'sem ação possível'}")
    for c in res.get("candidatas", []):
        print(f"  candidata {c['goal']}/{c['action']}: escore {c['score']} (desempate {c['desempate']})")
    for d in res.get("descartadas", []):
        print(f"  descartada {d['goal']}/{d['action']}: {d['motivo']}" + (" — " + "; ".join(d["verificado"]) if d["verificado"] else ""))


def _cause(w, c):
    if c and not w.event(c):
        raise sim.SimError(f"evento '{c}' não existe")
    return c


def cmd_show(a):
    w = sim._w(a, False)
    e = _npc(w, a.id)
    st = load(w.dir)["npcs"].get(a.id, {})
    print(f"## {a.id} — " + " | ".join(f"{k}: {v}" for k, v in e["meta"].items() if k != "id"))
    print(f"Nível: {st.get('tier', 1)}")
    for k, lbl in (("needs", "Necessidades"), ("fears", "Medos"), ("motivations", "Motivações")):
        if st.get(k):
            print(f"{lbl}: {st[k]}")
    if st.get("travel"):
        t = st["travel"]
        print(f"EM VIAGEM: {t['from']} → {t['to']}, chegada {w.fmt(t['arrive'])} ({t['event']})")
    if st.get("routine"):
        print("Rotina:")
        for b in st["routine"]:
            print(f"  {b['from']}–{b['to']} {b['loc']}" + (f": {b['activity']}" if b.get("activity") else ""))
    if st.get("goals"):
        print("Objetivos:")
        for g in st["goals"]:
            print(f"  [{g['id']}] prioridade {g.get('priority', 1)}: {g.get('desc', '')}")
            for x in g.get("actions", []):
                print(f"    - {x['id']}: {x.get('desc', '')} (utilidade {x.get('utility', 1)}, risco {x.get('risk', 0)}"
                      f", duração {x.get('duration', '0')}, {len(x.get('preconditions', []))} precondição(ões))")
    ks = sim.knowledge(w.dir, a.id)
    cnt = {s: sum(1 for k in ks.values() if k["status"] == s) for s in sim.KNOW_RANK}
    print("Conhecimento: " + ", ".join(f"{s} {n}" for s, n in cnt.items()) + " (detalhes: npc knows)")
    pend = sorted((x for x in w.events() if x["status"] in ACTIVE and world._touches(x, a.id)), key=sim._order)
    print("Agenda pendente:" + ("" if pend else " nada"))
    for x in pend:
        print(f"  {w.fmt(x['due'])} {x['id']} [{x.get('type')}] {x['status']}: {x.get('text', '')}")
    if st.get("decisions"):
        print("Decisões: " + ", ".join(st["decisions"][-10:]))


def cmd_knows(a):
    w = sim._w(a, False)
    e = _npc(w, a.id)
    F = sim.load_facts(w.dir)
    ks = F["known"].get(a.id, {})
    for s in ("sabe", "acredita", "suspeita"):
        rows = sorted(f for f, k in ks.items() if k["status"] == s)
        print(f"{s.upper()}:" + ("" if rows else " —"))
        for f in rows:
            fx = F["facts"].get(f, {})
            print(f"  {f}{'' if fx.get('true', True) else ' (FALSO)'} — {fx.get('text', '?')} (fonte {ks[f].get('source', '?')}, confiança {ks[f].get('confidence', '?')})")
    me = {a.id, e["meta"].get("faction")} - {None}
    unk = sorted(f for f, fx in F["facts"].items() if f not in ks and me & set(fx.get("about", [])))
    print("DESCONHECE (fatos sobre ele ou sua facção):" + ("" if unk else " —"))
    for f in unk:
        print(f"  {f} — {F['facts'][f].get('text', '?')}")


def cmd_history(a):
    w = sim._w(a, False)
    _npc(w, a.id)
    evs = sorted((e for e in w.events() if world._touches(e, a.id) and e["status"] not in ACTIVE), key=sim._order)
    if not evs:
        print(f"Nenhum evento registrado para {a.id}.")
    for e in evs[-a.limit:]:
        print(world._line(e))


def cmd_set(a):
    w = sim._w(a, False)
    _npc(w, a.id)
    S = load(w.dir)
    st = S["npcs"].setdefault(a.id, {})
    P = []
    if a.tier is not None:
        st["tier"] = a.tier
    if a.routine is not None:
        st["routine"] = sim._json_arg(a.routine, "--routine")
        P += check_routine(w, st["routine"])
    if a.goals is not None:
        st["goals"] = sim._json_arg(a.goals, "--goals")
        P += check_goals(w, st["goals"])
    for k in ("needs", "fears", "motivations"):
        if getattr(a, k) is not None:
            st[k] = getattr(a, k)
    if P:
        raise sim.SimError("nada foi gravado:\n  " + "\n  ".join(P))
    sim.save_json(st_path(w.dir), S)
    if a.routine is not None:  # rotina nova: cancela fronteiras já geradas pela antiga
        for e in w.events():
            if e["status"] == "pendente" and (e.get("gen_key") or "").startswith(f"routine:{a.id}:"):
                w.set_status(e["id"], "cancelado", {"motivo": "rotina alterada"})
    print(f"{a.id} atualizado em {st_path(w.dir).relative_to(w.dir)}")


def cmd_decide(a):
    w = sim._w(a)
    if not a.commit:
        _print_reason(evaluate(w, a.id))
        print("(só avaliação: nada foi gravado — use --commit para registrar e agendar)")
        return
    did, res, act = decide(w, a.id, f"npc-decide:{a.id}:{a.op or w.now}", _cause(w, a.cause))
    _print_reason(res)
    print(f"Decisão registrada: {did} [MESTRE]" + (f"; ação agendada: {act} para {w.fmt(w.event(act)['due'])}" if act else ""))


def cmd_propose(a):
    w = sim._w(a)
    act = sim._json_arg(a.action, "--action")
    pid, aid = propose(w, a.id, act[0] if len(act) == 1 else act, f"npc-propose:{a.id}:{a.op}" if a.op else None,
                       _cause(w, a.cause))
    print(f"Proposta do mestre validada: {pid} [MESTRE]; ação agendada {aid} para {w.fmt(w.event(aid)['due'])}")


def cmd_why(a):
    w = sim._w(a, False)
    e = _cause(w, a.id) and w.event(a.id)
    if e.get("type") == "npc.acao" and e.get("cause"):
        print(f"{e['id']} ({e['status']}) é a ação '{e.get('text')}', agendada por {e['cause']}:")
        e = w.event(e["cause"])
    if e.get("type") not in ("npc.decisao", "npc.proposta") or not isinstance(e.get("result"), dict):
        raise sim.SimError(f"{a.id} não é decisão/proposta/ação de NPC")
    print(f"{e['id']} [{e.get('date')}]" + (f" ← causa {e['cause']}" if e.get("cause") else ""))
    _print_reason(e["result"])


def register(sp):
    n = sp.add_parser("npc", help="NPCs autônomos: rotina, decisões, propostas do mestre, viagens").add_subparsers(dest="ncmd", required=True)
    c = n.add_parser("show", help="frontmatter, estado, rotina, objetivos, conhecimento e agenda pendente")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=sim._wrap(cmd_show))
    c = n.add_parser("knows", help="o que o NPC sabe/acredita/suspeita e o que desconhece sobre si/sua facção")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=sim._wrap(cmd_knows))
    c = n.add_parser("history", help="decisões e eventos do NPC")
    c.add_argument("id"); c.add_argument("--limit", type=int, default=30); c.add_argument("--campaign"); c.set_defaults(f=sim._wrap(cmd_history))
    c = n.add_parser("set", help="define nível, rotina, objetivos, necessidades, medos, motivações (validado, atômico)")
    c.add_argument("id"); c.add_argument("--tier", type=int, choices=[1, 2, 3])
    c.add_argument("--routine", help="JSON ou @arquivo.json"); c.add_argument("--goals", help="JSON ou @arquivo.json")
    c.add_argument("--needs"); c.add_argument("--fears"); c.add_argument("--motivations"); c.add_argument("--campaign")
    c.set_defaults(f=sim._wrap(cmd_set))
    c = n.add_parser("decide", help="avalia objetivos; com --commit registra a decisão e agenda a ação")
    c.add_argument("id"); c.add_argument("--commit", action="store_true"); c.add_argument("--op", help="id único (idempotência)")
    c.add_argument("--cause", help="evt-* que motivou a decisão"); c.add_argument("--campaign"); c.set_defaults(f=sim._wrap(cmd_decide))
    c = n.add_parser("propose", help="ação sugerida pelo mestre: validada e só então agendada")
    c.add_argument("id"); c.add_argument("--action", required=True, help='JSON {desc, preconditions, effects, duration} ou @arquivo')
    c.add_argument("--op", help="id único (idempotência)"); c.add_argument("--cause"); c.add_argument("--campaign")
    c.set_defaults(f=sim._wrap(cmd_propose))
    c = n.add_parser("why", help="raciocínio registrado de uma decisão/proposta (ou da ação que ela agendou)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=sim._wrap(cmd_why))
