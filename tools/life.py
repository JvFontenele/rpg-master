"""Vida cotidiana: compromissos recorrentes ou únicos (trabalho, aulas, provas, encontros, tarefas, lazer),
empregos e cursos ligados à economia, e presença DECLARADA pelo jogador.

Estado: state/sim/life.json = {
  "commitments": {"cmp-id": {"who": "pc-x", "kind": <KIND_DEPTH>, "text", "loc"?, "with": [npc…]?, "move_with": bool,
                  "when": {"at": "HH:MM", "days": [...]?, "monthday": N?, "skip_rest": bool?} | "once": <minuto>,
                  "until": <minuto>?, "duration": "4h"?, "important": bool, "hidden": bool,
                  "on_attend"/"on_late"/"on_miss": [efeitos], "route": "<rota da economia>"?, "status": "ativo|encerrado",
                  "history": [{"at", "status", "reason", "event"}]}},
  "attendance": {"cmp-id@<minuto>": {"status": presente|atrasou|faltou, "at", "event", "note", "key"}},
  "_applied": [...]}

Três coisas distintas, nunca confundidas:
  1. compromisso agendado — o gerador cria um LEMBRETE (evento 'compromisso', gen_key life:<cmp>:<t>); não move o PJ,
     não decide nada; com move_with, só os NPCs de 'with' (livres e vivos) vão ao local.
  2. ação declarada pelo jogador — 'life attend' (presente/atrasou/faltou), idempotente por --op e única por ocorrência.
     Sem declaração, NADA acontece: ausência de registro não é falta.
  3. consequência registrada — os efeitos on_attend/on_late/on_miss, com chaves <evento>#0/<j>, aplicados uma vez.
A profundidade (campaign.yaml 'depth') só decide quais compromissos ROTINEIROS viram lembrete; 'important' sempre vira.
Visibilidade: compromissos, empregos e cursos de NPCs são do mestre por padrão; os do PJ são públicos (o jogador sabe).
--public/--hidden decidem explicitamente; a marca fica no compromisso e em TODOS os eventos dele.
Toda entrada é validada ANTES de criar o evento (entidades, valores, rota, consequências, causa): entrada inválida
não deixa evento falho, rota órfã nem compromisso pela metade.
Estilos, ritmo e autonomia não são lidos aqui.
"""
import re
from pathlib import Path

import sim
from rpg import camp_dir, die, prefs

KIND_DEPTH = {"prova": "leve", "entrevista": "leve", "evento": "leve", "trabalho": "equilibrada", "estudo": "equilibrada",
              "social": "equilibrada", "saude": "equilibrada", "compromisso": "equilibrada", "tarefa": "profunda",
              "lazer": "profunda", "rotina": "profunda"}
DEPTHS = ("leve", "equilibrada", "profunda")
ATTEND = {"presente": "on_attend", "atrasou": "on_late", "faltou": "on_miss"}
LOOKBACK_DAYS = 62  # 'life attend' sem --at procura a ocorrência mais recente nesta janela


def path(cdir):
    return Path(cdir) / "state" / "sim" / "life.json"


def load(cdir):
    L = sim.load_json(path(cdir), {})
    for k in ("commitments", "attendance"):
        L.setdefault(k, {})
    return L


def emits(depth, c):
    """O compromisso vira lembrete nesta profundidade? Importantes sempre; rotineiros conforme o nível."""
    return c.get("important") or DEPTHS.index(depth) >= DEPTHS.index(KIND_DEPTH.get(c["kind"], "equilibrada"))


def occurrences(w, c, t0, t1):
    if c.get("once") is not None:
        return [c["once"]] if t0 < c["once"] <= t1 else []
    created = (c.get("history") or [{}])[0].get("at")
    if created is not None:  # recorrência não existe antes de o compromisso ser criado
        t0 = max(t0, created - 1)
    wh = c["when"]
    end = min(t1, c["until"]) if c.get("until") is not None else t1
    return list(sim.occurrences(t0, end, wh["at"], w.cal, wh.get("days"), wh.get("skip_rest", False), wh.get("monthday")))


