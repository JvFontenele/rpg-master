"""Economia OPCIONAL: produção, consumo, rotas, preços e trocas. Ligada só com 'economy: on' no campaign.yaml.

Estado: state/sim/economy.json = {
  "markets":   {loc-id: {"goods": {bem: {"base_price", "demand", "supply", "stock_holder"?}}}},
  "producers": [{"id", "who", "res", "qty", "every": "1d", "source"?}],
  "consumers": [{"id", "who", "res", "qty", "every"}],
  "routes":    [{"id", "from", "to", "res", "qty", "every", "status": "ativa|interrompida", "history"?}],
  "_applied":  [...]}
Saldos ficam SÓ em state/sim/resources.json (núcleo). Cada ciclo vira um evento com gen_key econ:<id>:<n>,
onde n = índice do período desde t=0 — reprocessar ou avançar de novo nunca duplica.
Preço: base_price × clamp(demand/supply, 0.5, 3.0); supply = saldo do stock_holder, se houver; supply 0 → teto.

Recorrência por calendário (opcional, no lugar de "every"): "when": {"at": "HH:MM", "days": [dias da semana],
"monthday": N, "skip_rest": bool} → ocorrências determinísticas (sim.occurrences), gen_key econ:<id>:t<instante>.

Contas ("bill": true, só em rotas: pagador = from, credor = to) — salário, aluguel, mensalidade, parcela:
  a decisão de cada ocorrência (devido, pago, faltou) é gravada UMA vez em "_bills" junto com o atraso acumulado
  ("arrears"); depois aplica o pagamento (chave do evento), registra o evento de atraso (gen_key atraso:<chave>)
  e as consequências "on_arrears" (efeitos com chaves <chave>/atraso/<j>). Retomar/repetir nunca duplica.
  "partial": true paga o que houver e deixa o resto em atraso; sem ele, saldo insuficiente = nada pago.
  Quitação: 'econ pay ... --route <id>' (efeito econ.settle).
'econ set' faz upsert por id (nunca apaga; não mexe em arrears/history/status). Remoção só por 'econ remove'
(recusa entrada em uso por compromisso ativo ou com atraso sem --forgive; marca status "removida", com histórico).
Visibilidade: transações e saldos iniciais são do mestre por padrão; só o que envolve um PJ é público (--public/--hidden).
Com economy: on, o saldo em resources.json é a ÚNICA fonte de verdade do dinheiro (a ficha não guarda número).
"""
import re
from pathlib import Path

import sim
from rpg import camp_dir, read_meta

KINDS = ("producers", "consumers", "routes")
ROUTE_ST = ("ativa", "interrompida")  # transições por efeito (econ.route.status)
FINAL = "removida"  # estado final de qualquer entrada: só por 'econ remove' (auditado; o registro e o histórico ficam)
MANAGED = ("arrears", "history", "status")  # gerenciados pelo motor: 'econ set' não os altera
RATIO = (0.5, 3.0)
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def path(cdir):
    return Path(cdir) / "state" / "sim" / "economy.json"


def load(cdir):
    E = sim.load_json(path(cdir), {})
    for k, v in (("markets", {}), ("producers", []), ("consumers", []), ("routes", []), ("_applied", [])):
        E.setdefault(k, v)
    return E


def enabled(cdir):
    return read_meta(Path(cdir) / "campaign.yaml").get("economy") == "on"


def _need_on(w):
    if not enabled(w.dir):
        raise sim.SimError("economia desligada nesta campanha (adicione 'economy: on' ao campaign.yaml)")


def _entry(E, eid):
    for k in KINDS:
        for x in E[k]:
            if x["id"] == eid:
                return k, x
    raise sim.SimError(f"entrada econômica '{eid}' não existe")


def price(cdir, loc, good):
    g = load(cdir)["markets"].get(loc, {}).get("goods", {}).get(good)
    if not g:
        raise sim.SimError(f"{loc} não negocia '{good}'")
    supply = sim.balance(cdir, g["stock_holder"], good) if g.get("stock_holder") else g.get("supply", 0)
    ratio = max(RATIO[0], min(RATIO[1], g.get("demand", 0) / supply if supply > 0 else RATIO[1]))
    return round(g["base_price"] * ratio, 2), ratio, supply


