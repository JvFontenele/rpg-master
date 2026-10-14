#!/usr/bin/env python3
"""Etapa 3.2.2, em raiz temporária: auditoria final de agência (A), efeitos compostos e recuperação parcial (B),
causalidade de facções e fatos (C) e entradas econômicas antigas sem autorização (D). Mostra cada verificação."""
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


def ev_gk(C, gk):
    return next(e for e in evs(C) if e.get("gen_key") == gk)


def snap(C):
    h = hashlib.sha256()
    for p in sorted(C.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(C)).encode() + p.read_bytes())
    return h.hexdigest()


def meta(C, eid):
    return rpg.read_meta(C / ("characters" if eid.startswith("pc-") else "npcs") / f"{eid}.md")


def flags(C):
    return sim.load_json(sim.flags_path(C), {})


def econ(C):
    return sim.load_json(C / "state" / "sim" / "economy.json", {})


def life(C):
    return sim.load_json(C / "state" / "sim" / "life.json", {})


def rels(C, op="add"):
    return [r for r in sim.read_jsonl(C / "state" / "relations.jsonl") if r.get("op") == op]


def refused(*args, camp="ag", needle=REFUSED):
    C = tmp / "campaigns" / camp
    s0 = snap(C)
    rc, out = sh(*args, camp=camp)
    return rc != 0 and needle in out and snap(C) == s0, out.strip()[-220:]


def camp(cid, start="2026/11/09 05:00"):
    must("new", cid, "--name", cid, "--system", "aventura-2d6", "--setting", "moderna", camp=None)
    C = tmp / "campaigns" / cid
    rpg.set_key(C / "campaign.yaml", "economy", "on")
    (C / "world.md").write_text((C / "world.md").read_text() + "\n- fac-guarda: a guarda da cidade\n")
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