def problems(w, cid, c, L=None):
    P = []
    if not re.fullmatch(r"cmp-[a-z0-9][a-z0-9-]*", cid):
        P.append(f"id '{cid}' deve começar com 'cmp-' (minúsculas/hífen)")
    if not w.exists(c.get("who", "")):
        P.append(f"'{c.get('who')}' não existe")
    if c.get("kind") not in KIND_DEPTH:
        P.append(f"tipo '{c.get('kind')}' inválido ({', '.join(KIND_DEPTH)})")
    for x in [c.get("loc")] + list(c.get("with", [])):
        if x and not w.exists(x):
            P.append(f"'{x}' não existe")
    if c.get("move_with") and not c.get("loc"):
        P.append("move_with exige 'loc'")
    if (c.get("once") is None) == (not c.get("when")):
        P.append("informe 'when' (recorrente) OU 'once' (único)")
    try:
        if c.get("when"):
            sim.hhmm(c["when"].get("at"), w.cal)
            sim.check_days(c["when"].get("days"), w.cal)
        if c.get("duration"):
            sim.parse_duration(c["duration"], w.cal)
    except sim.SimError as e:
        P.append(str(e))
    for k in ATTEND.values():
        P += sim.check_effects(w, c.get(k, []), k)
    if c.get("route"):
        import economy
        try:
            economy._entry(economy.load(w.dir), c["route"])
        except sim.SimError as e:
            P.append(str(e))
    return P


# ---------------------------------------------------------------- efeitos
@sim.effect("life.define")
def _fx_define(w, f, key, ev):
    """Cria um compromisso (e, opcionalmente, a rota/conta ligada, com economy: on). Idempotente pela chave."""
    L = load(w.dir)
    cid, c = f["id"], dict(f["commitment"])
    if key in L.get("_applied", []):
        return {"compromisso": cid}
    if cid in L["commitments"]:
        raise sim.SimError(f"compromisso '{cid}' já existe")
    if f.get("route"):
        import economy
        E = economy.load(w.dir)
        r = f["route"]
        cur = next((x for x in E["routes"] if x["id"] == r["id"]), None)
        if cur is None:  # retomada: a rota já gravada (mesma definição) não é recriada
            economy._need_on(w)
            E["routes"].append(r)
            P = economy.problems(w, E)
            if P:
                raise sim.SimError("rota recusada: " + "; ".join(P))
            sim.save_json(economy.path(w.dir), E)
        elif {k: cur.get(k) for k in r} != r:
            raise sim.SimError(f"já existe outra rota '{r['id']}'")
        c["route"] = r["id"]
    c.setdefault("status", "ativo")
    c["history"] = [{"at": w.now, "status": "ativo", "reason": f.get("reason", ""), "event": ev["id"]}]
    P = problems(w, cid, c)
    if P:
        raise sim.SimError("; ".join(P))
    L["commitments"][cid] = c
    sim.once(L, key)
    sim.save_json(path(w.dir), L)
    return {"compromisso": cid, "rota": c.get("route")}


@sim.effect("life.status")
def _fx_status(w, f, key, ev):
    """Encerra (ou reativa) um compromisso; a rota ligada muda junto (econ.route.status, mesma chave)."""
    L = load(w.dir)
    c = L["commitments"].get(f["id"])
    if not c:
        raise sim.SimPermanent(f"compromisso '{f['id']}' não existe")
    if sim.once(L, key):
        c["status"] = f["status"]
        c.setdefault("history", []).append({"at": w.now, "status": f["status"], "reason": f.get("reason", ""), "event": ev["id"]})
        sim.save_json(path(w.dir), L)
    if c.get("route") and _route_state(w, c["route"]) != "removida":  # rota removida já está no estado final
        # a conta ligada acompanha a MESMA decisão: autorização passada explicitamente só se este efeito é a declaração
        auth = "declaração do jogador ('life end')" if sim.declared_effect(ev, f) else None
        sim.apply(w, {"type": "econ.route.status", "route": c["route"], "reason": f.get("reason", ""),
                      "status": "ativa" if f["status"] == "ativo" else "interrompida"}, key + "/rota", ev, auth=auth)
    return {"compromisso": f["id"], "status": f["status"]}