# ---------------------------------------------------------------- ciclos
@sim.generator
def _gen(w, t0, t1):
    if not enabled(w.dir):
        return
    E = load(w.dir)
    for k in KINDS:
        for x in E[k]:
            if x.get("status", "ativa") != "ativa":  # rota interrompida ou entrada removida
                continue
            who = [x["from"], x["to"]] if k == "routes" else [x["who"]]
            if x.get("when"):
                wh = x["when"]
                for t in sim.occurrences(t0, t1, wh["at"], w.cal, wh.get("days"), wh.get("skip_rest", False), wh.get("monthday")):
                    w.schedule({"type": "economia", "text": f"Ciclo econômico {x['id']} ({x['res']} ×{x['qty']:g})", "due": t,
                                "hidden": True, "entities": who, "effects": [{"type": "econ.run", "entry": x["id"]}]},
                               gen_key=f"econ:{x['id']}:t{t}")
                continue
            per = sim.parse_duration(x["every"], w.cal)
            # ponytail: um evento por entrada e período; avanços de meses com muitas entradas ficam lentos
            # (events.jsonl é relido a cada evento) — agrupar por instante se isso pesar
            for n in range(t0 // per + 1, t1 // per + 1):
                w.schedule({"type": "economia", "text": f"Ciclo econômico {x['id']} ({x['res']} ×{x['qty']:g})", "due": n * per,
                            "hidden": True, "entities": who, "effects": [{"type": "econ.run", "entry": x["id"]}]},
                           gen_key=f"econ:{x['id']}:{n}")


@sim.effect("econ.run")
def _fx_run(w, f, key, ev):
    if not enabled(w.dir):
        return {"economia": "desligada"}
    k, x = _entry(load(w.dir), f["entry"])
    payer = payer_pc(k, x)
    if payer and not x.get("authorized"):  # entrada antiga, sem autorização: nada é debitado, nada vira atraso
        raise sim.SimPermanent(f"{x['id']} debitaria {payer} sem autorização do jogador ('authorized' na entrada) — nada foi "
                               f"debitado; {fix_hint(k, x)}", before_write=True)
    auth = x.get("authorized")
    if k == "routes" and x.get("bill"):
        return _run_bill(w, x["id"], key, ev)
    if k == "producers":
        return sim.apply(w, {"type": "resource.produce", "who": x["who"], "res": x["res"], "qty": x["qty"],
                             "source": x["id"]}, key, ev)
    if k == "routes" and x.get("status", "ativa") != "ativa":
        return {"rota": x["id"], "status": x["status"]}
    who = x["from"] if k == "routes" else x["who"]
    if any(e.get("gen_key") == f"escassez:{key}" for e in w.events()):  # escassez já decidida para este ciclo
        return {"escassez": "já registrada"}
    R = sim.load_res(w.dir)
    # débito AUTOMÁTICO de PJ nunca usa allow_negative (seria dívida sem consentimento): falta de saldo = escassez
    if key not in R.get("_applied", []) and (who not in R["allow_negative"] or sim._pc(who)):  # já aplicado → só repete o resultado
        have = R["holdings"].get(who, {}).get(x["res"], 0)
        if have < x["qty"]:
            why = f"{who} tem {have:g} {x['res']} (precisa {x['qty']:g})"
            w.record(f"Escassez: {x['id']} não ocorreu — {why}", "economia", hidden=True, cause=ev["id"], entities=[who],
                     result={"motivo": why}, gen_key=f"escassez:{key}")
            return {"escassez": why}
    if k == "routes":
        return sim.apply(w, {"type": "resource.transfer", "from": x["from"], "to": x["to"], "res": x["res"], "qty": x["qty"]}, key, ev,
                         auth=auth)
    return sim.apply(w, {"type": "resource.consume", "who": who, "res": x["res"], "qty": x["qty"]}, key, ev, auth=auth)


def payer_pc(k, x):
    """O PJ que esta entrada debitaria (rota: 'from'; consumidor: 'who'), ou None."""
    who = x.get("from") if k == "routes" else x.get("who") if k == "consumers" else None
    return who if sim._pc(who) else None


def fix_hint(k, x):
    return (f"autorize com econ set --json '{{\"{k}\": [{{\"id\": \"{x['id']}\", \"authorized\": \"<o que o jogador autorizou>\"}}]}}' "
            f"(só com o consentimento do jogador) ou encerre com 'econ remove {x['id']} --reason ... --op ...'; ciclos recusados "
            f"não são cobrados depois — registre cada um com 'time cancel <evt> --reason ...'")


def _entry_or_none(w, eid):
    try:
        return _entry(load(w.dir), eid) if w else None
    except sim.SimError:
        return None


@sim.agency_rule("econ.run")
def _ag_run(w, f, ev):
    """O débito autorizado vale só nas datas da própria entrada (evento do gerador, gen_key econ:<id>:…): um ciclo extra
    disparado por dados (mestre, NPC, facção, missão) cobraria o PJ fora do que ele autorizou."""
    kx = _entry_or_none(w, f.get("entry"))
    payer = payer_pc(*kx) if kx else None
    if payer and not (ev and (ev.get("gen_key") or "").startswith(f"econ:{kx[1]['id']}:")):
        return f"disparar fora do calendário um ciclo de '{kx[1]['id']}', que debita {payer} (o débito autorizado só ocorre nas datas da entrada)"


@sim.agency_rule("econ.route.status")
def _ag_route(w, f, ev):
    """Reativar uma rota que debita o PJ retoma o débito automático: só pela decisão declarada ('life end --reactivate',
    que passa a autorização explicitamente) ou por nova entrada com 'authorized'. Interromper continua livre."""
    kx = _entry_or_none(w, f.get("route")) if f.get("status") == "ativa" else None
    if kx and kx[0] == "routes" and sim._pc(kx[1].get("from")):
        return f"reativar a rota '{kx[1]['id']}', que debita {kx[1]['from']} (só 'life end --reactivate' com a decisão do jogador)"


sim.composite("econ.run")  # conta: decisão + pagamento + atraso + consequências; escassez: registro


def _run_bill(w, rid, key, ev):
    """Uma ocorrência de conta. Etapas idempotentes, na ordem: decisão+atraso (gravação atômica única) →
    pagamento → evento de atraso → consequências. Uma falha em qualquer etapa é retomada sem repetir as anteriores."""
    E = load(w.dir)
    _, x = _entry(E, rid)
    dec = E.setdefault("_bills", {}).get(key)
    if dec is None:
        if x.get("status", "ativa") != "ativa":
            return {"rota": rid, "status": x["status"]}
        R = sim.load_res(w.dir)
        have = R["holdings"].get(x["from"], {}).get(x["res"], 0)
        q = x["qty"]
        neg = x["from"] in R["allow_negative"] and not sim._pc(x["from"])  # PJ: atraso, nunca saldo negativo automático
        paid = q if have >= q or neg else (round(max(have, 0), 6) if x.get("partial") else 0)
        dec = {"entry": rid, "due": q, "paid": paid, "short": round(q - paid, 6), "at": w.now, "event": ev["id"]}
        E["_bills"][key] = dec
        if dec["short"]:
            x["arrears"] = round(x.get("arrears", 0) + dec["short"], 6)
        sim.save_json(path(w.dir), E)
    out = {"conta": rid, "devido": dec["due"], "pago": dec["paid"], "faltou": dec["short"]}
    if dec["paid"]:
        sim.apply(w, {"type": "resource.transfer", "from": x["from"], "to": x["to"], "res": x["res"], "qty": dec["paid"]}, key, ev,
                  auth=x.get("authorized"))
        if payer_pc("routes", x):
            out["autorizacao"] = x.get("authorized")  # o que fundamentou o débito, no registro do próprio ciclo
    if dec["short"]:
        why = f"{x['from']} pagou {dec['paid']:g} de {dec['due']:g} {x['res']} ({rid}); atraso acumulado {x.get('arrears', 0):g}"
        out["evento_atraso"] = w.record(f"Atraso: {why}", "economia", hidden=True, cause=ev["id"], entities=[x["from"], x["to"]],
                                        result=dec, gen_key=f"atraso:{key}")
        for j, eff in enumerate(x.get("on_arrears", [])):
            sim.apply(w, eff, f"{key}/atraso/{j}", ev)
            w.reload()
    return out


@sim.effect("econ.settle")
def _fx_settle(w, f, key, ev):
    """Abate 'qty' do atraso de uma conta (o pagamento em si é um resource.transfer no mesmo evento)."""
    E = load(w.dir)
    k, x = _entry(E, f["route"])
    if k != "routes" or not x.get("bill"):
        raise sim.SimError(f"'{f['route']}' não é uma conta (rota com bill: true)")
    if sim.once(E, key):
        if f["qty"] > x.get("arrears", 0) + 1e-9:
            raise sim.SimError(f"{f['route']}: atraso é {x.get('arrears', 0):g}; não dá para quitar {f['qty']:g}")
        x["arrears"] = round(x.get("arrears", 0) - f["qty"], 6)
        sim.save_json(path(w.dir), E)
    return {"conta": x["id"], "atraso": x.get("arrears", 0)}


@sim.effect("econ.remove")
def _fx_remove(w, f, key, ev):
    """Remoção auditada: marca 'removida' (o registro, o histórico e o atraso ficam). --forgive zera o atraso
    registrando o valor perdoado no histórico."""
    E = load(w.dir)
    k, x = _entry(E, f["entry"])
    if sim.once(E, key):
        if x.get("status") == FINAL:
            raise sim.SimPermanent(f"'{x['id']}' já foi removida")
        h = {"at": w.now, "de": x.get("status", "ativa"), "para": FINAL, "reason": f.get("reason", ""), "cause": ev["id"]}
        if x.get("arrears"):
            if not f.get("forgive"):
                raise sim.SimError(f"'{x['id']}' tem atraso {x['arrears']:g} — quite ou use --forgive")
            h["perdoado"], x["arrears"] = x["arrears"], 0
        x.setdefault("history", []).append(h)
        x["status"] = FINAL
        sim.save_json(path(w.dir), E)
    return {"removida": x["id"], "perdoado": x.get("history", [{}])[-1].get("perdoado")}


@sim.precheck("econ.route.status")
def _pc_route(w, f):
    if f.get("status") not in ROUTE_ST:
        raise sim.SimPermanent(f"status de rota inválido '{f.get('status')}' ({', '.join(ROUTE_ST)}; remoção só por 'econ remove')")


@sim.effect("econ.route.status")
def _fx_route(w, f, key, ev):
    _need_on(w)
    if f["status"] not in ROUTE_ST:
        raise sim.SimPermanent(f"status de rota inválido '{f['status']}' ({', '.join(ROUTE_ST)})")
    E = load(w.dir)
    k, r = _entry(E, f["route"])
    if k != "routes":
        raise sim.SimPermanent(f"'{f['route']}' não é uma rota")
    if r.get("status") == FINAL and key not in E["_applied"]:
        raise sim.SimPermanent(f"rota '{r['id']}' foi removida — status final")
    if sim.once(E, key):
        r.setdefault("history", []).append({"at": w.now, "de": r.get("status", "ativa"), "para": f["status"],
                                            "reason": f.get("reason", ""), "cause": ev["id"]})
        r["status"] = f["status"]
        sim.save_json(path(w.dir), E)
    return {"rota": r["id"], "status": r["status"]}


def trade_check(w, f, R):
    """Valida tudo antes de qualquer alteração. → (qtd, total)."""
    for x in (f["buyer"], f["seller"]):
        if not w.exists(x):
            raise sim.SimError(f"entidade '{x}' não existe")
    if f["buyer"] == f["seller"]:
        raise sim.SimError("troca consigo mesmo")
    q, unit = sim._qty(f["qty"]), sim._qty(f["unit_price"])
    total = round(q * unit, 6)
    for who, res, need in ((f["seller"], f["res"], q), (f["buyer"], f["price_res"], total)):
        have = R["holdings"].get(who, {}).get(res, 0)
        if have < need and who not in R["allow_negative"]:
            raise sim.SimError(f"{who} tem {have:g} {res}; precisa {need:g} {res} — troca recusada, nada foi alterado")
    return q, total


@sim.effect("econ.trade")
def _fx_trade(w, f, key, ev):
    _need_on(w)
    R = sim.load_res(w.dir)
    q, total = trade_check(w, f, R) if key not in R.get("_applied", []) else (f["qty"], f["qty"] * f["unit_price"])
    if sim.once(R, key):  # tudo em memória e uma única gravação atômica: ou as duas transferências, ou nenhuma
        for src, dst, res, n in ((f["seller"], f["buyer"], f["res"], q), (f["buyer"], f["seller"], f["price_res"], total)):
            sim._take(R, src, res, n)
            h = R["holdings"].setdefault(dst, {})
            h[res] = h.get(res, 0) + n
        sim.save_json(sim.res_path(w.dir), R)
    return {"troca": f"{f['seller']}→{f['buyer']}", "res": f["res"], "qtd": q, "pago": total, "moeda": f["price_res"]}


# ---------------------------------------------------------------- validação
def problems(w, E):
    P, ids = [], []
    for loc, m in E["markets"].items():
        if not w.exists(loc):
            P.append(f"mercado em local inexistente '{loc}'")
        for g, d in m.get("goods", {}).items():
            if not isinstance(d.get("base_price"), (int, float)) or d["base_price"] <= 0:
                P.append(f"{loc}/{g}: base_price deve ser > 0")
            for k in ("demand", "supply"):
                if not isinstance(d.get(k, 0), (int, float)) or d.get(k, 0) < 0:
                    P.append(f"{loc}/{g}: {k} deve ser ≥ 0")
            if d.get("stock_holder") and not w.exists(d["stock_holder"]):
                P.append(f"{loc}/{g}: stock_holder '{d['stock_holder']}' não existe")
    for k in KINDS:
        for x in E[k]:
            pre = f"{k}/{x.get('id')}"
            ids.append(x.get("id"))
            if not SLUG.match(str(x.get("id", ""))):
                P.append(f"{pre}: id inválido")
            for e in ([x.get("from"), x.get("to")] if k == "routes" else [x.get("who")]):
                if not e or not w.exists(e):
                    P.append(f"{pre}: entidade '{e}' não existe")
            if not x.get("res"):
                P.append(f"{pre}: falta 'res'")
            if not isinstance(x.get("qty"), (int, float)) or isinstance(x.get("qty"), bool) or x["qty"] <= 0:
                P.append(f"{pre}: qty deve ser > 0")
            try:
                if x.get("when"):
                    wh = x["when"]
                    if not isinstance(wh, dict) or not wh.get("at"):
                        raise sim.SimError("'when' precisa de 'at' (HH:MM)")
                    sim.hhmm(wh["at"], w.cal)
                    sim.check_days(wh.get("days"), w.cal)
                    if wh.get("monthday") is not None and not (isinstance(wh["monthday"], int) and wh["monthday"] >= 1):
                        raise sim.SimError("'monthday' deve ser inteiro ≥ 1")
                elif sim.parse_duration(str(x.get("every", "")), w.cal) <= 0:
                    P.append(f"{pre}: 'every' deve ser > 0")
            except sim.SimError as e:
                P.append(f"{pre}: {e}")
            if payer_pc(k, x) and x.get("status") != FINAL and not (isinstance(x.get("authorized"), str) and x["authorized"].strip()):
                P.append(f"{pre}: debita {payer_pc(k, x)} — exige \"authorized\": \"<o que o jogador autorizou>\" (débito "
                         f"automático só com autorização explícita do protagonista)")
            if (x.get("bill") or x.get("partial") or x.get("on_arrears")) and k != "routes":
                P.append(f"{pre}: bill/partial/on_arrears só valem em rotas (pagador → credor)")
            P += [f"{pre}: {p}" for p in sim.check_effects(w, x.get("on_arrears", []), "on_arrears")]
            if k == "routes" and x.get("from") == x.get("to"):
                P.append(f"{pre}: origem = destino")
            if x.get("status", "ativa") not in ROUTE_ST + (FINAL,) or (k != "routes" and x.get("status", "ativa") == "interrompida"):
                P.append(f"{pre}: status inválido '{x.get('status')}'")
    if len(set(ids)) != len(ids):
        P.append("ids repetidos entre produtores/consumidores/rotas")
    return P


@sim.verifier
def _verify(w):
    """Com economy: on o dinheiro mecânico vive só em resources.json: a ficha não pode ter saldo concorrente."""
    if not enabled(w.dir):
        return []
    import world
    E = load(w.dir)
    P = [f"[economia] {p}" for p in problems(w, E)]
    refused = [e for e in w.events() if (e.get("gen_key") or "").startswith("econ:") and sim.is_permanent(e)]
    for k in KINDS:  # entrada antiga sem autorização: um diagnóstico por entrada, com o procedimento
        for x in E[k]:
            mine = [e["id"] for e in refused if e["gen_key"].startswith(f"econ:{x['id']}:") and "sem autorização" in str(e.get("result"))]
            if mine:
                P.append(f"[economia] {x['id']}: {len(mine)} ciclo(s) recusado(s) por falta de autorização ({mine[0]}"
                         + (f" … {mine[-1]}" if len(mine) > 1 else "") + f"); nada foi debitado nem virou atraso — "
                         + ("entrada já removida; " if x.get("status") == FINAL else "") + fix_hint(k, x))
    for eid, e in sorted(w.ctx.ent.items()):
        if eid.startswith("pc-") and re.search(r"\d", world.section(e["text"], "Dinheiro")):
            P.append(f"[economia] {eid}: '## Dinheiro' da ficha tem número; com economy: on o saldo é só "
                     f"'rpg.py res {eid}' (migre uma vez com 'econ open' e troque o texto por esse comando)")
    return P


# ---------------------------------------------------------------- CLI
def _w(a, clock=True):
    sim.load_modules()
    w = sim.World(camp_dir(a.campaign), clock)
    _need_on(w)
    return w


def _num(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        raise sim.SimError(f"quantidade inválida: '{s}' (use um número > 0)")
    if not v > 0 or v != v or v == float("inf"):
        raise sim.SimError(f"quantidade inválida: '{s}' (precisa ser > 0)")
    return int(v) if v.is_integer() else v


def visibility(a, *who):
    """Público só o que envolve um PJ (o jogador sabe); o resto é do mestre. --public/--hidden decidem explicitamente."""
    if getattr(a, "public", False):
        return False
    if getattr(a, "hidden", False):
        return True
    return not any(str(x).startswith("pc-") for x in who)


def cmd_status(a):
    d = camp_dir(a.campaign)
    if not enabled(d):
        print("Economia desligada nesta campanha (adicione 'economy: on' ao campaign.yaml para ligar).")
        return
    E = load(d)
    print("Economia ligada.")
    for loc, m in sorted(E["markets"].items()):
        for g in sorted(m.get("goods", {})):
            p, ratio, sup = price(d, loc, g)
            print(f"Mercado {loc}: {g} = {p:g} (×{ratio:g}; oferta {sup:g})")
    for k, title in (("producers", "Produtor"), ("consumers", "Consumidor")):
        for x in E[k]:
            print(f"{title} {x['id']}: {x['who']} {x['res']} ×{x['qty']:g} " + (str(x["when"]) if x.get("when") else f"a cada {x['every']}")
                  + (f" [{x['status']}]" if x.get("status") else ""))
    for r in E["routes"]:
        quando = r["when"] if r.get("when") else f"a cada {r['every']}"
        conta = f" conta; atraso {r.get('arrears', 0):g}" if r.get("bill") else ""
        print(f"Rota {r['id']}: {r['from']} → {r['to']} {r['res']} ×{r['qty']:g} {quando} [{r.get('status', 'ativa')}{conta}]")


def cmd_price(a):
    w = _w(a, False)
    p, ratio, sup = price(w.dir, a.loc, a.good)
    g = load(w.dir)["markets"][a.loc]["goods"][a.good]
    print(f"{a.good} em {a.loc}: {p:g} = base {g['base_price']:g} × {ratio:g} "
          f"(demanda {g.get('demand', 0):g} / oferta {sup:g}, limitado a {RATIO[0]}–{RATIO[1]})")


def cmd_trade(a):
    w = _w(a)
    gk = f"econ:trade:{a.op}"
    f = {"type": "econ.trade", "buyer": a.buyer, "seller": a.seller, "res": a.res, "qty": _num(a.qty),
         "price_res": a.price_res, "unit_price": _num(a.unit_price)}
    prev = next((e for e in w.events() if e.get("gen_key") == gk), None)
    if prev and prev["status"] == "processado" and prev.get("effects") == [f]:  # contrato da Fase 3: repetir é recusado
        raise sim.SimError(f"operação '{a.op}' já foi aplicada — nada foi feito de novo")
    if not prev:
        trade_check(w, f, sim.load_res(w.dir))  # recusa antes de criar o evento
    # apply_now: conflito com outros dados, retomada de troca falha (mesmo --op) e criação nova
    e, _ = sim.apply_now(w, {"type": "economia", "text": a.reason or f"{a.buyer} compra {f['qty']:g} {a.res} de {a.seller}",
                             "hidden": visibility(a, a.buyer, a.seller), "entities": [a.buyer, a.seller], "effects": [f]}, gk,
                         declared=sim._pc(a.buyer) or sim._pc(a.seller))  # troca do PJ = declarada pelo jogador
    r = e["result"]["efeitos"][0]
    print(f"{e['id']}: {a.buyer} recebeu {r['qtd']:g} {a.res} de {a.seller} e pagou {r['pago']:g} {a.price_res}")


def cmd_pay(a):
    """Transação avulsa (compra, pagamento, presente, quitação): origem, destino, valor, motivo e causa no evento."""
    w = _w(a)
    gk = f"econ:pay:{a.op}"
    q = sim._qty(_num(a.qty))
    effs = [{"type": "resource.transfer", "from": a.payer, "to": a.payee, "res": a.res, "qty": q}]
    if a.route:
        effs.append({"type": "econ.settle", "route": a.route, "qty": q})
    if gk not in {e.get("gen_key") for e in w.events()}:  # valida tudo antes de criar o evento
        for x in (a.payer, a.payee):
            if not w.exists(x):
                raise sim.SimError(f"entidade '{x}' não existe")
        R = sim.load_res(w.dir)
        have = R["holdings"].get(a.payer, {}).get(a.res, 0)
        if have < q and a.payer not in R["allow_negative"]:
            raise sim.SimError(f"{a.payer} tem {have:g} {a.res}; não pode pagar {q:g} — nada foi feito")
        if a.route:
            k, x = _entry(load(w.dir), a.route)
            if k != "routes" or not x.get("bill") or (x["from"], x["to"]) != (a.payer, a.payee) or x["res"] != a.res:
                raise sim.SimError(f"'{a.route}' não é uma conta de {a.payer} para {a.payee} em {a.res}")
            if x.get("status") == FINAL:
                raise sim.SimError(f"'{a.route}' foi removida")
            if q > x.get("arrears", 0) + 1e-9:
                raise sim.SimError(f"{a.route}: atraso é {x.get('arrears', 0):g}; não dá para quitar {q:g}")
        if a.cause and not w.event(a.cause):
            raise sim.SimError(f"causa '{a.cause}' não existe")
    e, done = sim.apply_now(w, {"type": "economia", "text": f"{a.payer} paga {q:g} {a.res} a {a.payee}: {a.reason}",
                                "entities": [a.payer, a.payee], "hidden": visibility(a, a.payer, a.payee), "cause": a.cause,
                                "effects": effs}, gk, declared=sim._pc(a.payer))  # pagamento do PJ = declarado pelo jogador
    r = e["effects"][0]  # o que foi REGISTRADO (numa repetição, não os argumentos novos)
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {r['from']} → {r['to']} {r['qty']:g} {r['res']}"
          + (f" (quita {a.route})" if a.route else "") + f". Saldo de {r['from']}: {sim.balance(w.dir, r['from'], r['res']):g}"
          + (" [MESTRE]" if e.get("hidden") else ""))


def cmd_open(a):
    """Saldo inicial EXPLÍCITO (ex.: o dinheiro que estava na ficha). Nunca roda sozinho; não sobrescreve saldo existente."""
    w = _w(a)
    gk = f"econ:open:{a.op}"
    q = sim._qty(_num(a.qty))
    if gk not in {e.get("gen_key") for e in w.events()}:
        if not w.exists(a.who):
            raise sim.SimError(f"entidade '{a.who}' não existe")
        if a.res in sim.load_res(w.dir)["holdings"].get(a.who, {}):
            raise sim.SimError(f"{a.who} já tem saldo de {a.res}; use transações (econ pay), não um novo saldo inicial")
    e, done = sim.apply_now(w, {"type": "economia", "text": f"Saldo inicial de {a.who}: {q:g} {a.res} ({a.reason})",
                                "entities": [a.who], "hidden": not a.public,  # saldo é informação do mestre por padrão
                                "effects": [{"type": "resource.produce", "who": a.who, "res": a.res,
                                             "qty": q, "source": f"saldo-inicial: {a.reason}"}]}, gk)
    r = e["effects"][0]
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {r['who']} começa com {r['qty']:g} {r['res']}"
          + (" [MESTRE]" if e.get("hidden") else ""))


