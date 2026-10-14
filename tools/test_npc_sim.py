#!/usr/bin/env python3
"""Testes de NPCs autônomos (tools/npc_sim.py), em raiz temporária — nunca toca campanhas reais."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
os.environ["RPG_SIM_MODULES"] = "npc_sim"
import sim  # noqa: E402
import npc_sim  # noqa: E402

env = {**os.environ, "RPG_ROOT": str(tmp), "RPG_SIM_MODULES": "npc_sim"}


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def evs(C):
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def loc(C, npc):
    return sim.read_meta(C / "npcs" / f"{npc}.md")["location"]


def J(x):
    return json.dumps(x)


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    run("new", "npcs", "--name", "NPCs", "--system", "aventura-2d6")
    C = tmp / "campaigns" / "npcs"
    for nid, l in (("npc-padeiro", "loc-padaria"), ("npc-ladra", "loc-porto"), ("npc-capanga", "loc-casa"),
                   ("npc-informante", "loc-porto")):
        (C / "npcs" / f"{nid}.md").write_text(f"---\nid: {nid}\nname: {nid[4:].title()}\nstatus: vivo\nlocation: {l}\n---\n# {nid}\n")
    for lid in ("loc-padaria", "loc-casa", "loc-porto", "loc-longe"):
        (C / "locations" / f"{lid}.md").write_text(f"---\nid: {lid}\nname: {lid[4:].title()}\n---\n# {lid}\n\n## Conhecido pelos jogadores\n")
    run("time", "init", "--seed", "7")  # Dia 1, 08:00 → t=480

    # ---- rotina (nível 1)
    rot = [{"from": "06:00", "to": "18:00", "loc": "loc-padaria", "activity": "assar pão"},
           {"from": "18:00", "to": "06:00", "loc": "loc-casa", "activity": "dormir"}]
    assert "não existe" in run("npc", "set", "npc-padeiro", "--routine", J([{"from": "06:00", "to": "07:00", "loc": "loc-nada"}]), ok=False)
    assert "horário inválido" in run("npc", "set", "npc-padeiro", "--routine", J([{"from": "25:00", "to": "07:00", "loc": "loc-casa"}]), ok=False)
    assert not npc_sim.st_path(C).exists()  # recusado: nada gravado
    run("npc", "set", "npc-padeiro", "--routine", J(rot), "--needs", "farinha")
    assert "assar pão" in run("npc", "show", "npc-padeiro")  # persistiu (processo novo)
    run("time", "advance", "9h", "--op", "r1", "--reason", "x")  # 17:00
    assert loc(C, "npc-padeiro") == "loc-padaria"
    assert not [e for e in evs(C) if e.get("type") == "npc.rotina"]  # nada além do intervalo, nada futuro
    run("time", "advance", "2h", "--op", "r2", "--reason", "x")  # 19:00
    assert loc(C, "npc-padeiro") == "loc-casa"
    r = [e for e in evs(C) if e.get("type") == "npc.rotina"]
    assert [(e["due"], e["status"]) for e in r] == [(18 * 60, "processado")], r
    assert all(e.get("hidden") for e in r)
    run("time", "advance", "12h", "--op", "r3", "--reason", "x")  # Dia 2, 07:00
    assert loc(C, "npc-padeiro") == "loc-padaria"
    r = [e for e in evs(C) if e.get("type") == "npc.rotina"]
    assert [e["due"] for e in r] == [18 * 60, 1440 + 6 * 60] and all(e["status"] == "processado" for e in r)
    # reprocessar / gerar de novo o mesmo intervalo não duplica
    run("time", "process")
    w = sim.World(C)
    npc_sim._g_routine(w, 480, w.now)
    assert len([e for e in evs(C) if e.get("type") == "npc.rotina"]) == 2
    assert "já foi aplicada" in run("time", "advance", "1h", "--op", "r3", "--reason", "x", ok=False)

    # ---- decisão (nível 2): prioridade, recursos, idempotência
    goals = [{"id": "lucro", "desc": "ganhar dinheiro", "priority": 1, "actions": [
                 {"id": "furtar", "desc": "furtar bolsas", "utility": 5, "effects": [{"type": "flag.set", "name": "furtou", "value": True}]}]},
             {"id": "seguranca", "desc": "não ser pega", "priority": 3, "actions": [
                 {"id": "vigiar", "desc": "vigiar a guarda", "utility": 3, "risk": 1, "duration": "2h",
                  "effects": [{"type": "flag.set", "name": "vigiou", "value": True}]}]},
             {"id": "poder", "desc": "comprar a guarda", "priority": 10, "actions": [
                 {"id": "subornar", "desc": "subornar o guarda", "utility": 5,
                  "preconditions": [{"type": "resource.gte", "who": "npc-ladra", "res": "ouro", "qty": 10}],
                  "effects": [{"type": "resource.consume", "who": "npc-ladra", "res": "ouro", "qty": 10}]}]}]
    assert "desconhecido" in run("npc", "set", "npc-ladra", "--goals", J([{"id": "x", "actions": [{"id": "a", "effects": [{"type": "nao.existe"}]}]}]), ok=False)
    run("npc", "set", "npc-ladra", "--goals", J(goals))
    n0 = len(sim.read_jsonl(C / "state" / "events.jsonl"))
    out = run("npc", "decide", "npc-ladra")
    assert "ESCOLHA: seguranca/vigiar" in out and "escore 8" in out, out  # 3*3-1=8 > 1*5
    assert "descartada poder/subornar" in out and "tem 0 ouro (precisa 10)" in out, out  # sem meios não age
    assert len(sim.read_jsonl(C / "state" / "events.jsonl")) == n0  # sem --commit nada é gravado
    out = run("npc", "decide", "npc-ladra", "--commit", "--op", "d1")
    dec = next(e for e in evs(C) if e.get("type") == "npc.decisao")
    act = next(e for e in evs(C) if e.get("type") == "npc.acao")
    assert dec["hidden"] and act["hidden"] and act["cause"] == dec["id"] and act["due"] == w.now + 120 and act["status"] == "pendente"
    run("npc", "decide", "npc-ladra", "--commit", "--op", "d1")  # mesma op: não duplica
    assert len([e for e in evs(C) if e.get("type") in ("npc.decisao", "npc.acao")]) == 2
    assert "já em andamento" in run("npc", "decide", "npc-ladra")  # ação pendente não é escolhida de novo
    run("time", "advance", "1h", "--op", "d-espera", "--reason", "x")
    assert "vigiou" not in sim.load_json(sim.flags_path(C), {})  # não executa antes da hora
    run("time", "advance", "1h", "--op", "d-fim", "--reason", "x")
    assert sim.load_json(sim.flags_path(C), {}).get("vigiou") is True
    sim.save_json(sim.res_path(C), {"holdings": {"npc-ladra": {"ouro": 12}}, "allow_negative": []})
    assert "ESCOLHA: poder/subornar" in run("npc", "decide", "npc-ladra")  # com meios, a prioridade maior vence

    # ---- conhecimento: quem não sabe não age; após knowledge.tell passa a agir
    run("fact", "add", "fact-traicao", "O capitão vende segredos", "--about", "npc-informante")
    run("fact", "add", "fact-divida", "O capanga deve à guilda", "--about", "npc-capanga")
    run("fact", "learn", "npc-informante", "fact-traicao", "--source", "viu")
    run("npc", "set", "npc-capanga", "--goals", J([{"id": "vinganca", "priority": 2, "actions": [
        {"id": "denunciar", "desc": "denunciar o capitão", "utility": 4,
         "preconditions": [{"type": "knows", "who": "npc-capanga", "fact": "fact-traicao"}],
         "effects": [{"type": "flag.set", "name": "denuncia", "value": True}]}]}]))
    out = run("npc", "decide", "npc-capanga", "--commit", "--op", "k1")
    assert "sem ação possível" in out and "NÃO conhece fact-traicao" in out, out
    k1 = next(e for e in evs(C) if e.get("type") == "npc.decisao" and e.get("npc") == "npc-capanga")
    assert "sem ação possível" in k1["text"] and not [e for e in evs(C) if e.get("type") == "npc.acao" and e.get("npc") == "npc-capanga"]
    assert "NÃO conhece fact-traicao" in run("npc", "why", k1["id"])
    run("time", "schedule", "informante conta", "--in", "30min", "--hidden", "--effects",
        J({"type": "knowledge.tell", "from": "npc-informante", "to": "npc-capanga", "fact": "fact-traicao"}))
    run("time", "advance", "30min", "--op", "k-conta", "--reason", "x")
    out = run("npc", "decide", "npc-capanga", "--commit", "--op", "k2")
    assert "ESCOLHA: vinganca/denunciar" in out and "npc-capanga conhece fact-traicao" in out, out
    k2 = [e for e in evs(C) if e.get("type") == "npc.decisao" and e.get("npc") == "npc-capanga"][-1]
    a2 = next(e for e in evs(C) if e.get("type") == "npc.acao" and e.get("cause") == k2["id"])
    out = run("npc", "why", a2["id"])  # da ação para a decisão que a causou
    assert k2["id"] in out and "ESCOLHA: vinganca/denunciar" in out and "ok: npc-capanga conhece" in out, out
    out = run("npc", "knows", "npc-capanga")
    assert out.index("ACREDITA") < out.index("fact-traicao") and "fact-divida" in out.split("DESCONHECE")[1], out
    run("time", "process")
    assert sim.load_json(sim.flags_path(C), {}).get("denuncia") is True
    hist = run("npc", "history", "npc-capanga")
    assert k1["id"] in hist and k2["id"] in hist and a2["id"] in hist

    # ---- proposta do mestre (nível 3) + viagem com duração explícita
    base = {"desc": "vai ao porto", "duration": "1h",
            "preconditions": [{"type": "entity", "id": "npc-capanga", "field": "status", "eq": "vivo"}],
            "effects": [{"type": "npc.travel", "npc": "npc-capanga", "to": "loc-porto", "duration": "5h"}]}
    n0 = len(sim.read_jsonl(C / "state" / "events.jsonl"))
    assert "recusada" in run("npc", "propose", "npc-capanga", "--action", J(dict(base, effects=[{"type": "voar"}])), ok=False)
    assert "loc-atlantida" in run("npc", "propose", "npc-capanga", "--action",
                                  J(dict(base, effects=[{"type": "npc.travel", "npc": "npc-capanga", "to": "loc-atlantida"}])), ok=False)
    assert "precondições falsas" in run("npc", "propose", "npc-capanga", "--action",
                                       J(dict(base, preconditions=[{"type": "flag", "name": "nunca"}])), ok=False)
    assert len(sim.read_jsonl(C / "state" / "events.jsonl")) == n0  # recusas não gravam nada
    t0 = sim.World(C).now
    assert "validada" in run("npc", "propose", "npc-capanga", "--action", J(base), "--op", "p1")
    run("npc", "propose", "npc-capanga", "--action", J(base), "--op", "p1")  # idempotente
    assert len([e for e in evs(C) if e.get("type") == "npc.proposta"]) == 1
    run("time", "advance", "3h", "--op", "v1", "--reason", "x")
    assert loc(C, "npc-capanga") == "loc-casa"  # frontmatter mantém a origem durante a viagem
    assert "EM VIAGEM: loc-casa → loc-porto" in run("npc", "show", "npc-capanga")
    assert "em viagem" in run("npc", "decide", "npc-capanga")
    assert "OK" in run("world", "verify")  # chegada pendente: coerente
    run("time", "advance", "3h", "--op", "v2", "--reason", "x")
    arr = next(e for e in evs(C) if e.get("type") == "npc.chegada")
    assert arr["due"] == t0 + 6 * 60 and arr["status"] == "processado" and loc(C, "npc-capanga") == "loc-porto"
    assert "travel" not in npc_sim.load(C)["npcs"]["npc-capanga"]

    # ---- viagem estimada pelo mapa (velocidade KM_POR_DIA) e sem mapa
    w = sim.World(C)
    assert npc_sim.travel_minutes(w, "loc-porto", "loc-longe") is None
    (C / "maps").mkdir(exist_ok=True)
    mp = C / "maps" / "map-teste.json"
    mp.write_text(J({"id": "map-teste", "size": {"unit": "km", "per_cell": 6}, "points": [
        {"id": "poi-porto", "ref": "loc-porto", "x": 0, "y": 0}, {"id": "poi-longe", "ref": "loc-longe", "x": 3, "y": 4}]}))
    assert npc_sim.travel_minutes(w, "loc-porto", "loc-longe") == 1440  # 30 km a 30 km/dia
    mp.unlink()

    # ---- mesma seed → mesma decisão (empate resolvido pelo rng da seed)
    tie = [{"id": "g", "priority": 1, "actions": [{"id": f"a{i}", "utility": 2, "effects": [{"type": "log"}]} for i in range(4)]}]
    run("npc", "set", "npc-informante", "--goals", J(tie))
    shutil.copytree(C, tmp / "campaigns" / "copia")
    C2 = tmp / "campaigns" / "copia"
    r1, r2 = (npc_sim.evaluate(sim.World(c), "npc-informante") for c in (C, C2))
    assert r1["escolha"]["action"] == r2["escolha"]["action"] and r1["candidatas"] == r2["candidatas"]
    assert run("npc", "decide", "npc-informante").split("\n")[1] == run("npc", "decide", "npc-informante", "--campaign", "copia").split("\n")[1]
    ck = sim.load_json(sim.clock_path(C2), {})
    sim.save_json(sim.clock_path(C2), dict(ck, seed=8))
    r3 = npc_sim.evaluate(sim.World(C2), "npc-informante")
    assert [c["desempate"] for c in r1["candidatas"]] != [c["desempate"] for c in r3["candidatas"]]

    # ---- gerador diário (nível >= 2): decide sozinho às 06:00, com causa
    run("npc", "set", "npc-ladra", "--tier", "2")
    run("time", "advance", "1d", "--op", "dia", "--reason", "x")
    g = next(e for e in evs(C) if e.get("type") == "npc.gatilho")
    d = next(e for e in evs(C) if e.get("type") == "npc.decisao" and e.get("cause") == g["id"])
    assert g["due"] % 1440 == 6 * 60 and "subornar" in d["text"] and sim.balance(C, "npc-ladra", "ouro") == 2
    assert len([e for e in evs(C) if e.get("type") == "npc.gatilho"]) == 1

    # ---- privacidade: nada do módulo aparece para o jogador
    pub = run("world", "timeline", "--public")
    assert not any(x in pub for x in ("decide", "rotina", "proposta", "chega a", "reavalia", "assar")), pub

    # ---- verificador
    assert "OK" in run("world", "verify")
    S = npc_sim.load(C)
    S["npcs"]["npc-padeiro"]["routine"][0]["loc"] = "loc-sumiu"
    S["npcs"]["npc-informante"]["travel"] = {"from": "loc-porto", "to": "loc-longe", "arrive": 0, "event": "evt-9999"}
    sim.save_json(npc_sim.st_path(C), S)
    out = run("world", "verify")
    assert "npc-padeiro rotina: local 'loc-sumiu' não existe" in out and "npc-informante em viagem" in out, out
    p = C / "npcs" / "npc-padeiro.md"
    p.write_text(p.read_text().replace("status: vivo", "status: morto"))
    assert "npc-padeiro está morto mas ainda tem rotina" in run("world", "verify")
finally:
    shutil.rmtree(tmp)
print("OK: testes de NPCs autônomos passaram")