sim.composite("life.status")  # muda a conta ligada por dentro


@sim.effect("life.occur")
def _fx_occur(w, f, key, ev):
    """Lembrete de uma ocorrência. NUNCA move nem decide pelo PJ; com move_with leva os NPCs livres de 'with'."""
    c = load(w.dir)["commitments"][f["id"]]
    moved = []
    if c.get("move_with") and c.get("loc"):
        for j, npc in enumerate(c.get("with", [])):
            if npc.startswith("npc-") and ("npc.free" not in sim.CONDITIONS or sim.check(w, {"type": "npc.free", "npc": npc})[0]):
                sim.apply(w, {"type": "entity.set", "id": npc, "field": "location", "value": c["loc"]}, f"{key}/{j}", ev)
                moved.append(npc)
    return {"lembrete": f["id"], "quem": c["who"], "npcs_no_local": moved}


sim.composite("life.occur", lambda f: True)  # move NPCs participantes


@sim.effect("life.attend")
def _fx_attend(w, f, key, ev):
    """Registra o que o JOGADOR declarou para UMA ocorrência. Desde a Fase 3.1.1 as consequências são EFEITOS SEPARADOS
    do mesmo evento (inline: false), então o progresso de cada uma fica gravado e a reconciliação é exata.
    Eventos antigos (sem 'inline') aplicam as consequências aqui dentro, como antes."""
    L = load(w.dir)
    c = L["commitments"][f["id"]]
    occ = f"{f['id']}@{f['at']}"
    cur = L["attendance"].get(occ)
    if cur and cur["key"] != key:
        raise sim.SimPermanent(f"{occ} já registrado como '{cur['status']}' ({cur['event']}) — corrija com um novo evento, não repita")
    if not cur:
        L["attendance"][occ] = {"status": f["status"], "at": w.now, "event": ev["id"], "note": f.get("note", ""), "key": key}
        sim.save_json(path(w.dir), L)
    effs = c.get(ATTEND[f["status"]], []) if f.get("inline", True) else []
    for j, eff in enumerate(effs):
        sim.apply(w, eff, f"{key}/{j}", ev)
        w.reload()
    return {"compromisso": f["id"], "ocorrencia": w.fmt(f["at"]), "status": f["status"],
            "consequencias": len(effs) if f.get("inline", True) else len(ev.get("effects", [])) - 1}


sim.composite("life.attend", lambda f: f.get("inline", True))  # só o formato antigo embute consequências


# ---------------------------------------------------------------- agência (regras da guarda central, sim.agency_problem)
def _payer(w, rid):
    import economy
    kx = economy._entry_or_none(w, rid) if rid else None
    return kx[1].get("from") if kx and kx[0] == "routes" else None


def _pc_part(w, c, route=None):
    """O PJ é parte desta decisão (compromisso dele ou conta ligada que ele paga)? Então o comando registra a declaração."""
    return sim._pc(c.get("who")) or sim._pc((route or {}).get("from") or _payer(w, c.get("route")))


@sim.agency_rule("life.attend")
def _ag_attend(w, f, ev):
    """Presença, atraso ou falta do PJ é o que o jogador declarou ('life attend'); compromisso desconhecido: restritivo."""
    c = load(w.dir)["commitments"].get(f.get("id")) if w else None
    if c is None or sim._pc(c.get("who")):
        return f"registrar '{f.get('status')}' em {f.get('id')} pelo protagonista (só o jogador declara: use 'life attend')"