def cmd_set(a):
    w = _w(a, False)
    v = sim._json_arg(a.json, "--json")
    if len(v) != 1 or not isinstance(v[0], dict):
        raise sim.SimError("--json deve ser um objeto")
    bad = set(v[0]) - {"markets", *KINDS}
    if bad:
        raise sim.SimError(f"campos desconhecidos: {', '.join(sorted(bad))}")
    E = load(w.dir)
    done = []
    for loc, m in (v[0].get("markets") or {}).items():  # mercados: mescla por local e por bem
        goods = E["markets"].setdefault(loc, {}).setdefault("goods", {})
        for g, d in (m.get("goods") or {}).items():
            goods[g] = {**goods.get(g, {}), **d}
            done.append(f"mercado {loc}/{g}")
    for k in KINDS:
        for x in v[0].get(k) or []:
            if not isinstance(x, dict) or not x.get("id"):
                raise sim.SimError(f"{k}: cada entrada precisa de 'id' — nada foi gravado")
            cur = next(((kk, y) for kk in KINDS for y in E[kk] if y["id"] == x["id"]), None)
            if cur and cur[0] != k:
                raise sim.SimError(f"id '{x['id']}' já é usado em {cur[0]} — nada foi gravado")
            if cur and cur[1].get("status") == FINAL:
                raise sim.SimError(f"'{x['id']}' foi removida; ids não são reaproveitados — nada foi gravado")
            touched = [f for f in MANAGED if f in x and (not cur or x[f] != cur[1].get(f))]
            if not cur and x.get("status", "ativa") not in ROUTE_ST:
                raise sim.SimError(f"'{x['id']}': entrada nova só nasce 'ativa' ou 'interrompida' — nada foi gravado")
            if touched and (cur or set(touched) - {"status"}):
                raise sim.SimError(f"'{x['id']}': {', '.join(touched)} é gerenciado pelo motor (status: econ.route.status ou "
                                   f"'econ remove'; atraso: 'econ pay --route'/--forgive) — nada foi gravado")
            if cur:
                cur[1].update({f: val for f, val in x.items() if f not in MANAGED})
                done.append(f"atualizou {x['id']}")
            else:
                E[k].append(dict(x))
                done.append(f"criou {x['id']}")
    # problema que já existia numa entrada NÃO tocada (ex.: rota antiga sem 'authorized') não impede corrigir outra;
    # qualquer problema da entrada alterada, ou novo, continua recusando tudo
    before = set(problems(w, load(w.dir)))
    mine = tuple(f"{k}/{x['id']}:" for k in KINDS for x in v[0].get(k) or [])
    P = [p for p in problems(w, E) if p not in before or p.startswith(mine)]
    if P:
        raise sim.SimError("nada foi gravado:\n  " + "\n  ".join(P))
    if not done:
        print("Economia sem mudanças (econ set só cria ou atualiza por id; para remover use 'econ remove').")
        return
    auth = {x["id"]: x["authorized"] for k in KINDS for x in v[0].get(k) or [] if "authorized" in x}
    if w.clock:  # auditoria da configuração (antes de gravar: uma queda deixa só o registro da intenção)
        w.record(f"Configuração econômica: {'; '.join(done)}", "economia", hidden=True,
                 result={"mudancas": done, **({"autorizacoes": auth} if auth else {})})  # o texto autorizado fica na história
    sim.save_json(path(w.dir), E)
    print(f"Economia atualizada: {'; '.join(done)}")


