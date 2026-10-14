#!/usr/bin/env python3
"""Etapa 3.2.1, em raiz temporária: guarda de agência do protagonista (definição × execução, declarações próprias,
efeitos compostos e recuperação, missão e eventos do mestre, autorizações econômicas) e causalidade (N1: origem do
conhecimento nas decisões de NPC; N2: causa da expiração de missão). Mostra cada verificação."""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
os.environ.pop("RPG_SIM_MODULES", None)
import rpg, sim  # noqa: E402

env = {**os.environ, "RPG_ROOT": str(tmp)}
J = json.dumps
RESULTS = []
REFUSED = "não decide pelo protagonista"


def check(cid, ok, detail=""):
    RESULTS.append((cid, bool(ok), detail))


def sh(*args, camp="ag"):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *[str(x) for x in args], *(["--campaign", camp] if camp else [])],
                       env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def must(*args, camp="ag"):
    rc, out = sh(*args, camp=camp)
    assert rc == 0, f"preparação falhou: {args}\n{out}"
    return out


def ent(C, eid, known="", **meta):
    sub = {"pc": "characters", "npc": "npcs", "loc": "locations", "quest": "quests"}[eid.split("-")[0]]
    body = "\n".join(f"{k}: {v}" for k, v in {"id": eid, "name": eid.split("-", 1)[1].title(), **meta}.items())
    (C / sub / f"{eid}.md").write_text(f"---\n{body}\n---\n# {eid}\n\n## Conhecido pelos jogadores\n{known}\n")


def evs(C):
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def snap(C):
    h = hashlib.sha256()
    for p in sorted(C.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(C)).encode() + p.read_bytes())
    return h.hexdigest()


def loc(C, eid):
    return rpg.read_meta(C / ("characters" if eid.startswith("pc-") else "npcs") / f"{eid}.md").get("location")


def qst(C, q):
    return rpg.read_meta(C / "quests" / f"{q}.md").get("status")


def feels(C):
    return sim.feelings(sim.read_jsonl(C / "state" / "relations.jsonl"))


def refused(*args, camp="ag", needle=REFUSED):
    C = tmp / "campaigns" / camp
    s0 = snap(C)
    rc, out = sh(*args, camp=camp)
    return rc != 0 and needle in out and snap(C) == s0, out.strip()[-200:]


MOVE = {"type": "entity.set", "id": "pc-ana", "field": "location", "value": "loc-porao"}
FEEL = {"type": "relation.feel", "from": "pc-ana", "to": "npc-lia", "dim": "afeto", "delta": 5, "declared": True}
ACCEPT = {"type": "quest.status", "quest": "quest-trama", "to": "aceita", "reason": "x"}
TAKE = {"type": "resource.transfer", "from": "pc-ana", "to": "npc-lia", "res": "dinheiro", "qty": 10}
EAT = {"type": "resource.consume", "who": "pc-ana", "res": "dinheiro", "qty": 1}
TRADE = {"type": "econ.trade", "buyer": "pc-ana", "seller": "npc-loja", "res": "livro", "qty": 1, "price_res": "dinheiro", "unit_price": 5}