@sim.agency_rule("life.define")
def _ag_define(w, f, ev):
    """Conta que debita o PJ criada por dados traria um 'authorized' forjado: só 'life job/course --authorized'."""
    r = f.get("route") or {}
    if sim._pc(r.get("from")):
        return f"criar a conta '{r.get('id')}' debitando {r['from']} (só 'life job/course --authorized' registra a autorização do jogador)"


@sim.agency_rule("life.status")
def _ag_status(w, f, ev):
    c = load(w.dir)["commitments"].get(f.get("id")) if w and f.get("status") == "ativo" else None
    payer = _payer(w, c.get("route")) if c else None
    if sim._pc(payer):
        return f"reativar {f.get('id')}, cuja conta volta a debitar {payer} (só 'life end --reactivate' com a decisão do jogador)"


# ---------------------------------------------------------------- gerador e verificador
@sim.generator
def _gen(w, t0, t1):
    L = load(w.dir)
    if not L["commitments"]:
        return
    depth = prefs(w.dir)["depth"]
    for cid, c in sorted(L["commitments"].items()):
        if c.get("status", "ativo") != "ativo" or not emits(depth, c):
            continue
        for t in occurrences(w, c, t0, t1):
            span = f" até {w.fmt(t + sim.parse_duration(c['duration'], w.cal))[-5:]}" if c.get("duration") else ""
            w.schedule({"type": "compromisso", "text": f"Compromisso de {c['who']}: {c['text']}{span}"
                        + (f" em {c['loc']}" if c.get("loc") else ""), "due": t, "priority": 45,
                        "entities": [c["who"]] + list(c.get("with", [])), "loc": c.get("loc"), "hidden": bool(c.get("hidden")),
                        "effects": [{"type": "life.occur", "id": cid}]}, gen_key=f"life:{cid}:{t}")


@sim.verifier
def _verify(w):
    P = []
    L = load(w.dir)
    for cid, c in sorted(L["commitments"].items()):
        P += [f"[vida] {cid}: {p}" for p in problems(w, cid, c)]
    import economy
    if not economy.enabled(w.dir):
        for cid, c in sorted(L["commitments"].items()):
            if c.get("route") and c.get("status", "ativo") == "ativo" and _route_state(w, c["route"]) == "ativa":
                P.append(f"[vida] {cid}: a conta ligada '{c['route']}' está parada porque 'economy' está desligada "
                         f"(nada é pago nem cobrado; religue a economia ou encerre o compromisso com 'life end')")
    for occ in L["attendance"]:
        if occ.split("@")[0] not in L["commitments"]:
            P.append(f"[vida] presença registrada para compromisso inexistente: {occ}")
    return P


def _route_state(w, rid):
    import economy
    try:
        return economy._entry(economy.load(w.dir), rid)[1].get("status", "ativa")
    except sim.SimError:
        return None


# ---------------------------------------------------------------- CLI
def _w(a):
    sim.load_modules()
    return sim.World(camp_dir(a.campaign))