def cmd_remove(a):
    """Remoção explícita, validada e auditada de produtor/consumidor/rota. Nada é apagado."""
    import life
    w = _w(a)
    gk = f"econ:remove:{a.op}"
    E = load(w.dir)
    k, x = _entry(E, a.id)
    if gk not in {e.get("gen_key") for e in w.events()}:
        if x.get("status") == FINAL:
            raise sim.SimError(f"'{a.id}' já foi removida")
        users = [cid for cid, c in life.load(w.dir)["commitments"].items() if c.get("route") == a.id and c.get("status", "ativo") == "ativo"]
        if users:
            raise sim.SimError(f"'{a.id}' está em uso por {', '.join(users)} (ativo) — encerre antes com 'life end <cmp>'")
        if x.get("arrears") and not a.forgive:
            raise sim.SimError(f"'{a.id}' tem atraso de {x['arrears']:g} — quite com 'econ pay ... --route {a.id}' ou "
                               f"use --forgive (o valor perdoado fica registrado)")
        if a.cause and not w.event(a.cause):
            raise sim.SimError(f"causa '{a.cause}' não existe")
    who = [x["from"], x["to"]] if k == "routes" else [x["who"]]
    e, done = sim.apply_now(w, {"type": "economia", "text": f"Entrada econômica {a.id} removida: {a.reason}", "entities": who,
                                "hidden": visibility(a, *who), "cause": a.cause,
                                "effects": [{"type": "econ.remove", "entry": a.id, "reason": a.reason, "forgive": bool(a.forgive)}]}, gk)
    r = e["result"]["efeitos"][0]
    print(f"{e['id']}{' (já aplicado; nada repetido)' if done else ''}: {a.id} removida (registro e histórico preservados)"
          + (f"; atraso perdoado: {r['perdoado']:g}" if r.get("perdoado") else ""))