def legacy(C, name, eff, **kw):
    """Definição gravada ANTES da guarda (não passou pela validação): agendada direto no motor, para agora."""
    w = sim.World(C)
    return w.schedule({"type": "evento", "text": f"legado {name}", "due": w.now, "hidden": True,
                       "effects": [{"type": "flag.set", "name": f"leg-{name}", "value": True}, eff,
                                   {"type": "flag.set", "name": f"leg2-{name}", "value": True}], **kw})


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    sim.load_modules()

    # ================================================================== A. agência: matriz de efeitos
    C = camp("ag")
    must("fact", "add", "fact-x", "Segredo X")
    must("fact", "add", "fact-y", "Segredo Y")
    must("fact", "learn", "npc-lia", "fact-x", "--source", "conversa antiga")
    must("fact", "learn", "pc-ana", "fact-y", "--source", "viu com os próprios olhos")
    must("life", "add", "cmp-aula", "--who", "pc-ana", "--kind", "estudo", "--text", "Aula", "--once", "+30min", "--op", "la")
    must("life", "add", "cmp-npc", "--who", "npc-lia", "--kind", "social", "--text", "Encontro", "--once", "+20min", "--op", "ln")
    must("life", "course", "cmp-curso", "--who", "pc-ana", "--text", "Curso", "--school", "loc-loja", "--at", "19:00", "--days", "terça",
         "--fee", "50", "--fee-monthday", "11", "--authorized", "jogador: mensalidade autorizada", "--op", "lc")
    must("econ", "set", "--json", J({"routes": [
        {"id": "r-aut", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro", "qty": 20, "bill": True,
         "when": {"at": "10:00", "monthday": 11}, "authorized": "jogador: aluguel autorizado"},
        {"id": "r-npc", "from": "npc-lia", "to": "npc-loja", "res": "dinheiro", "qty": 1, "when": {"at": "10:00", "monthday": 28}}]}))
    occ = life(C)["commitments"]["cmp-aula"]["once"]
    FAC = {"type": "entity.set", "id": "pc-ana", "field": "faction", "value": "fac-guarda"}
    REG = {"type": "entity.set", "id": "pc-ana", "field": "region", "value": "loc-praca"}
    REL_B = {"type": "relation.add", "a": "npc-lia", "b": "pc-ana", "rel": "noiva_de"}
    REL_A = {"type": "relation.add", "a": "pc-ana", "b": "npc-lia", "rel": "aliado_de"}
    TELL = {"type": "knowledge.tell", "from": "pc-ana", "to": "npc-lia", "fact": "fact-y"}
    ATT = {"type": "life.attend", "id": "cmp-aula", "at": occ, "status": "faltou"}
    DEF = {"type": "life.define", "id": "cmp-forjado", "commitment": {"who": "pc-ana", "kind": "estudo", "text": "Curso forjado",
                                                                      "when": {"at": "19:00"}},
           "route": {"id": "r-forjada", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro", "qty": 99, "bill": True, "status": "ativa",
                     "when": {"at": "10:00", "monthday": 10}, "authorized": "forjado nos dados"}}
    RUN = {"type": "econ.run", "entry": "r-aut"}
    REACT = {"type": "econ.route.status", "route": "r-aut", "status": "ativa", "reason": "x"}
    LST = {"type": "life.status", "id": "cmp-curso", "status": "ativo", "reason": "x"}
    PROT = {"faccao": FAC, "regiao": REG, "vinculo-b": REL_B, "vinculo-a": REL_A, "relato-do-pj": TELL, "presenca": ATT,
            "conta-forjada": DEF, "ciclo-extra": RUN, "reativar-rota": REACT, "reativar-compromisso": LST}

    # A1 — definição: recusada antes de gravar, em todos os pontos de entrada; --op não consumido
    for name, eff in PROT.items():
        ok, out = refused("time", "schedule", "x", "--in", "2h", "--effects", J([eff]), "--op", f"d-{name}")
        check(f"A1.time-schedule.{name}.recusado", ok, out)
        ok, out = refused("time", "schedule", "x", "--in", "2h", "--op", f"n-{name}",
                          "--effects", J([{"type": "schedule", "in": "1h", "event": {"type": "evento", "text": "x", "effects": [eff]}}]))
        check(f"A1.aninhado-em-schedule.{name}.recusado", ok, out)
    rc, out = sh("time", "schedule", "x", "--in", "2h", "--effects", J([{"type": "flag.set", "name": "ok", "value": 1}]), "--op", "d-faccao")
    check("A1.op-nao-consumido", rc == 0 and "Agendado" in out, out[-120:])
    ok, out = refused("npc", "set", "npc-lia", "--tier", "2", "--goals", J([{"id": "g", "priority": 1, "actions": [
        {"id": "a", "desc": "noivado", "effects": [REL_B]}]}]), needle="nada foi gravado")
    check("A1.npc-set.vinculo.recusado", ok and REFUSED in out, out)
    ok, out = refused("npc", "propose", "npc-lia", "--action", J({"desc": "Ana conta", "effects": [TELL]}), "--op", "p1")
    check("A1.npc-propose.relato-do-pj.recusado", ok, out)
    ok, out = refused("faction", "set", "fac-guarda", "--json", J({"plans": [{"id": "p", "steps": [{"id": "s", "effects": [FAC]}]}]}))
    check("A1.faction-set.faccao.recusado", ok, out)
    ok, out = refused("quest", "define", "quest-trama", "--json", J({"consequences": {"falhou": [ATT]}}))
    check("A1.quest-define.presenca.recusado", ok, out)
    ok, out = refused("life", "add", "cmp-y", "--who", "npc-lia", "--kind", "social", "--text", "x", "--once", "+3h",
                      "--on-miss", J([DEF]), "--op", "ly")
    check("A1.life-on-miss.conta-forjada.recusado", ok, out)
    ok, out = refused("econ", "set", "--json", J({"routes": [{"id": "r-x", "from": "npc-lia", "to": "npc-loja", "res": "dinheiro", "qty": 1,
                                                             "bill": True, "when": {"at": "10:00", "monthday": 1}, "on_arrears": [RUN]}]}))
    check("A1.econ-on-arrears.ciclo-extra.recusado", ok, out)

    # A2 — o que o mundo impõe (não é escolha do PJ) continua permitido
    allowed = [{"type": "entity.set", "id": "pc-ana", "field": "status", "value": "ferida"},
               {"type": "entity.set", "id": "pc-ana", "field": "home", "value": "loc-casa"},
               {"type": "relation.add", "a": "npc-lia", "b": "pc-ana", "rel": "conhece"},
               {"type": "relation.add", "a": "pc-ana", "b": "npc-guarda", "rel": " Conhece "},
               {"type": "relation.add", "a": "npc-lia", "b": "npc-guarda", "rel": "noiva_de"},
               {"type": "knowledge.tell", "from": "npc-lia", "to": "pc-ana", "fact": "fact-x"},
               {"type": "faction.reputation", "faction": "fac-guarda", "who": "pc-ana", "delta": -10},
               {"type": "resource.produce", "who": "pc-ana", "res": "dinheiro", "qty": 5, "source": "achado na rua"},
               {"type": "resource.transfer", "from": "npc-lia", "to": "pc-ana", "res": "dinheiro", "qty": 7},
               {"type": "econ.route.status", "route": "r-aut", "status": "interrompida", "reason": "banco em greve"},
               {"type": "life.define", "id": "cmp-convite", "commitment": {"who": "pc-ana", "kind": "social", "text": "Convite",
                                                                            "once": occ + 600}},
               {"type": "life.attend", "id": "cmp-npc", "at": life(C)["commitments"]["cmp-npc"]["once"], "status": "presente"},
               {"type": "econ.run", "entry": "r-npc"},
               {"type": "relation.feel", "from": "npc-lia", "to": "pc-ana", "dim": "afeto", "delta": 3, "declared": True}]
    must("time", "schedule", "mundo", "--in", "1h", "--effects", J(allowed), "--op", "ok1")
    must("time", "advance", "2h", "--op", "a1", "--reason", "x")
    e_ok = ev_gk(C, "schedule:ok1")
    rr = {r["id"]: r for r in econ(C)["routes"]}
    adds = {(r["a"], r["b"], r["type"]) for r in rels(C)}
    feel = [r for r in rels(C, "feel") if r["from"] == "npc-lia"]
    check("A2.consequencias-do-mundo-aplicadas", e_ok["status"] == "processado" and meta(C, "pc-ana")["status"] == "ferida"
          and {("npc-lia", "pc-ana", "conhece"), ("pc-ana", "npc-guarda", " Conhece "), ("npc-lia", "npc-guarda", "noiva_de")} <= adds
          and sim.knows(C, "pc-ana", "fact-x") and sim.balance(C, "pc-ana", "dinheiro") == 512
          and sim.balance(C, "npc-lia", "dinheiro") == 92 and rr["r-aut"]["status"] == "interrompida"
          and "cmp-convite" in life(C)["commitments"] and any(k.startswith("cmp-npc@") for k in life(C)["attendance"]),
          J(e_ok.get("result"), ensure_ascii=False)[:300])
    check("A2.marca-declared-nao-vem-do-dado", feel and feel[-1]["declared"] is False, J(feel[-1:]))
    rid = next(r["id"] for r in rels(C) if (r["a"], r["b"], r["type"]) == ("npc-lia", "pc-ana", "conhece"))
    must("time", "schedule", "fim", "--effects", J([{"type": "relation.end", "id": rid, "note": "Lia se mudou"}]), "--op", "ok2")
    must("time", "process")
    check("A2.relation-end-de-vinculo-do-pj-permitido", any(r["id"] == rid for r in rels(C, "end")))

    # A3 — execução: definições antigas (sem validação) viram falha permanente ANTES de gravar; o relógio segue
    must("life", "end", "cmp-curso", "--reason", "jogador trancou o curso", "--op", "le1")
    bal0, life0 = sim.balance(C, "pc-ana", "dinheiro"), life(C)
    ids = {name: legacy(C, name, eff) for name, eff in PROT.items()}
    rc, out = sh("time", "advance", "1min", "--op", "a2", "--reason", "x")
    E = {e["id"]: e for e in evs(C)}
    for name, eid in ids.items():
        e = E[eid]
        check(f"A3.legado.{name}.bloqueado-sem-aplicar", sim.is_permanent(e) and sim.application(e)[:2] == ("parcial", 1)
              and REFUSED in J(e["result"], ensure_ascii=False) and flags(C).get(f"leg-{name}") and not flags(C).get(f"leg2-{name}"),
              J(e.get("result"), ensure_ascii=False)[:200])
    rr = {r["id"]: r for r in econ(C)["routes"]}
    check("A3.estado-do-pj-intacto", rc == 0 and "ATENÇÃO" in out and not sim.load_clock(C).get("pending_op")
          and "faction" not in meta(C, "pc-ana") and "region" not in meta(C, "pc-ana")
          and not {("npc-lia", "pc-ana", "noiva_de"), ("pc-ana", "npc-lia", "aliado_de")} & {(r["a"], r["b"], r["type"]) for r in rels(C)}
          and not sim.knows(C, "npc-lia", "fact-y") and not any(k.startswith("cmp-aula@") for k in life(C)["attendance"])
          and "r-forjada" not in rr and "cmp-forjado" not in life(C)["commitments"] and sim.balance(C, "pc-ana", "dinheiro") == bal0
          and rr["r-aut"]["status"] == "interrompida" and rr["mens-curso"]["status"] == "interrompida"
          and life(C)["commitments"]["cmp-curso"]["status"] == "encerrado", out[-300:])
    must("npc", "set", "npc-det", "--tier", "2", "--goals", J([{"id": "g", "priority": 9, "actions": [
        {"id": "a", "desc": "pede Ana em noivado", "preconditions": [{"type": "not", "of": {"type": "flag", "name": "det"}}],
         "effects": [{"type": "flag.set", "name": "det", "value": True}]}]}]))
    S = sim.load_json(C / "state" / "sim" / "npcs.json", {})
    S["npcs"]["npc-det"]["goals"][0]["actions"][0]["effects"].append({"type": "relation.add", "a": "npc-det", "b": "pc-ana", "rel": "noivo_de"})
    sim.save_json(C / "state" / "sim" / "npcs.json", S)
    out = must("world", "verify")
    check("A3.verify-aponta-definicao-antiga-de-npc", "npc-det objetivos" in out and "noivo_de" in out, out[-300:])
    rc, out = sh("time", "advance", "1d", "--op", "a3", "--reason", "x")
    act = next(e for e in evs(C) if e.get("type") == "npc.acao" and e.get("npc") == "npc-det")
    check("A3.acao-antiga-de-npc-bloqueada", rc == 0 and sim.application(act)[:2] == ("parcial", 1)
          and not any(r["type"] == "noivo_de" for r in rels(C)), out[-200:])

    # A4 — marcas forjadas e aninhamento: nada herda autorização
    w = sim.World(C)
    fid = w.schedule({"type": "evento", "text": "forjado", "due": w.now, "declared": True, "effects": [TELL]})
    must("time", "process")
    raw = next(r for r in sim.read_jsonl(C / "state" / "events.jsonl") if r.get("id") == fid)
    check("A4.declared-do-json-ignorado", "declared" not in raw and sim.is_permanent(sim.World(C).event(fid)) and not sim.knows(C, "npc-lia", "fact-y"))
    L = life(C)  # consequência gravada antes da guarda, no compromisso do PJ
    L["commitments"]["cmp-aula"]["on_attend"] = [{"type": "flag.set", "name": "att-ok", "value": True}, TELL]
    sim.save_json(C / "state" / "sim" / "life.json", L)
    rc, out = sh("life", "attend", "cmp-aula", "presente", "--op", "at1")
    ea = ev_gk(C, "life:attend:at1")
    check("A4.presenca-declarada-registrada", ea.get("declared") is True and life(C)["attendance"][f"cmp-aula@{occ}"]["status"] == "presente"
          and flags(C).get("att-ok") is True, J(ea.get("result"), ensure_ascii=False)[:200])
    check("A4.consequencia-de-1o-nivel-nao-herda-a-declaracao", rc != 0 and sim.is_permanent(ea) and sim.application(ea)[:2] == ("parcial", 2)
          and not sim.knows(C, "npc-lia", "fact-y"), out[-200:])
    must("quest", "define", "quest-trama", "--json", J({"consequences": {"concluida": [{"type": "flag.set", "name": "qt-ok", "value": True}]}}))
    Q = sim.load_json(C / "state" / "sim" / "quests.json", {})
    Q["quests"]["quest-trama"]["consequences"]["concluida"].append(REL_A)
    sim.save_json(C / "state" / "sim" / "quests.json", Q)
    rc, out = sh("quest", "set-status", "quest-trama", "concluida", "--reason", "jogador concluiu", "--op", "qt1")
    eq = ev_gk(C, "quest-op:qt1")
    check("A4.aninhado-em-transicao-declarada-bloqueado", rc != 0 and sim.is_permanent(eq) and flags(C).get("qt-ok") is True
          and (eq.get("result") or {}).get("falhou_em") == f"{eq['id']}#0/1"
          and not any(r["type"] == "aliado_de" for r in rels(C)), J(eq.get("result"), ensure_ascii=False)[:200])

    # A5 — comandos de declaração continuam valendo (e gravam o que os fundamentou)
    must("world", "rel-add", "pc-ana", "npc-lia", "noiva_de", "--note", "jogador: Ana aceitou o pedido")
    must("world", "event-add", "Ana conta o segredo a Lia (declarado pelo jogador)", "--entities", "pc-ana,npc-lia", "--op", "cena")
    cena = ev_gk(C, "event:cena")["id"]
    must("fact", "learn", "npc-lia", "fact-y", "--status", "acredita", "--source", cena)
    check("A5.vinculo-e-relato-pelos-comandos", any(r["type"] == "noiva_de" and r["a"] == "pc-ana" for r in rels(C))
          and sim.knowledge(C, "npc-lia")["fact-y"].get("event") == cena)
    rc, out = sh("life", "end", "cmp-curso", "--reactivate", "--reason", "jogador voltou ao curso", "--op", "le2")
    er = ev_gk(C, "life:status:le2")
    ins = must("time", "inspect", er["id"])
    check("A5.reativacao-declarada-retoma-a-conta", rc == 0 and er.get("declared") is True
          and {r["id"]: r for r in econ(C)["routes"]}["mens-curso"]["status"] == "ativa"
          and "autorização: declaração do jogador ('life end')" in ins, ins[-300:])
    check("A5.curso-autorizado-marcado-declarado", ev_gk(C, "life:define:lc").get("declared") is True)
    bal1 = sim.balance(C, "pc-ana", "dinheiro")
    must("time", "advance", "1d12h", "--op", "a4", "--reason", "até 11/11 10h")  # 10/11 05:01 → 11/11 17:01
    paid = {d["entry"]: d["paid"] for d in econ(C).get("_bills", {}).values()}
    check("A5.mensalidade-autorizada-debitada-uma-vez", paid.get("mens-curso") == 50 and "r-aut" not in paid
          and sim.balance(C, "pc-ana", "dinheiro") == bal1 - 50, J(paid))
    ec = next(e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:mens-curso:"))
    check("A5.autorizacao-registrada-no-debito", "jogador: mensalidade autorizada" in J(ec.get("result"), ensure_ascii=False))

    # A6 — allow_negative: débito AUTOMÁTICO de PJ não cria dívida; NPC e pagamento declarado continuam podendo
    N = camp("neg")
    R = sim.load_res(N)
    R["allow_negative"] = ["pc-ana", "npc-lia"]
    R["holdings"]["pc-ana"]["dinheiro"] = 10
    R["holdings"]["npc-lia"]["dinheiro"] = 10
    sim.save_json(sim.res_path(N), R)
    must("econ", "set", "--json", J({
        "routes": [{"id": "r-pc", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro", "qty": 50, "bill": True,
                    "when": {"at": "10:00", "monthday": 10}, "authorized": "jogador: aluguel"},
                   {"id": "r-npc", "from": "npc-lia", "to": "npc-loja", "res": "dinheiro", "qty": 50, "bill": True,
                    "when": {"at": "10:00", "monthday": 10}}],
        "consumers": [{"id": "c-pc", "who": "pc-ana", "res": "dinheiro", "qty": 30, "every": "1d", "authorized": "jogador: assinatura"}]}), camp="neg")
    must("time", "advance", "1d6h", "--op", "n1", "--reason", "x", camp="neg")
    E = econ(N)
    paid = {d["entry"]: d["paid"] for d in E.get("_bills", {}).values()}
    rr = {r["id"]: r for r in E["routes"]}
    esc = [e for e in evs(N) if (e.get("gen_key") or "").startswith("escassez:")]
    check("A6.conta-do-pj-vira-atraso-nao-divida", paid.get("r-pc") == 0 and rr["r-pc"]["arrears"] == 50 and esc
          and sim.balance(N, "pc-ana", "dinheiro") == 10, J(paid) + f" saldo {sim.balance(N, 'pc-ana', 'dinheiro')}")
    check("A6.npc-com-allow-negative-inalterado", paid.get("r-npc") == 50 and sim.balance(N, "npc-lia", "dinheiro") == -40)
    must("econ", "pay", "pc-ana", "npc-loja", "25", "--reason", "jogador: paga fiado", "--op", "fiado", camp="neg")
    check("A6.pagamento-declarado-pode-usar-allow-negative", sim.balance(N, "pc-ana", "dinheiro") == -15)

    # ================================================================== B. compostos: falha injetada e retomada
    B = camp("comp")
    must("fact", "add", "fact-z", "A rota do contrabando", camp="comp")
    # B1 — consequência de missão: válida, válida, PROIBIDA (definição antiga), não iniciada → permanente
    ent(B, "quest-b1", "", status="em_andamento", type="principal")
    must("quest", "define", "quest-b1", "--json", J({"consequences": {"concluida": [
        {"type": "flag.set", "name": "b1a", "value": True},
        {"type": "resource.transfer", "from": "npc-lia", "to": "pc-ana", "res": "dinheiro", "qty": 5},
        {"type": "flag.set", "name": "b1b", "value": True}]}}), camp="comp")
    Q = sim.load_json(B / "state" / "sim" / "quests.json", {})
    Q["quests"]["quest-b1"]["consequences"]["concluida"].insert(2, {"type": "entity.set", "id": "pc-ana", "field": "location", "value": "loc-porao"})
    sim.save_json(B / "state" / "sim" / "quests.json", Q)
    rc, out = sh("quest", "set-status", "quest-b1", "concluida", "--reason", "jogador concluiu", "--op", "b1", camp="comp")
    e1 = ev_gk(B, "quest-op:b1")
    r1 = e1.get("result") or {}
    check("B1.estado-depois-da-falha", rc != 0 and flags(B).get("b1a") is True and "b1b" not in flags(B)
          and sim.balance(B, "pc-ana", "dinheiro") == 505 and meta(B, "pc-ana")["location"] == "loc-casa"
          and rpg.read_meta(B / "quests" / "quest-b1.md")["status"] == "concluida", out[-200:])
    check("B1.persistido-concluidos-falho-incerto", sim.is_permanent(e1) and r1.get("incerto") is True
          and r1.get("falhou_em") == f"{e1['id']}#0/2" and list(sim.subeffects(e1)) == [f"{e1['id']}#0/0", f"{e1['id']}#0/1"]
          and sim.application(e1) == ("parcial", 0, True), J(r1, ensure_ascii=False)[:300])
    ins = must("time", "inspect", e1["id"], camp="comp")
    check("B1.inspect-reconstroi", f"sub {e1['id']}#0/0 flag.set: concluído" in ins and f"sub {e1['id']}#0/1 resource.transfer: concluído" in ins
          and f"sub {e1['id']}#0/2: FALHOU" in ins and "#0 quest.status: INCERTO" in ins and "declarado pelo jogador" in ins, ins[-500:])
    v = must("world", "verify", camp="comp")
    check("B1.verify-descreve-o-parcial", f"falhou no sub-efeito {e1['id']}#0/2" in v and "sub-efeitos concluídos" in v, v[-400:])
    rc, out = sh("time", "retry", e1["id"], camp="comp")
    s0 = snap(B)
    must("time", "process", camp="comp")
    must("time", "process", camp="comp")
    check("B1.permanente-nao-retentado-nem-duplicado", rc != 0 and "permanente" in out and snap(B) == s0)
    rc, out = sh("time", "cancel", e1["id"], "--reason", "x", camp="comp")
    check("B1.cancelamento-comum-recusado", rc != 0 and "PARCIALMENTE" in out and snap(B) == s0, out[-160:])
    must("time", "cancel", e1["id"], "--accept-partial", "--reason", "mestre: consequência antiga descartada", camp="comp")
    rec = ev_gk(B, "quest-op:b1")["result"]
    check("B1.encerramento-guarda-o-que-se-sabe", rec.get("subefeitos_concluidos") == [f"{e1['id']}#0/0", f"{e1['id']}#0/1"]
          and rec.get("falhou_em") == f"{e1['id']}#0/2" and flags(B).get("b1a") is True and sim.balance(B, "pc-ana", "dinheiro") == 505,
          J(rec, ensure_ascii=False)[:300])

    # B2 — etapa de facção: falha RECUPERÁVEL depois de um efeito sem chave; um fato posterior não é sobrescrito
    must("faction", "set", "fac-guarda", "--json", J({"plans": [{"id": "ronda", "steps": [{"id": "s", "duration": "1h", "effects": [
        {"type": "flag.set", "name": "b2a", "value": 1},
        {"type": "entity.set", "id": "npc-lia", "field": "location", "value": "loc-loja"},
        {"type": "knowledge.tell", "from": "npc-guarda", "to": "npc-capitao", "fact": "fact-z"},
        {"type": "flag.set", "name": "b2b", "value": True}]}]}]}), camp="comp")
    rc, out = sh("time", "advance", "2h", "--op", "b2", "--reason", "x", camp="comp")
    e2 = next(e for e in evs(B) if e.get("faction") == "fac-guarda" and "conclui" in e.get("text", ""))
    r2 = e2.get("result") or {}
    check("B2.falha-recuperavel-pendente", rc != 0 and e2["status"] == "falhou" and not sim.is_permanent(e2) and r2.get("incerto")
          and r2.get("falhou_em") == f"{e2['id']}#0/2" and sim.load_clock(B).get("pending_op"), out[-200:])
    ins = must("time", "inspect", e2["id"], camp="comp")
    check("B2.inspect-mostra-sub-efeitos", f"sub {e2['id']}#0/1 entity.set: concluído" in ins and f"#0/2: FALHOU" in ins, ins[-400:])
    must("world", "move", "npc-lia", "loc-bar", "--reason", "fato posterior à falha", "--op", "mv-lia", camp="comp")
    F0 = flags(B)
    F0["b2a"] = 2  # outro fato posterior, sobre o efeito sem chave já aplicado
    sim.save_json(sim.flags_path(B), F0)
    must("fact", "learn", "npc-guarda", "fact-z", "--source", "relatório", camp="comp")
    must("time", "resume", camp="comp")
    e2 = sim.World(B).event(e2["id"])
    P = sim.load_json(B / "state" / "sim" / "factions.json", {})["factions"]["fac-guarda"]["plans"][0]
    nsub = [h for h in e2.get("history", []) if (h.get("result") or {}).get("subefeito")]
    check("B2.retomada-nao-reaplica-concluidos", e2["status"] == "processado" and meta(B, "npc-lia")["location"] == "loc-bar"
          and flags(B).get("b2a") == 2 and flags(B).get("b2b") is True and sim.knows(B, "npc-capitao", "fact-z")
          and P["status"] == "concluido" and len(nsub) == 4 and len({h["result"]["subefeito"] for h in nsub}) == 4,
          f"lia {meta(B, 'npc-lia')['location']} flags {flags(B)} subs {len(nsub)}")

    # B3 — conta do PJ: atraso + on_arrears com falha recuperável no meio; atraso contado uma vez
    must("fact", "add", "fact-w", "Dívida antiga", camp="comp")
    must("econ", "set", "--json", J({"routes": [{"id": "r-cara", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro", "qty": 99999,
                                                 "bill": True, "when": {"at": "10:00", "monthday": 10}, "authorized": "jogador: parcela",
                                                 "on_arrears": [{"type": "flag.set", "name": "b3a", "value": True},
                                                                {"type": "knowledge.tell", "from": "npc-guarda", "to": "npc-det", "fact": "fact-w"},
                                                                {"type": "flag.set", "name": "b3b", "value": True}]}]}), camp="comp")
    rc, out = sh("time", "advance", "1d4h", "--op", "b3", "--reason", "até dia 10, 11h", camp="comp")
    e3 = next(e for e in evs(B) if (e.get("gen_key") or "").startswith("econ:r-cara:"))
    check("B3.falha-no-meio-do-on-arrears", rc != 0 and e3["status"] == "falhou" and (e3.get("result") or {}).get("falhou_em") == f"{e3['id']}#0/atraso/1"
          and flags(B).get("b3a") is True and "b3b" not in flags(B), out[-200:])
    must("fact", "learn", "npc-guarda", "fact-w", "--source", "livro-caixa", camp="comp")
    must("time", "resume", camp="comp")
    must("time", "process", camp="comp")
    E = econ(B)
    atrasos = [e for e in evs(B) if (e.get("gen_key") or "").startswith("atraso:")]
    check("B3.retomada-sem-duplicar", {r["id"]: r for r in E["routes"]}["r-cara"]["arrears"] == 99999 and len(atrasos) == 1
          and flags(B).get("b3b") is True and sim.knows(B, "npc-det", "fact-w") and sim.World(B).event(e3["id"])["status"] == "processado")

    # B4 — erro de programação e queda do processo DENTRO do composto (no próprio processo)
    import quest_engine
    calls = {"n": 0, "modo": RuntimeError}

    @sim.effect("teste.queda")
    def _queda(w, f, key, ev):
        calls["n"] += 1
        if calls["n"] == 1:
            raise calls["modo"]("queda simulada")
        return {"ok": calls["n"]}
    for q, modo in (("quest-b4", RuntimeError), ("quest-b5", SystemExit)):
        calls.update(n=0, modo=modo)
        ent(B, q, "", status="em_andamento", type="principal")
        Q = sim.load_json(B / "state" / "sim" / "quests.json", {})
        Q["quests"][q] = {"consequences": {"concluida": [{"type": "flag.set", "name": f"{q}-a", "value": 1}, {"type": "teste.queda"},
                                                         {"type": "flag.set", "name": f"{q}-b", "value": True}]}}
        sim.save_json(B / "state" / "sim" / "quests.json", Q)
        w = sim.World(B)
        eid = quest_engine._transition(w, q, "concluida", "teste")
        try:
            sim.process_event(w, w.event(eid))
        except (sim.SimError, SystemExit):
            pass
        e = sim.World(B).event(eid)
        tag = "erro" if modo is RuntimeError else "queda"
        check(f"B4.{tag}.incerto-e-nao-concluido", sim.application(e) == ("parcial", 0, True) and list(sim.subeffects(e)) == [f"{eid}#0/0"]
              and (e["status"] == "falhou" and e["result"].get("falhou_em") == f"{eid}#0/1" if tag == "erro" else e["status"] == "processando"),
              J(e.get("result"), ensure_ascii=False)[:200])
        F0 = flags(B)
        F0[f"{q}-a"] = 2  # fato posterior
        sim.save_json(sim.flags_path(B), F0)
        w = sim.World(B)
        st, _ = sim.process_event(w, w.event(eid))
        check(f"B4.{tag}.retomada-conclui-sem-reaplicar", st == "processado" and flags(B).get(f"{q}-a") == 2 and flags(B).get(f"{q}-b") is True
              and calls["n"] == 2)

    # ================================================================== C. causalidade: facções e fatos
    D = camp("causa")
    for fid, txt in (("fact-furto", "Roubaram o cofre"), ("fact-fuga", "O ladrão fugiu pelo norte"), ("fact-carta", "Carta anônima"),
                     ("fact-selo", "O selo é falso"), ("fact-boato", "Dizem que o prefeito sabia")):
        must("fact", "add", fid, txt, camp="causa")
    must("npc", "set", "npc-guarda", "--tier", "2", "--goals", J([{"id": "dever", "priority": 5, "actions": [
        {"id": "relatar", "desc": "relata ao capitão", "duration": "1h",
         "preconditions": [{"type": "knows", "who": "npc-guarda", "fact": "fact-furto"}, {"type": "not", "of": {"type": "flag", "name": "relatado"}}],
         "effects": [{"type": "knowledge.tell", "from": "npc-guarda", "to": "npc-capitao", "fact": "fact-furto"},
                     {"type": "flag.set", "name": "relatado", "value": True}]}]}]), camp="causa")
    must("faction", "set", "fac-guarda", "--json", J({"plans": [
        {"id": "cacada", "steps": [{"id": "mobilizar", "duration": "2h",
                                    "start_conditions": [{"type": "knows", "who": "npc-capitao", "fact": "fact-furto"}],
                                    "done_conditions": [{"type": "knows", "who": "npc-capitao", "fact": "fact-fuga"}],
                                    "effects": [{"type": "flag.set", "name": "mobilizado", "value": True}]}]},
        {"id": "sigilo", "steps": [{"id": "s", "duration": "1h", "start_conditions": [{"type": "knows", "who": "npc-det", "fact": "fact-carta"}],
                                    "effects": [{"type": "flag.set", "name": "sigilo", "value": True}]}]},
        {"id": "boato", "steps": [{"id": "s", "duration": "1h", "start_conditions": [{"type": "knows", "who": "npc-loja", "fact": "fact-boato"}],
                                   "effects": [{"type": "flag.set", "name": "boato", "value": True}]}]}]}), camp="causa")
    must("time", "schedule", "Furto do cofre", "--in", "2h", "--loc", "loc-praca", "--hidden", "--op", "furto",
         "--effects", J({"type": "knowledge.witness", "fact": "fact-furto", "loc": "loc-praca"}), camp="causa")
    must("time", "schedule", "Relatório da patrulha", "--in", "1d1h", "--hidden", "--op", "relat", "--effects", J({"type": "log", "text": "x"}),
         camp="causa")
    must("time", "schedule", "Carta entregue ao detetive", "--hidden", "--op", "carta", "--effects", J({"type": "log", "text": "x"}), camp="causa")
    must("time", "process", camp="causa")
    carta = ev_gk(D, "schedule:carta")["id"]
    must("fact", "learn", "npc-det", "fact-carta", "--source", carta, camp="causa")  # origem explícita (evento já ocorrido)
    must("fact", "learn", "npc-loja", "fact-boato", "--source", "ouviu na feira", camp="causa")  # texto livre: sem origem
    must("fact", "learn", "npc-det", "fact-boato", "--source", "ouviu na feira", camp="causa")
    must("time", "advance", "1d3h", "--op", "c1", "--reason", "x", camp="causa")  # 10/11 08:00: etapa iniciada, conclusão às 09:00
    E1 = evs(D)
    furto = ev_gk(D, "schedule:furto")["id"]
    relat = ev_gk(D, "schedule:relat")["id"]
    dec = next(e for e in E1 if e.get("type") == "npc.decisao" and e.get("npc") == "npc-guarda" and "relata" in e.get("text", ""))
    tell = next(e for e in E1 if e.get("type") == "npc.acao" and e.get("cause") == dec["id"])
    start = next(e for e in E1 if (e.get("gen_key") or "").startswith("fac:fac-guarda:cacada:mobilizar:1#"))
    s_sig = next(e for e in E1 if (e.get("gen_key") or "").startswith("fac:fac-guarda:sigilo:"))
    s_bo = next(e for e in E1 if (e.get("gen_key") or "").startswith("fac:fac-guarda:boato:"))
    check("C1.inicio-da-etapa-com-origem", s_sig.get("sources") == [carta] and not s_sig.get("cause"), J({k: s_sig.get(k) for k in ("cause", "sources")}))
    check("C1.relato-como-causa-sem-repetir-na-origem", start.get("cause") == tell["id"] and not start.get("sources"),
          J({k: start.get(k) for k in ("cause", "sources")}))
    check("C1.sem-evento-sem-origem-inventada", not s_bo.get("sources") and not s_bo.get("cause"), J(s_bo.get("sources")))
    must("fact", "learn", "npc-capitao", "fact-fuga", "--source", relat, camp="causa")
    must("time", "advance", "2h", "--op", "c2", "--reason", "x", camp="causa")
    done = next(e for e in evs(D) if e.get("faction") == "fac-guarda" and "conclui etapa 'mobilizar'" in e.get("text", ""))
    raw_src = [r for r in sim.read_jsonl(D / "state" / "events.jsonl") if r.get("op") == "sources" and r["id"] == done["id"]]
    check("C1.conclusao-com-origem-do-que-sabia", done["status"] == "processado" and done.get("sources") == [relat] and len(raw_src) == 1
          and flags(D).get("mobilizado") is True, J(done.get("sources")))
    out = must("world", "chain", done["id"], camp="causa")
    check("C1.chain-da-faccao-alcanca-testemunho-e-relato", all(x in out for x in (start["id"], tell["id"], dec["id"], furto, relat))
          and out.count(f"└ {furto} ") == 1 and out.count(f"└ {tell['id']} ") == 1, out[-700:])
    out = must("world", "chain", relat, camp="causa")
    check("C1.consequencias-pela-origem", done["id"] in out.split("Consequências:")[1], out[-300:])
    out = must("world", "why", "fac-guarda", camp="causa")
    check("C1.why-da-faccao", "origem do conhecimento" in out and tell["id"] in out and relat in out, out[-500:])
    # C2 — fact learn: origem explícita validada, várias origens, texto livre, ensino × origem do conteúdo
    must("time", "schedule", "Carta lida na taverna", "--hidden", "--op", "leitura", "--effects", J({"type": "log", "text": "x"}), camp="causa")
    must("time", "schedule", "Selo examinado", "--hidden", "--op", "selo", "--effects", J({"type": "log", "text": "x"}), camp="causa")
    must("time", "schedule", "Conteúdo: o selo", "--hidden", "--op", "origem", "--effects",
         J({"type": "fact.create", "fact": "fact-lacre", "text": "O lacre foi trocado"}), camp="causa")
    must("time", "process", camp="causa")
    leit, selo, orig = (ev_gk(D, f"schedule:{k}")["id"] for k in ("leitura", "selo", "origem"))
    pend = sim.World(D).schedule({"type": "evento", "text": "futuro", "due": sim.World(D).now + 600})
    s0 = snap(D)
    rc1, o1 = sh("fact", "learn", "npc-det", "fact-selo", "--source", "evt-9999", camp="causa")
    rc2, o2 = sh("fact", "learn", "npc-det", "fact-selo", "--source", f"{leit},{pend}", camp="causa")
    check("C2.origem-invalida-recusada-sem-gravar", rc1 != 0 and rc2 != 0 and "nada foi gravado" in o1 + o2 and snap(D) == s0, (o1 + o2)[-200:])
    must("fact", "learn", "npc-det", "fact-selo", "--source", f"{leit}, {selo},{leit}", camp="causa")
    must("fact", "learn", "npc-det", "fact-lacre", "--source", leit, camp="causa")
    kd = sim.knowledge(D, "npc-det")
    check("C2.varias-origens-sem-duplicar", kd["fact-selo"].get("events") == [leit, selo] and kd["fact-lacre"].get("event") == leit
          and "event" not in kd["fact-boato"] and "events" not in kd["fact-boato"], J(kd, ensure_ascii=False)[:300])
    must("npc", "set", "npc-det", "--tier", "2", "--goals", J([{"id": "g", "priority": 1, "actions": [
        {"id": "denuncia", "desc": "denuncia a fraude", "preconditions": [
            {"type": "knows", "who": "npc-det", "fact": "fact-selo"}, {"type": "knows", "who": "npc-det", "fact": "fact-lacre"},
            {"type": "knows", "who": "npc-det", "fact": "fact-carta"}, {"type": "knows", "who": "npc-det", "fact": "fact-boato"},
            {"type": "not", "of": {"type": "flag", "name": "den"}}],
         "effects": [{"type": "flag.set", "name": "den", "value": True}]}]}]), camp="causa")
    must("npc", "decide", "npc-det", "--commit", "--op", "d1", camp="causa")
    dd = next(e for e in evs(D) if e.get("type") == "npc.decisao" and e.get("npc") == "npc-det")
    check("C2.decisao-usa-evento-que-ensinou-nao-a-origem-do-conteudo", dd.get("sources") == [leit, selo, carta] and orig not in dd.get("sources", []),
          J(dd.get("sources")))
    K = sim.load_facts(D)  # registro antigo, sem fonte nem evento
    K["known"].setdefault("npc-loja", {})["fact-selo"] = {"status": "sabe", "at": 0, "confidence": 1.0}
    sim.save_json(sim.facts_path(D), K)
    rcs = [sh("fact", "knows", "npc-loja", camp="causa")[0], sh("npc", "knows", "npc-loja", camp="causa")[0]]
    check("C2.registro-antigo-sem-source-compativel", rcs == [0, 0]
          and sim.knowledge_sources(sim.World(D), [{"type": "knows", "who": "npc-loja", "fact": "fact-selo"}]) == [])
    out = must("world", "chain", dd["id"], camp="causa")
    check("C2.chain-com-varias-origens-sem-repetir", out.count(f"└ {leit} ") == 1 and out.count(f"└ {selo} ") == 1, out[-400:])
    out = must("world", "verify", camp="causa")
    check("C2.verify-limpo", "OK" in out, out[-300:])
    sim.append_jsonl(D / "state" / "events.jsonl", {"op": "sources", "id": dd["id"], "sources": ["evt-9999"]})
    out = sh("world", "verify", camp="causa")[1]
    check("C2.verify-acusa-origem-inexistente", f"{dd['id']}: origem 'evt-9999' não existe" in out, out[-300:])

    # ================================================================== D. entradas econômicas antigas sem autorização
    G = camp("leg")
    import economy
    E = economy.load(G)  # entradas gravadas antes da regra 'authorized' (sem passar pelo 'econ set')
    E["routes"] += [{"id": "r-antiga", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro", "qty": 11, "bill": True,
                     "when": {"at": "10:00", "monthday": 10}},
                    {"id": "r-velha", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro", "qty": 7, "bill": True,
                     "when": {"at": "11:00", "monthday": 10}}]
    E["consumers"] += [{"id": "c-antigo", "who": "pc-ana", "res": "dinheiro", "qty": 2, "every": "1d"}]
    sim.save_json(G / "state" / "sim" / "economy.json", E)
    rc, out = sh("time", "advance", "2d", "--op", "g1", "--reason", "x", camp="leg")
    ref = {k: [e for e in evs(G) if (e.get("gen_key") or "").startswith(f"econ:{k}:")] for k in ("r-antiga", "r-velha", "c-antigo")}
    check("D1.antigas-nao-debitam-nem-geram-atraso", rc == 0 and "ATENÇÃO" in out and sim.balance(G, "pc-ana", "dinheiro") == 500
          and all(x and all(sim.is_permanent(e) for e in x) for x in ref.values()) and not econ(G).get("_bills")
          and not any(r.get("arrears") for r in econ(G)["routes"]) and not sim.load_clock(G).get("pending_op"), out[-200:])
    msg = ref["r-antiga"][0]["result"]["erro"]
    check("D1.mensagem-identifica-e-indica-o-comando", "r-antiga" in msg and "econ set --json" in msg and "econ remove r-antiga" in msg
          and "não são cobrados depois" in msg, msg)
    v = must("world", "verify", camp="leg")
    check("D1.verify-um-diagnostico-por-entrada", f"[economia] c-antigo: {len(ref['c-antigo'])} ciclo(s) recusado(s)" in v
          and "[economia] r-antiga: 1 ciclo(s) recusado(s)" in v and "r-antiga: debita pc-ana" in v, v[-600:])
    ok, out = refused("econ", "set", "--json", J({"routes": [{"id": "r-antiga", "from": "pc-ana", "to": "npc-loja", "res": "dinheiro",
                                                             "qty": 12, "bill": True, "when": {"at": "10:00", "monthday": 10}}]}),
                      camp="leg", needle="r-antiga: debita pc-ana")
    check("D2.alterar-antiga-sem-autorizar-continua-recusado", ok, out)
    must("econ", "set", "--json", J({"consumers": [{"id": "c-antigo", "who": "pc-ana", "res": "dinheiro", "qty": 2, "every": "1d",
                                                    "authorized": "jogador: assinatura do jornal"}]}), camp="leg")  # outras antigas não impedem
    aud = [e for e in evs(G) if e.get("type") == "economia" and "Configuração" in e.get("text", "")][-1]
    must("time", "advance", "1d", "--op", "g2", "--reason", "x", camp="leg")
    check("D2.autorizada-depois-cobra-so-dali-em-diante", sim.balance(G, "pc-ana", "dinheiro") == 498
          and (aud.get("result") or {}).get("autorizacoes") == {"c-antigo": "jogador: assinatura do jornal"}, J(aud.get("result"), ensure_ascii=False))
    must("econ", "remove", "r-velha", "--reason", "mestre: conta antiga sem autorização", "--op", "rm1", camp="leg")
    v = must("world", "verify", camp="leg")
    check("D3.removida-sai-dos-problemas-e-fica-o-historico", "r-velha: debita" not in v and "r-velha: 1 ciclo(s)" in v
          and "entrada já removida" in v and {r["id"]: r for r in econ(G)["routes"]}["r-velha"]["status"] == "removida", v[-500:])
    for e in ref["r-velha"]:
        must("time", "cancel", e["id"], "--reason", "mestre: ciclo sem autorização, nada cobrado", camp="leg")
    v = must("world", "verify", camp="leg")
    check("D3.depois-de-registrar-os-ciclos-sem-pendencia", "r-velha" not in v and "r-antiga" in v, v[-300:])
    check("D4.saldo-final-so-com-o-autorizado", sim.balance(G, "pc-ana", "dinheiro") == 498 and not econ(G).get("_bills"))
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
print("OK: auditoria de agência, compostos, causalidade e economia antiga (Etapa 3.2.2) passou")