def _ids(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _effs(s, what):
    return sim._json_arg(s, what) if s else []


def _hidden(a):
    if a.public:
        return False
    return True if a.hidden else not a.who.startswith("pc-")  # NPC: do mestre; PJ: o jogador sabe


def _spec(w, a, kind):
    c = {"who": a.who, "kind": kind, "text": a.text}
    if a.once:
        c["once"] = sim.parse_when(a.once, w.cal, w.now)
    else:
        if not a.at:
            die("informe --at HH:MM (recorrente) ou --once 'A/M/D HH:MM' (único)")
        c["when"] = {k: v for k, v in (("at", a.at), ("days", _ids(a.days) or None), ("monthday", a.monthday),
                                       ("skip_rest", a.skip_rest or None)) if v}
    if a.until:
        c["until"] = sim.parse_when(a.until, w.cal, w.now)
    for k, v in (("loc", a.loc), ("with", _ids(a.with_)), ("duration", a.duration), ("important", a.important),
                 ("move_with", a.move_with), ("hidden", _hidden(a))):
        if v:
            c[k] = v
    for k, arg in (("on_attend", "on_attend"), ("on_late", "on_late"), ("on_miss", "on_miss")):
        if getattr(a, arg, None):
            c[k] = _effs(getattr(a, arg), f"--{arg.replace('_', '-')}")
    return c


def _define(w, a, kind, route=None, text=None):
    gk = f"life:define:{a.op}"
    c = _spec(w, a, kind)
    if gk not in {e.get("gen_key") for e in w.events()}:  # valida TUDO antes de criar o evento
        if a.id in load(w.dir)["commitments"]:
            raise sim.SimError(f"compromisso '{a.id}' já existe")
        P = problems(w, a.id, {**c, "route": None})
        if route:
            import economy
            E = economy.load(w.dir)
            if any(x["id"] == route["id"] for k in economy.KINDS for x in E[k]):
                P.append(f"já existe entrada econômica '{route['id']}' (escolha outro id de compromisso)")
            else:
                E["routes"].append(route)
                P += [f"conta: {p}" for p in economy.problems(w, E)]
        if a.cause and not w.event(a.cause):
            P.append(f"causa '{a.cause}' não existe")
        if P:
            raise sim.SimError("nada foi criado: " + "; ".join(P))
    f = {"type": "life.define", "id": a.id, "commitment": c, "reason": a.reason}
    if route:
        f["route"] = route
    e, done = sim.apply_now(w, {"type": "vida", "text": text or f"Novo compromisso de {a.who}: {a.text}",
                                "entities": [a.who] + c.get("with", []), "loc": c.get("loc"), "hidden": bool(c.get("hidden")),
                                "cause": a.cause, "effects": [f]}, gk, declared=_pc_part(w, c, route))  # PJ: decisão declarada
    r = e["effects"][0]  # o que foi registrado
    rc = r["commitment"]
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: compromisso {r['id']} ({rc['kind']}) de {rc['who']}"
          + (f", com conta {r['route']['id']}" if r.get("route") else "") + f" — {_when_txt(w, rc)}"
          + (" [MESTRE]" if rc.get("hidden") else ""))


def _route(w, a, rid, frm, to, qty, days, at, monthday):
    """Monta a conta ligada (validação completa fica em _define, antes de criar o evento)."""
    import economy
    economy._need_on(w)
    if not (days or monthday):
        raise sim.SimError("pagamento precisa de dias (--pay-days/--fee-days) ou dia do mês (--pay-monthday/--fee-monthday)")
    for x in (frm, to):
        if not w.exists(x):
            raise sim.SimError(f"'{x}' não existe — nada foi criado")
    wh = {k: v for k, v in (("at", at or "09:00"), ("days", _ids(days) or None), ("monthday", monthday)) if v}
    r = {"id": rid, "from": frm, "to": to, "res": a.res, "qty": economy._num(qty), "when": wh, "status": "ativa", "bill": True}
    if a.authorized:  # débito automático do PJ só com a autorização declarada (validado em economy.problems)
        r["authorized"] = a.authorized
    return r


def cmd_add(a):
    w = _w(a)
    _define(w, a, a.kind)


def cmd_job(a):
    """Emprego ACEITO pelo jogador: expediente (trabalho) + salário como conta do empregador (economy: on)."""
    w = _w(a)
    route = _route(w, a, f"sal-{a.id[4:]}", a.employer, a.who, a.salary, a.pay_days, a.pay_at, a.pay_monthday) if a.salary else None
    _define(w, a, "trabalho", route, f"{a.who} começa a trabalhar: {a.text} ({a.employer})")