def camp(cid, start="2026/11/09 05:00"):
    must("new", cid, "--name", cid, "--system", "aventura-2d6", "--setting", "moderna", camp=None)
    C = tmp / "campaigns" / cid
    rpg.set_key(C / "campaign.yaml", "economy", "on")
    must("time", "init", "--calendar", tmp / "templates" / "calendars" / "terrestre.json", "--start", start, "--seed", "5", camp=cid)
    ent(C, "pc-ana", "Protagonista", location="loc-casa")
    for nid, where in (("npc-lia", "loc-casa"), ("npc-loja", "loc-loja"), ("npc-guarda", "loc-praca"), ("npc-capitao", "loc-quartel"),
                       ("npc-det", "loc-bar")):
        ent(C, nid, "Conhecido", status="vivo", location=where)
    for lid in ("loc-casa", "loc-loja", "loc-porao", "loc-praca", "loc-quartel", "loc-bar"):
        ent(C, lid, "Lugar")
    ent(C, "quest-trama", "Uma proposta", status="disponivel", type="principal")
    (C / "state" / "current.md").write_text("# Estado atual\n\n- **Local atual:** loc-casa\n- **Personagens presentes:** pc-ana\n")
    must("econ", "open", "pc-ana", "500", "--reason", "ficha", "--op", "o1", camp=cid)
    must("econ", "open", "npc-loja", "3", "--res", "livro", "--reason", "estoque", "--op", "o2", camp=cid)
    must("econ", "open", "npc-lia", "100", "--reason", "carteira", "--op", "o3", camp=cid)
    return C


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    C = camp("ag")
    sim.load_modules()

    # ============================================================ 1. definição: recusada antes de gravar, --op não consumido
    for name, eff in {"mover": MOVE, "sentir": FEEL, "aceitar": ACCEPT, "tirar": TAKE, "consumir": EAT, "negociar": TRADE,
                      "aninhado": {"type": "schedule", "in": "1h", "event": {"type": "evento", "text": "x", "effects": [MOVE]}}}.items():
        ok, out = refused("time", "schedule", "mestre", "--in", "1h", "--effects", J([eff]), "--op", f"d-{name}")
        check(f"1.time-schedule.{name}.recusado", ok, out)
    rc, out = sh("time", "schedule", "mestre", "--in", "1h", "--effects", J([{"type": "flag.set", "name": "ok", "value": True}]), "--op", "d-mover")
    check("1.time-schedule.op-nao-consumido", rc == 0 and "Agendado" in out, out[-120:])
    ok, out = refused("life", "add", "cmp-x", "--who", "pc-ana", "--kind", "lazer", "--text", "Cinema", "--once", "2026/11/09 20:00",
                      "--on-attend", J([TAKE]), "--op", "l1")
    rc, _ = sh("life", "add", "cmp-x", "--who", "pc-ana", "--kind", "lazer", "--text", "Cinema", "--once", "2026/11/09 20:00", "--op", "l1")
    check("1.life-consequencia.recusada-e-op-livre", ok and rc == 0, out)
    ok, out = refused("econ", "set", "--json", J({"routes": [{"id": "x", "from": "npc-lia", "to": "npc-loja", "res": "dinheiro", "qty": 1,
                                                             "bill": True, "when": {"at": "10:00", "monthday": 1}, "on_arrears": [MOVE]}]}))
    check("1.econ-on-arrears.recusado", ok, out)
    ok, out = refused("quest", "define", "quest-trama", "--json", J({"deadline": "+1d", "on_deadline": {"status": "aceita"}}),
                      needle="decisão do jogador")
    check("1.quest-prazo-aceita.recusado", ok, out)
    for k, v in {"rewards": [TAKE], "consequences": {"concluida": [FEEL]}, "on_deadline": {"status": "expirada", "effects": [MOVE]}}.items():
        ok, out = refused("quest", "define", "quest-trama", "--json", J({k: v}))
        check(f"1.quest-{k}.recusado", ok, out)
    ok, out = refused("npc", "propose", "npc-lia", "--action", J({"desc": "aceita por Ana", "effects": [ACCEPT]}), "--op", "pr1")
    check("1.npc-propose.recusado-e-op-livre", ok and not [e for e in evs(C) if "pr1" in (e.get("gen_key") or "")], out)
    # o que NÃO é escolha do PJ continua permitido
    allowed = [{"type": "entity.set", "id": "npc-lia", "field": "location", "value": "loc-loja"},
               {"type": "entity.set", "id": "pc-ana", "field": "home", "value": "loc-casa"},
               {"type": "relation.feel", "from": "npc-lia", "to": "pc-ana", "dim": "afeto", "delta": 3},
               {"type": "resource.transfer", "from": "npc-lia", "to": "pc-ana", "res": "dinheiro", "qty": 10},
               {"type": "knowledge.learn", "who": "pc-ana", "fact": "fact-x", "source": "carta"}]
    must("fact", "add", "fact-x", "Uma carta chegou")
    must("time", "schedule", "permitido", "--in", "1h", "--effects", J(allowed), "--op", "ok1")
    must("npc", "set", "npc-lia", "--tier", "2", "--goals", J([{"id": "g", "priority": 1, "actions": [
        {"id": "a", "desc": "dá presente", "duration": "1h", "preconditions": [{"type": "not", "of": {"type": "flag", "name": "presente"}}],
         "effects": [allowed[3], {"type": "flag.set", "name": "presente", "value": True}]}]}]))
    must("time", "advance", "2h", "--op", "a1", "--reason", "x")
    check("1.efeitos-que-nao-decidem-pelo-pj-aplicados", loc(C, "npc-lia") == "loc-loja" and feels(C).get(("npc-lia", "pc-ana")) == {"afeto": 3}
          and sim.balance(C, "pc-ana", "dinheiro") == 520 and sim.knows(C, "pc-ana", "fact-x"),  # 500 + agendado 10 + presente 10
          f"lia {loc(C, 'npc-lia')} saldo {sim.balance(C, 'pc-ana', 'dinheiro')}")

    # ============================================================ 2. declarações do jogador pelos comandos próprios continuam valendo
    must("world", "move", "pc-ana", "loc-loja", "--reason", "jogador: vai à loja", "--op", "mv1")
    must("world", "feel", "pc-ana", "npc-lia", "confianca", "+5", "--declared", "--reason", "jogador: confia", "--op", "fe1")
    must("econ", "pay", "pc-ana", "npc-lia", "20", "--reason", "jogador: paga o café", "--op", "pay1")
    must("econ", "trade", "pc-ana", "npc-loja", "livro", "1", "--price-res", "dinheiro", "--unit-price", "5", "--reason", "jogador compra", "--op", "tr1")
    must("quest", "set-status", "quest-trama", "aceita", "--reason", "jogador aceitou")
    raw = sim.read_jsonl(C / "state" / "events.jsonl")
    decl = {r.get("type"): r.get("declared") for r in raw if "op" not in r and r.get("declared")}
    check("2.declaracoes-aplicadas", loc(C, "pc-ana") == "loc-loja" and feels(C)[("pc-ana", "npc-lia")] == {"confianca": 5}
          and sim.balance(C, "pc-ana", "dinheiro") == 495 and sim.balance(C, "pc-ana", "livro") == 1 and qst(C, "quest-trama") == "aceita")
    check("2.marca-declared-persistida", {"movimento", "relacao.sentimento", "economia", "missao"} <= set(decl), J(decl))
    s0 = snap(C)
    rc, out = sh("world", "move", "pc-ana", "loc-loja", "--reason", "jogador: vai à loja", "--op", "mv1")
    check("2.repeticao-idempotente", rc == 0 and "já aplicado" in out and snap(C) == s0, out[-120:])

    # ============================================================ 3. execução: definições antigas e marcas forjadas
    w = sim.World(C)
    forged = w.schedule({"type": "evento", "text": "forjado", "due": w.now, "declared": True, "effects": [MOVE]})  # 'declared' no JSON
    legacy_move = w.schedule({"type": "movimento", "text": "move antigo", "due": w.now,
                              "effects": [{"type": "entity.set", "id": "pc-ana", "field": "location", "value": "loc-casa"}]},
                             gen_key="move:antes-da-marca")  # evento de 'world move' gravado antes da marca 'declared'
    must("time", "process")
    e_f, e_l = w.event(forged), sim.World(C).event(legacy_move)
    check("3.marca-forjada-ignorada", "declared" not in next(r for r in raw + sim.read_jsonl(C / "state" / "events.jsonl") if r.get("id") == forged)
          and sim.is_permanent(sim.World(C).event(forged)), J(sim.World(C).event(forged).get("result"), ensure_ascii=False)[:160])
    check("3.evento-antigo-de-world-move-compativel", e_l["status"] == "processado" and loc(C, "pc-ana") == "loc-casa")
    # declaração cobre só o efeito de 1º nível: consequência aninhada de uma transição declarada continua bloqueada
    ent(C, "quest-fuga", "Fuga", status="disponivel", type="principal")
    must("quest", "define", "quest-fuga", "--json", J({"consequences": {"concluida": [{"type": "flag.set", "name": "fugiu", "value": True}]}}))
    Q = sim.load_json(C / "state" / "sim" / "quests.json", {})
    Q["quests"]["quest-fuga"]["consequences"]["concluida"].append(MOVE)  # consequência gravada antes da guarda
    sim.save_json(C / "state" / "sim" / "quests.json", Q)
    rc, out = sh("quest", "set-status", "quest-fuga", "concluida", "--reason", "jogador concluiu", "--op", "qf1")
    ef = next(e for e in evs(C) if e.get("gen_key") == "quest-op:qf1")
    st, n, inc = sim.application(ef)
    check("3.consequencia-aninhada-bloqueada", loc(C, "pc-ana") == "loc-casa" and qst(C, "quest-fuga") == "concluida"
          and sim.load_json(sim.flags_path(C), {}).get("fugiu") is True and sim.is_permanent(ef) and REFUSED in J(ef["result"], ensure_ascii=False),
          out[-160:])
    check("3.composto-parcial-incerto", st == "parcial" and inc, f"{st} {n} {inc}")
    rc, out = sh("time", "retry", ef["id"])
    check("3.permanente-nao-retentado", rc != 0 and "permanente" in out, out[-120:])
    rc, out = sh("time", "cancel", ef["id"], "--accept-partial", "--reason", "mestre: consequência inválida descartada")
    ef2 = next(e for e in evs(C) if e["id"] == ef["id"])
    check("3.encerrado-com-auditoria", rc == 0 and ef2["status"] == "cancelado" and loc(C, "pc-ana") == "loc-casa"
          and sim.load_json(sim.flags_path(C), {}).get("fugiu") is True, out[-160:])
    # ação antiga de NPC com efeito permitido + proibido: o permitido fica, o proibido não; o relógio segue
    must("npc", "set", "npc-det", "--tier", "2", "--goals", J([{"id": "g", "priority": 9, "actions": [
        {"id": "a", "desc": "arrasta Ana", "duration": "1h", "preconditions": [{"type": "not", "of": {"type": "flag", "name": "det_agiu"}}],
         "effects": [{"type": "flag.set", "name": "det_agiu", "value": True}]}]}]))
    S = sim.load_json(C / "state" / "sim" / "npcs.json", {})
    S["npcs"]["npc-det"]["goals"][0]["actions"][0]["effects"].append(MOVE)
    sim.save_json(C / "state" / "sim" / "npcs.json", S)
    n_ev = len(evs(C))
    rc, out = sh("time", "advance", "1d", "--op", "a2", "--reason", "x")
    act = next(e for e in evs(C) if e.get("type") == "npc.acao" and e.get("npc") == "npc-det")
    check("3.acao-antiga-parcial-sem-travar", rc == 0 and "ATENÇÃO" in out and sim.application(act)[:2] == ("parcial", 1)
          and loc(C, "pc-ana") == "loc-casa" and sim.load_json(sim.flags_path(C), {}).get("det_agiu") is True
          and not sim.load_clock(C).get("pending_op"), out[-200:])
    s1 = snap(C)
    must("time", "process")
    check("3.reprocessar-nao-duplica", snap(C) == s1)
    must("time", "cancel", act["id"], "--accept-partial", "--reason", "mestre: ação antiga inválida")

    # ============================================================ 4. autorização econômica
    route = lambda rid, frm, qty, **kw: dict({"id": rid, "from": frm, "to": "npc-loja", "res": "dinheiro", "qty": qty,  # noqa: E731
                                               "when": {"at": "10:00", "monthday": 11}}, **kw)
    ok, out = refused("econ", "set", "--json", J({"consumers": [{"id": "c-bill", "who": "pc-ana", "res": "dinheiro", "qty": 1, "every": "1d",
                                                                 "bill": True, "authorized": "jogador"}]}), needle="só valem em rotas")
    check("4.consumidor-com-bill-recusado", ok, out)
    ok, out = refused("econ", "set", "--json", J({"consumers": [{"id": "c-sem", "who": "pc-ana", "res": "dinheiro", "qty": 1, "every": "1d"}]}),
                      needle="authorized")
    check("4.consumidor-sem-autorizacao-recusado", ok, out)
    ok, out = refused("econ", "set", "--json", J({"routes": [route("r-sem", "pc-ana", 5, bill=True)]}), needle="authorized")
    check("4.rota-sem-autorizacao-recusada", ok, out)
    ok, out = refused("econ", "set", "--json", J({"routes": [route("r-vazia", "pc-ana", 5, bill=True, authorized="  ")]}), needle="authorized")
    check("4.autorizacao-vazia-recusada", ok, out)
    ok, out = refused("life", "course", "cmp-curso", "--who", "pc-ana", "--text", "Curso", "--school", "loc-loja", "--at", "19:00",
                      "--days", "terça", "--fee", "50", "--fee-monthday", "11", "--op", "lc1", needle="authorized")
    check("4.mensalidade-sem-autorizacao-recusada", ok, out)
    must("life", "course", "cmp-curso", "--who", "pc-ana", "--text", "Curso", "--school", "loc-loja", "--at", "19:00", "--days", "terça",
         "--fee", "50", "--fee-monthday", "11", "--authorized", "jogador: mensalidade autorizada", "--op", "lc1")
    must("econ", "set", "--json", J({"routes": [route("r-aut", "pc-ana", 20, bill=True, authorized="jogador: conta autorizada"),
                                                route("r-cara", "pc-ana", 99999, bill=True, authorized="jogador: parcela autorizada"),
                                                route("r-npc", "npc-lia", 7, bill=True),  # NPC paga NPC: sem autorização do PJ
                                                route("r-livre", "pc-ana", 3, authorized="jogador: doação mensal")],
                                     "consumers": [{"id": "c-aut", "who": "pc-ana", "res": "dinheiro", "qty": 1, "every": "1d",
                                                    "authorized": "jogador: assinatura"}]}))
    E = sim.load_json(C / "state" / "sim" / "economy.json", {})
    E["routes"].append(route("r-antiga", "pc-ana", 11, bill=True))  # gravada antes da regra: sem autorização
    sim.save_json(C / "state" / "sim" / "economy.json", E)
    bal0, lia0 = sim.balance(C, "pc-ana", "dinheiro"), sim.balance(C, "npc-lia", "dinheiro")
    rc, out = sh("time", "advance", "1d", "--op", "a3", "--reason", "dia 11")  # 10/11 05:00 → 11/11 05:00
    rc, out = sh("time", "advance", "1d", "--op", "a4", "--reason", "dia 11 às 10h")
    E = sim.load_json(C / "state" / "sim" / "economy.json", {})
    rr = {r["id"]: r for r in E["routes"]}
    paid = {d["entry"]: d["paid"] for d in E.get("_bills", {}).values()}
    n_cons = len([e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:c-aut") and e["status"] == "processado"])
    check("4.autorizadas-debitadas", paid.get("r-aut") == 20 and paid.get("mens-curso") == 50 and paid.get("r-npc") == 7 and n_cons == 2
          and sim.balance(C, "pc-ana", "dinheiro") == bal0 - 20 - 50 - 3 - 2 and sim.balance(C, "npc-lia", "dinheiro") == lia0 - 7,
          J(paid) + f" saldo {sim.balance(C, 'pc-ana', 'dinheiro')} (antes {bal0})")
    check("4.sem-saldo-vira-atraso", paid.get("r-cara") == 0 and rr["r-cara"]["arrears"] == 99999
          and min(v for h in sim.load_res(C)["holdings"].values() for v in h.values()) >= 0)
    ant = [e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:r-antiga")]
    check("4.antiga-sem-autorizacao-nao-debita", "r-antiga" not in paid and not rr["r-antiga"].get("arrears") and ant
          and all(sim.is_permanent(e) for e in ant) and not sim.load_clock(C).get("pending_op"), J(rr["r-antiga"]))
    rc, out = sh("world", "verify")
    check("4.verify-lista-antiga", "r-antiga: debita pc-ana" in out, out[-200:])
    E["routes"] = [r for r in E["routes"] if r["id"] != "r-antiga"]  # limpa para os próximos passos (verificador)
    sim.save_json(C / "state" / "sim" / "economy.json", E)

    # ============================================================ 5. N1 — decisão de NPC alcança a origem do conhecimento
    D = camp("n1")
    must("fact", "add", "fact-furto", "Roubaram o cofre", "--about", "loc-praca", camp="n1")
    must("fact", "add", "fact-fuga", "O ladrão fugiu pelo norte", "--about", "loc-praca", camp="n1")
    must("fact", "add", "fact-carta", "Uma carta anônima", camp="n1")
    must("npc", "set", "npc-guarda", "--tier", "2", "--goals", J([{"id": "dever", "priority": 5, "actions": [
        {"id": "relatar", "desc": "relata ao capitão", "duration": "1h",
         "preconditions": [{"type": "knows", "who": "npc-guarda", "fact": "fact-furto"},
                           {"type": "any", "of": [{"type": "knows", "who": "npc-guarda", "fact": "fact-fuga"},
                                                  {"type": "knows", "who": "npc-guarda", "fact": "fact-carta"}]},
                           {"type": "not", "of": {"type": "flag", "name": "relatado"}}],
         "effects": [{"type": "knowledge.tell", "from": "npc-guarda", "to": "npc-capitao", "fact": "fact-furto"},
                     {"type": "flag.set", "name": "relatado", "value": True}]}]}]), camp="n1")
    must("npc", "set", "npc-capitao", "--tier", "2", "--goals", J([{"id": "dever", "priority": 5, "actions": [
        {"id": "cacar", "desc": "manda caçar o ladrão", "duration": "1h",
         "preconditions": [{"type": "knows", "who": "npc-capitao", "fact": "fact-furto"},
                           {"type": "not", "of": {"type": "flag", "name": "cacada"}}],
         "effects": [{"type": "flag.set", "name": "cacada", "value": True}]}]}]), camp="n1")
    must("time", "schedule", "Furto do cofre", "--in", "2h", "--loc", "loc-praca", "--hidden", "--op", "furto",
         "--effects", J({"type": "knowledge.witness", "fact": "fact-furto", "loc": "loc-praca"}), camp="n1")
    must("time", "schedule", "Pegadas para o norte", "--in", "3h", "--loc", "loc-praca", "--hidden", "--op", "fuga",
         "--effects", J({"type": "knowledge.witness", "fact": "fact-fuga", "loc": "loc-praca"}), camp="n1")
    must("fact", "learn", "npc-guarda", "fact-carta", "--source", "carta sem remetente", camp="n1")  # sem evento de origem
    for i in range(1, 4):
        must("time", "advance", "1d", "--op", f"n{i}", "--reason", "dia", camp="n1")
    E1 = evs(D)
    furto = next(e["id"] for e in E1 if e.get("gen_key") == "schedule:furto")
    fuga = next(e["id"] for e in E1 if e.get("gen_key") == "schedule:fuga")
    dec_g = next(e for e in E1 if e.get("type") == "npc.decisao" and e.get("npc") == "npc-guarda" and "relata" in e.get("text", ""))
    dec_c = next(e for e in E1 if e.get("type") == "npc.decisao" and e.get("npc") == "npc-capitao" and "caçar" in e.get("text", ""))
    act_c = next(e for e in E1 if e.get("type") == "npc.acao" and e.get("cause") == dec_c["id"])
    tell = next(e for e in E1 if e.get("type") == "npc.acao" and e.get("cause") == dec_g["id"])
    check("5.causa-imediata-preservada", sim.World(D).event(dec_g["cause"]).get("type") == "npc.gatilho")
    check("5.multiplas-origens-sem-duplicar", dec_g.get("sources") == [furto, fuga], J(dec_g.get("sources")))
    check("5.relato-e-origem-do-ouvinte", dec_c.get("sources") == [tell["id"]], J(dec_c.get("sources")))
    out = must("world", "chain", act_c["id"], camp="n1")
    check("5.chain-alcanca-o-evento-original", furto in out and fuga in out and tell["id"] in out and out.count(f"└ {dec_g['id']} ") == 1,
          out[-500:])
    out = must("world", "why", "npc-capitao", camp="n1")
    check("5.why-mostra-origem", "origem do conhecimento" in out and tell["id"] in out, out[-300:])
    out = must("world", "chain", furto, camp="n1")
    check("5.consequencias-pela-origem", dec_g["id"] in out.split("Consequências:")[1], out[-300:])
    raw = [r for r in sim.read_jsonl(D / "state" / "events.jsonl") if r.get("id") == dec_g["id"] and "op" not in r]
    check("5.persistido-no-arquivo", raw and raw[0].get("sources") == [furto, fuga])
    kn = sim.load_json(D / "state" / "sim" / "facts.json", {})["known"]
    check("5.sem-conhecimento-inventado", "fact-fuga" not in kn.get("npc-capitao", {}) and kn["npc-guarda"]["fact-carta"].get("event") is None
          and kn["npc-capitao"]["fact-furto"].get("event") == tell["id"])

    # ============================================================ 6. N2 — expiração com causa = última transição da missão
    ent(D, "quest-cofre", "", status="oculta", type="principal")
    ent(D, "quest-sem", "Sem transição", status="disponivel", type="principal")
    ent(D, "quest-reanc", "Reancorada", status="disponivel", type="principal")
    must("quest", "define", "quest-cofre", "--json", J({"unlock_conditions": [{"type": "flag", "name": "alarme"}], "deadline": "+2d",
                                                         "consequences": {"expirada": [{"type": "flag.set", "name": "cofre_perdido", "value": True}]}}),
         camp="n1")
    must("quest", "define", "quest-sem", "--json", J({"deadline": "+1d"}), camp="n1")
    must("quest", "define", "quest-reanc", "--json", J({"deadline": "+20h"}), camp="n1")
    must("time", "schedule", "Alarme do cofre", "--in", "1h", "--op", "alarme",
         "--effects", J({"type": "flag.set", "name": "alarme", "value": True}), camp="n1")
    must("time", "advance", "19h", "--op", "q1", "--reason", "x", camp="n1")  # prazo de quest-reanc ainda não agendado
    must("time", "advance", "1min", "--op", "q1b", "--reason", "x", camp="n1")
    # numa só janela: o prazo é agendado no INÍCIO do avanço (sem transição: sem causa) e uma transição acontece ANTES dele
    must("time", "schedule", "Missão descoberta", "--in", "10min", "--op", "desc",
         "--effects", J({"type": "quest.status", "quest": "quest-reanc", "to": "descoberta", "reason": "pista"}), camp="n1")
    must("time", "advance", "3d", "--op", "q2", "--reason", "x", camp="n1")
    E2 = evs(D)
    unl = next(e for e in E2 if e.get("gen_key") == "quest-unlock:quest-cofre")
    exp = [e for e in E2 if (e.get("gen_key") or "").startswith("quest-deadline:quest-cofre") and e["status"] == "processado"]
    alarme = next(e["id"] for e in E2 if e.get("gen_key") == "schedule:alarme")
    check("6.expiracao-causada-pela-transicao", len(exp) == 1 and exp[0].get("cause") == unl["id"], J([(e["id"], e.get("cause")) for e in exp]))
    out = must("world", "chain", exp[0]["id"], camp="n1") if exp else ""
    check("6.chain-completa", unl["id"] in out and alarme in out, out[-400:])
    sem = [e for e in E2 if (e.get("gen_key") or "").startswith("quest-deadline:quest-sem")]
    check("6.sem-transicao-sem-causa-inventada", len(sem) == 1 and sem[0].get("cause") is None and "sem transição anterior" in sem[0]["text"]
          and qst(D, "quest-sem") == "expirada", J([(e.get("cause"), e["text"]) for e in sem]))
    qa = next(e["id"] for e in E2 if e.get("gen_key") == "schedule:desc")
    re_ = [e for e in E2 if (e.get("gen_key") or "").startswith("quest-deadline:quest-reanc")]
    done = [e for e in re_ if e["status"] == "processado"]
    canc = [e for e in re_ if e["status"] == "cancelado"]
    check("6.reancorada-numa-transicao-posterior", len(done) == 1 and done[0].get("cause") == qa and qst(D, "quest-reanc") == "expirada"
          and len(canc) == 1 and canc[0].get("cause") is None and "reancorado" in J(canc[0]["result"], ensure_ascii=False),
          J([(e["id"], e["status"], e.get("cause")) for e in re_]))
    flags = sim.load_json(sim.flags_path(D), {})
    Q = sim.load_json(D / "state" / "sim" / "quests.json", {})["quests"]
    s2 = snap(D)
    must("time", "process", camp="n1")
    must("time", "advance", "2d", "--op", "q3", "--reason", "x", camp="n1")
    Q2 = sim.load_json(D / "state" / "sim" / "quests.json", {})["quests"]
    check("6.expira-uma-vez", flags.get("cofre_perdido") is True and all([h["para"] for h in Q2[q]["history"]].count("expirada") == 1
                                                                        for q in ("quest-cofre", "quest-sem", "quest-reanc")))
    check("6.repeticao-sem-efeito-duplicado", len([e for e in evs(D) if (e.get("gen_key") or "").startswith("quest-deadline:")
                                                   and e["status"] == "processado"]) == 3)
    out = must("world", "verify", camp="n1")
    check("6.verify-limpo", "OK" in out, out[-300:])
finally:
    shutil.rmtree(tmp)
    if sys.exc_info()[0]:
        for c, ok, d in RESULTS:
            print(f"{'OK  ' if ok else 'FALHA'} {c}" + (f"  — {d}" if d and not ok else ""))
        print("INTERROMPIDO: uma etapa de preparação falhou (veja o erro abaixo)")

fails = [c for c, ok, _ in RESULTS if not ok]
for c, ok, d in RESULTS:
    print(f"{'OK  ' if ok else 'FALHA'} {c}" + (f"  — {d}" if d and not ok else ""))
print(f"\n{len(RESULTS) - len(fails)} ok, {len(fails)} falha(s)")
if fails:
    sys.exit(1)
print("OK: agência do protagonista e causalidade (Etapa 3.2.1) passaram")