def cmd_ledger(a):
    import world
    w = _w(a, False)
    h = sim.load_res(w.dir)["holdings"].get(a.who, {})
    print(f"{a.who}: " + (", ".join(f"{k}={v:g}" for k, v in sorted(h.items())) or "sem saldos"))
    for e in w.events():
        types = [x.get("type", "") for x in e.get("effects", [])]
        if (e.get("type") == "economia" or any(t.startswith(("resource.", "econ.")) for t in types)) and world._touches(e, a.who):
            print("  " + world._line(e))


def register(sp):
    w = sim._wrap
    t = sp.add_parser("econ", help="economia opcional (economy: on): mercados, produção, rotas, trocas").add_subparsers(dest="ecmd", required=True)
    c = t.add_parser("status", help="mercados, produtores, consumidores e rotas"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_status))
    c = t.add_parser("price", help="preço de um bem num mercado")
    c.add_argument("loc"); c.add_argument("good"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_price))
    c = t.add_parser("trade", help="troca tudo-ou-nada: vendedor entrega o bem, comprador paga")
    c.add_argument("buyer"); c.add_argument("seller"); c.add_argument("res"); c.add_argument("qty")
    c.add_argument("--price-res", required=True); c.add_argument("--unit-price", required=True)
    c.add_argument("--op", required=True, help="id único da ação (evita repetir)"); c.add_argument("--reason", default="")
    g = c.add_mutually_exclusive_group(); g.add_argument("--hidden", action="store_true"); g.add_argument("--public", action="store_true")
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_trade))
    c = t.add_parser("pay", help="transação avulsa A → B (compra, pagamento, quitação de conta com --route)")
    c.add_argument("payer"); c.add_argument("payee"); c.add_argument("qty"); c.add_argument("--res", default="dinheiro")
    c.add_argument("--reason", required=True); c.add_argument("--op", required=True, help="id único da ação (evita repetir)")
    c.add_argument("--route", help="conta cujo atraso esta quantia quita"); c.add_argument("--cause")
    g = c.add_mutually_exclusive_group()
    g.add_argument("--hidden", action="store_true", help="só o mestre vê (padrão quando não envolve PJ)")
    g.add_argument("--public", action="store_true", help="os jogadores veem (padrão quando envolve PJ)")
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_pay))
    c = t.add_parser("open", help="saldo inicial explícito de uma entidade (ex.: migrar o dinheiro da ficha)")
    c.add_argument("who"); c.add_argument("qty"); c.add_argument("--res", default="dinheiro")
    c.add_argument("--reason", required=True); c.add_argument("--op", required=True)
    c.add_argument("--public", action="store_true", help="os jogadores veem o saldo inicial (padrão: só o mestre)")
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_open))
    c = t.add_parser("remove", help="remove (marca 'removida', com histórico) produtor/consumidor/rota — validado e auditado")
    c.add_argument("id"); c.add_argument("--reason", required=True); c.add_argument("--op", required=True)
    c.add_argument("--forgive", action="store_true", help="perdoa o atraso pendente (valor registrado no histórico)")
    c.add_argument("--cause"); g = c.add_mutually_exclusive_group()
    g.add_argument("--hidden", action="store_true"); g.add_argument("--public", action="store_true")
    c.add_argument("--campaign"); c.set_defaults(f=w(cmd_remove))
    c = t.add_parser("set", help="cria/atualiza por id mercados, produtores, consumidores e rotas (nunca apaga; valida antes)")
    c.add_argument("--json", required=True, help="JSON ou @arquivo.json"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_set))
    c = t.add_parser("ledger", help="saldos e eventos de recursos de uma entidade")
    c.add_argument("who"); c.add_argument("--campaign"); c.set_defaults(f=w(cmd_ledger))