def cmd_course(a):
    """Curso: aulas (estudo) + mensalidade opcional como conta do aluno (economy: on). Provas: 'life add --kind prova'."""
    w = _w(a)
    route = _route(w, a, f"mens-{a.id[4:]}", a.who, a.school, a.fee, a.fee_days, a.fee_at, a.fee_monthday) if a.fee else None
    _define(w, a, "estudo", route, f"{a.who} começa um curso: {a.text} ({a.school})")


def cmd_end(a):
    w = _w(a)
    c = load(w.dir)["commitments"].get(a.id) or die(f"compromisso '{a.id}' não existe")
    gk = f"life:status:{a.op}"
    st = "ativo" if a.reactivate else "encerrado"
    if gk not in {e.get("gen_key") for e in w.events()}:  # valida antes de criar o evento
        import economy
        if c.get("status", "ativo") == st:
            raise sim.SimError(f"{a.id} já está '{st}'")
        if a.cause and not w.event(a.cause):
            raise sim.SimError(f"causa '{a.cause}' não existe")
        rs = _route_state(w, c["route"]) if c.get("route") else None
        if c.get("route") and rs is None:
            raise sim.SimError(f"a conta ligada '{c['route']}' não existe mais — veja 'world verify'")
        if a.reactivate and rs == "removida":
            raise sim.SimError(f"a conta ligada '{c['route']}' foi removida — crie um novo compromisso para um novo vínculo")
        if c.get("route") and rs != "removida" and not economy.enabled(w.dir):
            raise sim.SimError(f"a conta ligada '{c['route']}' exige 'economy: on' para mudar de status — religue a economia antes")
    e, done = sim.apply_now(w, {"type": "vida", "text": f"Compromisso {a.id} de {c['who']} {st}: {a.reason}",
                                "entities": [c["who"]], "hidden": bool(c.get("hidden")), "cause": a.cause,
                                "effects": [{"type": "life.status", "id": a.id, "status": st, "reason": a.reason}]}, gk,
                              declared=_pc_part(w, c))  # demissão/trancar/voltar: decisão declarada do PJ
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {a.id} {st}" + (" (conta ligada acompanha)" if c.get("route") else ""))


def last_occurrence(w, c):
    mpd = sim._units(w.cal)[1]
    occ = occurrences(w, c, max(-1, w.now - LOOKBACK_DAYS * mpd), w.now)
    return occ[-1] if occ else None


def cmd_attend(a):
    """O JOGADOR declarou presença, atraso ou falta numa ocorrência que já passou (ou está acontecendo)."""
    w = _w(a)
    L = load(w.dir)
    c = L["commitments"].get(a.id) or die(f"compromisso '{a.id}' não existe")
    gk = f"life:attend:{a.op}"
    old = next((e for e in w.events() if e.get("gen_key") == gk), None)
    if old:  # repetição/retomada: mesma ocorrência registrada antes
        t = old["effects"][0]["at"]
    else:
        if a.at:
            t = sim.parse_when(a.at, w.cal, w.now)
            if t not in occurrences(w, c, t - 1, t):
                raise sim.SimError(f"{a.id} não tem ocorrência em {w.fmt(t)}")
        else:
            t = last_occurrence(w, c)
            if t is None:
                raise sim.SimError(f"{a.id}: nenhuma ocorrência nos últimos {LOOKBACK_DAYS} dias — use --at")
        if t > w.now:
            raise sim.SimError("não dá para registrar presença numa ocorrência futura")
        if f"{a.id}@{t}" in L["attendance"]:
            r = L["attendance"][f"{a.id}@{t}"]
            raise sim.SimError(f"ocorrência de {w.fmt(t)} já registrada como '{r['status']}' ({r['event']})")
    if a.cause and not w.event(a.cause):
        raise sim.SimError(f"causa '{a.cause}' não existe")
    cause = a.cause or next((e["id"] for e in w.events() if e.get("gen_key") == f"life:{a.id}:{t}"), None)
    e, done = sim.apply_now(w, {"type": "compromisso.presenca", "text": f"{c['who']} — {c['text']} ({w.fmt(t)}): {a.status}"
                                + (f" — {a.note}" if a.note else ""), "entities": [c["who"]], "loc": c.get("loc"),
                                "hidden": bool(c.get("hidden")), "cause": cause,
                                "effects": [{"type": "life.attend", "id": a.id, "at": t, "status": a.status, "note": a.note,
                                             "inline": False}] + c.get(ATTEND[a.status], [])}, gk,
                              declared=_pc_part(w, c))  # a marca cobre só a presença (#0), nunca as consequências
    r = e["result"]["efeitos"][0]
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {r['status']} em {r['ocorrencia']}"
          + (f"; {r['consequencias']} consequência(s) aplicada(s)" if r["consequencias"] else ""))


