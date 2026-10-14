#!/usr/bin/env python3
"""Testes de faction_sim.py e economy.py, em raiz temporária (RPG_ROOT)."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
os.environ["RPG_SIM_MODULES"] = "faction_sim,economy"
import sim  # noqa: E402

sim.load_modules()
env = {**os.environ}


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def evs():
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def facs():
    return json.loads((C / "state" / "sim" / "factions.json").read_text())["factions"]


def plan(fid, pid):
    return next(p for p in facs()[fid]["plans"] if p["id"] == pid)


def bal(who, res):
    return sim.balance(C, who, res)


def adv(dur, op):
    return run("time", "advance", dur, "--op", op, "--reason", op)


def sched(text, effects, when="1h", *extra):
    run("time", "schedule", text, "--in", when, "--effects", json.dumps(effects), *extra)


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    run("new", "fac", "--name", "Fac", "--system", "aventura-2d6")
    C = tmp / "campaigns" / "fac"
    (C / "world.md").write_text("# Mundo\n\n## Facções\n- fac-guilda: mercadores\n- fac-coroa: nobreza\n- fac-ladroes: crime\n")
    for nid, fac in (("npc-mestra", "fac-guilda"), ("npc-rei", "fac-coroa"), ("npc-padeiro", "")):
        (C / "npcs" / f"{nid}.md").write_text(f"---\nid: {nid}\nname: {nid[4:].title()}\nstatus: vivo\n"
                                              + (f"faction: {fac}\n" if fac else "") + f"---\n# {nid}\n")
    for lid in ("loc-porto", "loc-mercado"):
        (C / "locations" / f"{lid}.md").write_text(f"---\nid: {lid}\nname: {lid[4:].title()}\n---\n# {lid}\n")
    run("time", "init", "--start", "1/1/1 08:00", "--seed", "3")
    sim.save_json(sim.res_path(C), {"holdings": {"fac-guilda": {"ouro": 3}}, "allow_negative": []})
    run("world", "rel-add", "fac-guilda", "fac-coroa", "aliada", "--note", "pacto antigo")
    rels_before = (C / "state" / "relations.jsonl").read_text()

    # --- set: validação (nada gravado se inválido)
    assert "não existe em world.md" in run("faction", "set", "fac-fantasma", "--json", "{}", ok=False)
    assert "0 e 100" in run("faction", "set", "fac-guilda", "--json", '{"territories":{"loc-porto":{"control":150}}}', ok=False)
    assert "inexistente" in run("faction", "set", "fac-guilda", "--json", '{"territories":{"loc-nada":{"control":10}}}', ok=False)
    assert "desconhecido" in run("faction", "set", "fac-guilda", "--json",
                                 '{"plans":[{"id":"x","steps":[{"id":"a","effects":[{"type":"nao.existe"}]}]}]}', ok=False)
    assert not (C / "state" / "sim" / "factions.json").exists()
    steps = [
        {"id": "subornar", "desc": "subornar a guarda", "duration": "1d", "cost": {"ouro": 5},
         "effects": [{"type": "faction.territory", "faction": "fac-guilda", "territory": "loc-porto", "control": 60,
                      "situation": "guarda comprada"}]},
        {"id": "golpe", "desc": "tomar o porto", "duration": "2d", "start_conditions": [{"type": "flag", "name": "pronto"}],
         "done_conditions": [{"type": "flag", "name": "sucesso"}],
         "effects": [{"type": "faction.territory", "faction": "fac-guilda", "territory": "loc-porto", "control": 100}]}]
    run("faction", "set", "fac-guilda", "--json", json.dumps({
        "goals": ["dominar o porto"], "leader": "npc-mestra",
        "territories": {"loc-porto": {"control": 20, "security": 40}},
        "plans": [{"id": "porto", "goal": "controlar o porto", "steps": steps},
                  {"id": "feira", "goal": "feira anual", "status": "interrompido",
                   "steps": [{"id": "montar", "desc": "montar barracas", "duration": "1h", "cost": {"ouro": 2}}]}]}))

    # --- sem recurso: não inicia, registra o motivo (1x por dia, sem spam)
    adv("1h", "a1")
    adv("1h", "a2")
    falta = [e for e in evs() if "semrecurso" in (e.get("gen_key") or "")]
    assert len(falta) == 1 and "fac-guilda tem 3 ouro (precisa 5)" in falta[0]["result"]["motivo"], falta
    assert falta[0]["hidden"] and bal("fac-guilda", "ouro") == 3 and plan("fac-guilda", "porto")["current"] == 0

    # --- com recurso: inicia, consome o custo uma vez, conclui depois da duração
    sched("tributo", [{"type": "resource.produce", "who": "fac-guilda", "res": "ouro", "qty": 10, "source": "tributo do mercado"}])
    adv("2h", "a3")
    assert bal("fac-guilda", "ouro") == 8, bal("fac-guilda", "ouro")  # 3 + 10 - 5
    ini = next(e for e in evs() if "inicia etapa 'subornar a guarda'" in e.get("text", ""))
    assert ini["status"] == "processado" and ini["hidden"]
    adv("12h", "a4")
    assert plan("fac-guilda", "porto")["current"] == 0  # ainda não terminou
    out = adv("1d", "a5")
    assert "conclui etapa 'subornar a guarda'" in out, out
    p = plan("fac-guilda", "porto")
    assert p["current"] == 1 and p["status"] == "ativo"
    t = facs()["fac-guilda"]["territories"]["loc-porto"]
    assert t["control"] == 60 and t["security"] == 40 and t["situation"] == "guarda comprada" and t["events"]
    fim = next(e for e in evs() if "conclui etapa 'subornar" in e.get("text", ""))
    assert fim["cause"] == ini["id"]  # rastreável
    assert bal("fac-guilda", "ouro") == 8

    # reprocessar início e conclusão não duplica consumo nem avanço
    w = sim.World(C)
    for e in (ini, fim):
        sim.process_event(w, dict(e, status="processando"))
        sim.process_event(w, dict(e, status="processando"))
    assert bal("fac-guilda", "ouro") == 8 and plan("fac-guilda", "porto")["current"] == 1
    assert len([e for e in evs() if "inicia etapa 'subornar" in e.get("text", "")]) == 1

    # --- etapa 2 espera start_conditions; done_conditions falsas → interrompido com motivo
    adv("1d", "a6")
    assert not any("inicia etapa 'tomar o porto'" in e.get("text", "") for e in evs())
    sched("sinal", [{"type": "flag.set", "name": "pronto", "value": True}], "3h")
    adv("4h", "a7")
    g = next(e for e in evs() if "inicia etapa 'tomar o porto'" in e.get("text", ""))
    assert g["due"] == next(e for e in evs() if e.get("text") == "sinal")["due"]  # reagiu na hora da mudança
    adv("2d", "a8")
    p = plan("fac-guilda", "porto")
    assert p["status"] == "interrompido" and "sucesso" in p["reason"], p
    assert facs()["fac-guilda"]["territories"]["loc-porto"]["control"] == 60
    assert any("interrompido" in e.get("text", "") and "sucesso" in e["result"]["motivo"] for e in evs())
    assert "interrompido" in run("faction", "plans", "fac-guilda")

    # --- faction.plan.start + step manual com --op (idempotente)
    sched("reabrir feira", [{"type": "faction.plan.start", "faction": "fac-guilda", "plan": "feira"}], "1h")
    out = run("faction", "step", "fac-guilda", "feira", "--op", "st1")
    assert "parado" in out, out  # ainda interrompido: plan.start só acontece daqui a 1h
    adv("1h", "a9")  # plan.start → ouvinte inicia a etapa → conclui em 1h
    assert plan("fac-guilda", "feira")["status"] == "ativo" and bal("fac-guilda", "ouro") == 6
    assert "já foi aplicada" in run("faction", "step", "fac-guilda", "feira", "--op", "st1", ok=False)
    assert "andamento" in run("faction", "step", "fac-guilda", "feira", "--op", "st2")
    assert bal("fac-guilda", "ouro") == 6
    adv("1h", "a10")
    assert plan("fac-guilda", "feira")["status"] == "concluido"
    assert "concluido" in run("faction", "show", "fac-guilda")

    # --- postura: causa e histórico; relações antigas intactas; condição faction.stance
    sched("traição", [{"type": "faction.stance", "a": "fac-guilda", "b": "fac-coroa", "stance": "hostil", "score_delta": -130,
                       "reason": "a coroa confiscou navios"}])
    sched("reação", [{"type": "flag.set", "name": "guerra_fria", "value": True}], "2h",
          "--conditions", '{"type":"faction.stance","a":"fac-coroa","b":"fac-guilda","in":["hostil","guerra"]}')
    adv("2h", "a11")
    F = facs()
    trai = next(e for e in evs() if e.get("text") == "traição")
    for x, y in (("fac-guilda", "fac-coroa"), ("fac-coroa", "fac-guilda")):
        assert F[x]["stance"][y] == {"stance": "hostil", "score": -100}, F[x]["stance"]
        h = [h for h in F[x]["history"] if h["type"] == "stance"][-1]
        assert h["cause"] == trai["id"] and h["reason"] == "a coroa confiscou navios" and h["de"]["stance"] == "neutra"
    assert (C / "state" / "relations.jsonl").read_text() == rels_before
    assert json.loads((C / "state" / "sim" / "flags.json").read_text()).get("guerra_fria") is True
    out = run("faction", "relations", "fac-guilda")
    assert "hostil" in out and "aliada" in out  # postura nova + relação antiga preservada
    out = run("faction", "show", "fac-coroa")
    assert "npc-rei" in out and "hostil" in out  # membros derivados do frontmatter

    # --- território: persiste entre processos, limites validados no efeito
    sched("muralha", [{"type": "faction.territory", "faction": "fac-coroa", "territory": "loc-porto", "control": 30, "security": 90}])
    adv("1h", "a12")
    out = run("faction", "territory", "loc-porto")
    assert "controla: fac-guilda (controle 60" in out and "influencia: fac-coroa (controle 30" in out, out
    bad = [{"type": "faction.territory", "faction": "fac-coroa", "territory": "loc-porto", "control": 101}]
    ev_before = (C / "state" / "events.jsonl").read_text()
    assert "0 e 100" in run("time", "schedule", "inválido", "--in", "1h", "--effects", json.dumps(bad), ok=False)  # 3.1.1: recusado
    assert (C / "state" / "events.jsonl").read_text() == ev_before
    w0 = sim.World(C)  # motor: se chegar à execução, é falha permanente que não bloqueia
    w0.schedule({"type": "evento", "text": "inválido", "due": w0.now + 60, "effects": bad})
    out = run("time", "advance", "1h", "--op", "a13", "--reason", "x")  # Fase 3.1.1: falha permanente não bloqueia
    assert "0 e 100" in out and "FALHA PERMANENTE" in out
    run("time", "cancel", next(e["id"] for e in evs() if e.get("text") == "inválido"), "--reason", "teste")
    assert facs()["fac-coroa"]["territories"]["loc-porto"]["control"] == 30

    # --- economia desligada: nada muda
    assert "desligada" in run("econ", "status")
    assert "desligada" in run("econ", "set", "--json", "{}", ok=False)
    res_before = (C / "state" / "sim" / "resources.json").read_text()
    sim.save_json(C / "state" / "sim" / "economy.json", {"producers": [{"id": "prod-x", "who": "npc-padeiro", "res": "pao",
                                                                        "qty": 1, "every": "1h"}]})
    adv("3h", "e0")
    assert (C / "state" / "sim" / "resources.json").read_text() == res_before
    assert not any(e.get("type") == "economia" for e in evs())

    # --- economia ligada: produção/consumo/rota com escassez, sem duplicar
    with open(C / "campaign.yaml", "a") as f:
        f.write("economy: on\n")
    run("econ", "set", "--json", '{"producers":[{"id":"x","who":"npc-nada","res":"trigo","qty":1,"every":"1d"}]}', ok=False)
    run("econ", "set", "--json", json.dumps({
        "producers": [{"id": "prod-trigo", "who": "npc-mestra", "res": "trigo", "qty": 10, "every": "1d"}],
        "consumers": [{"id": "cons-pao", "who": "npc-padeiro", "res": "trigo", "qty": 3, "every": "1d"}],
        "routes": [{"id": "rota-moinho", "from": "npc-mestra", "to": "npc-padeiro", "res": "trigo", "qty": 4, "every": "1d",
                    "status": "ativa"}],
        "markets": {"loc-mercado": {"goods": {"trigo": {"base_price": 2, "demand": 30, "supply": 10},
                                              "sal": {"base_price": 4, "demand": 1, "stock_holder": "npc-mestra"}}}}}))
    now = sim.load_clock(C)["now"]
    nxt = (now // 1440 + 1) * 1440 - now  # minutos até a virada do dia
    adv(f"{nxt + 1440}min", "e1")  # cruza 2 viradas de dia
    assert bal("npc-mestra", "trigo") == 12, bal("npc-mestra", "trigo")  # 2×10 - 2×4
    assert bal("npc-padeiro", "trigo") == 5, bal("npc-padeiro", "trigo")  # dia1: falta (0<3), +4; dia2: -3, +4
    esc = [e for e in evs() if e.get("text", "").startswith("Escassez: cons-pao")]
    assert len(esc) == 1 and "npc-padeiro tem 0 trigo" in esc[0]["result"]["motivo"] and esc[0]["hidden"]
    w = sim.World(C)
    for e in [e for e in evs() if e.get("type") == "economia" and (e.get("gen_key") or "").startswith("econ:")]:
        sim.process_event(w, dict(e, status="processando"))
    assert bal("npc-mestra", "trigo") == 12 and bal("npc-padeiro", "trigo") == 5
    assert len([e for e in evs() if e.get("text", "").startswith("Escassez")]) == 1
    assert "prod-trigo" in run("econ", "ledger", "npc-mestra") and "trigo=12" in run("econ", "ledger", "npc-mestra")

    # rota interrompida por evento
    sched("ponte caiu", [{"type": "econ.route.status", "route": "rota-moinho", "status": "interrompida", "reason": "ponte caiu"}], "1min")
    adv("1d", "e2")
    assert bal("npc-mestra", "trigo") == 22 and bal("npc-padeiro", "trigo") == 2  # produz 10; padeiro só consome 3
    r = json.loads((C / "state" / "sim" / "economy.json").read_text())["routes"][0]
    assert r["status"] == "interrompida" and r["history"][-1]["reason"] == "ponte caiu"

    # preço: base × clamp(demanda/oferta), razão limitada a 0.5–3
    assert "6 = base 2 × 3" in run("econ", "price", "loc-mercado", "trigo")
    assert "12 = base 4 × 3" in run("econ", "price", "loc-mercado", "sal")  # estoque do stock_holder = 0 → teto
    run("econ", "price", "loc-mercado", "ouro", ok=False)

    # --- troca: inválidas recusadas sem efeito parcial; válida tudo-ou-nada; idempotente
    res_before, n_ev = (C / "state" / "sim" / "resources.json").read_text(), len(evs())
    trade = ("econ", "trade", "npc-padeiro", "npc-mestra", "trigo")
    assert "precisa 6 ouro" in run(*trade, "2", "--price-res", "ouro", "--unit-price", "3", "--op", "t1", ok=False)
    assert "não existe" in run("econ", "trade", "npc-nada", "npc-mestra", "trigo", "2", "--price-res", "ouro",
                               "--unit-price", "3", "--op", "t2", ok=False)
    assert "quantidade inválida" in run(*trade, "0", "--price-res", "ouro", "--unit-price", "3", "--op", "t3", ok=False)
    assert "precisa 999 trigo" in run(*trade, "999", "--price-res", "ouro", "--unit-price", "0.001", "--op", "t4", ok=False)
    assert (C / "state" / "sim" / "resources.json").read_text() == res_before and len(evs()) == n_ev
    sched("salário", [{"type": "resource.produce", "who": "npc-padeiro", "res": "ouro", "qty": 10, "source": "salário"}], "1min")
    adv("1min", "t-sal")
    run(*trade, "2", "--price-res", "ouro", "--unit-price", "3", "--op", "t5")
    assert (bal("npc-padeiro", "trigo"), bal("npc-padeiro", "ouro"), bal("npc-mestra", "trigo"), bal("npc-mestra", "ouro")) == (4, 4, 20, 6)
    assert "já foi aplicada" in run(*trade, "2", "--price-res", "ouro", "--unit-price", "3", "--op", "t5", ok=False)
    w = sim.World(C)
    tr = next(e for e in evs() if e.get("gen_key") == "econ:trade:t5")
    sim.process_event(w, dict(tr, status="processando"))
    assert bal("npc-padeiro", "ouro") == 4 and bal("npc-mestra", "ouro") == 6
    before = (C / "state" / "sim" / "resources.json").read_text()
    for bad in ({"qty": 1, "unit_price": 1000}, {"qty": 1000, "unit_price": 0.001}):  # vendedor ok/comprador sem; e vice-versa
        try:
            sim.apply(w, dict({"type": "econ.trade", "buyer": "npc-padeiro", "seller": "npc-mestra", "res": "trigo",
                               "price_res": "ouro"}, **bad), "teste#0", {"id": tr["id"]})
            raise AssertionError("troca inválida aceita")
        except sim.SimError:
            pass
    assert (C / "state" / "sim" / "resources.json").read_text() == before

    # --- persistência (novo processo) e verificador
    out = run("faction", "show", "fac-guilda")
    assert "Território loc-porto: controle 60, segurança 40 — guarda comprada" in out and "npc-mestra" in out, out
    assert "OK" in run("world", "verify"), run("world", "verify")
    (C / "maps").mkdir(exist_ok=True)
    (C / "maps" / "map-teste.json").write_text(json.dumps({"id": "map-teste", "name": "Teste", "type": "regiao",
        "points": [{"id": "poi-porto", "ref": "loc-porto", "name": "Porto"}], "areas": [{"id": "reg-norte", "name": "Norte"}]}))
    run("faction", "set", "fac-ladroes", "--json", '{"territories":{"reg-norte":{"control":70}}}')
    assert "No mapa map-teste (Teste): poi-porto" in run("faction", "territory", "loc-porto")
    assert "controla: fac-ladroes" in run("faction", "territory", "reg-norte")
    (C / "maps" / "map-teste.json").unlink()
    F = json.loads((C / "state" / "sim" / "factions.json").read_text())
    F["factions"]["fac-fantasma"] = {"territories": {"loc-porto": {"control": 150}},
                                     "plans": [{"id": "p", "steps": [{"id": "s", "effects": [{"type": "nao.existe"}]}]}]}
    sim.save_json(C / "state" / "sim" / "factions.json", F)
    out = run("world", "verify")
    for s in ("fac-fantasma: não existe em world.md", "fac-ladroes: território inexistente 'reg-norte'", "entre 0 e 100",
              "effect desconhecido: 'nao.existe'"):
        assert s in out, (s, out)
finally:
    shutil.rmtree(tmp)
print("OK: testes de facções e economia passaram")