def _when_txt(w, c):
    if c.get("once") is not None:
        return w.fmt(c["once"])
    wh = c["when"]
    d = ", ".join(wh["days"]) if wh.get("days") else (f"dia {wh['monthday']} de cada mês" if wh.get("monthday") else "todo dia")
    return f"{d} às {wh['at']}" + (" (exceto folgas/feriados)" if wh.get("skip_rest") else "")


def cmd_agenda(a):
    """Próximas ocorrências. --public: só compromissos não ocultos de PJs, sem consequências nem notas do mestre."""
    w = _w(a)
    L = load(w.dir)
    t1 = w.now + sim.parse_duration(a.span, w.cal)
    rows = []
    for cid, c in sorted(L["commitments"].items()):
        if c.get("status", "ativo") != "ativo" or (a.who and c["who"] != a.who):
            continue
        if a.public and (c.get("hidden") or not c["who"].startswith("pc-")):
            continue
        rows += [(t, cid, c) for t in occurrences(w, c, w.now - 1, t1)]
    if not rows:
        print("Nenhum compromisso no período.")
    for t, cid, c in sorted(rows, key=lambda r: (r[0], r[1])):
        tag = "" if a.public else (" [MESTRE]" if c.get("hidden") else "") + ("" if emits(prefs(w.dir)["depth"], c) else " (resumido)")
        print(f"{w.fmt(t)}  {c['who']}  [{c['kind']}] {c['text']}" + (f" — {w.ctx.name(c['loc'])}" if c.get("loc") else "") + tag)


def cmd_show(a):
    w = _w(a)
    L = load(w.dir)
    c = L["commitments"].get(a.id) or die(f"compromisso '{a.id}' não existe")
    print(f"{a.id} [{c['kind']}] {c['who']}: {c['text']} — {_when_txt(w, c)} — {c.get('status', 'ativo')}")
    for k in ("loc", "with", "duration", "route", "important", "move_with", "hidden"):
        if c.get(k):
            print(f"  {k}: {c[k]}")
    for k in ATTEND.values():
        if c.get(k):
            print(f"  {k}: {c[k]}")
    for h in c.get("history", []):
        print(f"  {w.fmt(h['at'])} {h['status']} ({h['event']}) {h.get('reason', '')}")
    for occ, r in sorted(L["attendance"].items()):
        if occ.split("@")[0] == a.id:
            print(f"  presença {w.fmt(int(occ.split('@')[1]))}: {r['status']} ({r['event']}) {r.get('note', '')}")


def register(sp):
    wrap = sim._wrap
    t = sp.add_parser("life", help="vida cotidiana: compromissos, emprego, curso, presença declarada, agenda").add_subparsers(dest="lcmd", required=True)

    def common(c, op=True):
        c.add_argument("id", help="cmp-…"); c.add_argument("--who", required=True); c.add_argument("--text", required=True)
        c.add_argument("--at", help="HH:MM (recorrente)"); c.add_argument("--days", help="dias da semana, por vírgula")
        c.add_argument("--monthday", type=int); c.add_argument("--skip-rest", action="store_true", help="pula folgas e feriados")
        c.add_argument("--once", help="'A/M/D HH:MM' ou '+2d' (compromisso único)"); c.add_argument("--until", help="fim da recorrência")
        c.add_argument("--loc"); c.add_argument("--with", dest="with_", help="NPCs, por vírgula"); c.add_argument("--duration")
        c.add_argument("--move-with", action="store_true", help="os NPCs de --with vão ao local na hora (se livres)")
        c.add_argument("--important", action="store_true", help="sempre vira lembrete, em qualquer profundidade")
        g = c.add_mutually_exclusive_group()
        g.add_argument("--hidden", action="store_true", help="só o mestre vê (padrão para NPCs)")
        g.add_argument("--public", action="store_true", help="os jogadores veem (padrão para PJs)")
        c.add_argument("--on-attend", dest="on_attend", help="efeitos JSON")
        c.add_argument("--on-late", dest="on_late"); c.add_argument("--on-miss", dest="on_miss")
        c.add_argument("--reason", default=""); c.add_argument("--cause"); c.add_argument("--res", default="dinheiro")
        c.add_argument("--op", required=True, help="id único da ação (evita repetir)"); c.add_argument("--campaign")

    c = t.add_parser("add", help="compromisso (aula, prova, encontro, tarefa, consulta…)"); common(c)
    c.add_argument("--kind", required=True, choices=list(KIND_DEPTH)); c.set_defaults(f=wrap(cmd_add))
    c = t.add_parser("job", help="emprego aceito pelo jogador: expediente + salário (conta do empregador)"); common(c)
    c.add_argument("--employer", required=True); c.add_argument("--salary"); c.add_argument("--pay-days")
    c.add_argument("--pay-monthday", type=int); c.add_argument("--pay-at")
    c.add_argument("--authorized", help="o que o jogador autorizou (obrigatório se o PJ paga o salário)"); c.set_defaults(f=wrap(cmd_job))
    c = t.add_parser("course", help="curso: aulas + mensalidade opcional"); common(c)
    c.add_argument("--school", required=True); c.add_argument("--fee"); c.add_argument("--fee-days")
    c.add_argument("--fee-monthday", type=int); c.add_argument("--fee-at")
    c.add_argument("--authorized", help="o que o jogador autorizou (obrigatório se o PJ paga a mensalidade)")
    c.set_defaults(f=wrap(cmd_course))
    c = t.add_parser("end", help="encerra (ou --reactivate) um compromisso; a conta ligada acompanha")
    c.add_argument("id"); c.add_argument("--reason", required=True); c.add_argument("--op", required=True)
    c.add_argument("--reactivate", action="store_true"); c.add_argument("--cause"); c.add_argument("--campaign")
    c.set_defaults(f=wrap(cmd_end))
    c = t.add_parser("attend", help="registra o que o JOGADOR declarou: presente, atrasou ou faltou")
    c.add_argument("id"); c.add_argument("status", choices=list(ATTEND)); c.add_argument("--at", help="ocorrência (padrão: a mais recente)")
    c.add_argument("--note", default=""); c.add_argument("--op", required=True); c.add_argument("--cause"); c.add_argument("--campaign")
    c.set_defaults(f=wrap(cmd_attend))
    c = t.add_parser("agenda", help="próximas ocorrências (--public: visão do jogador)")
    c.add_argument("--who"); c.add_argument("--span", default="7d"); c.add_argument("--public", action="store_true")
    c.add_argument("--campaign"); c.set_defaults(f=wrap(cmd_agenda))
    c = t.add_parser("show", help="detalhes, histórico e presenças de um compromisso (mestre)")
    c.add_argument("id"); c.add_argument("--campaign"); c.set_defaults(f=wrap(cmd_show))
