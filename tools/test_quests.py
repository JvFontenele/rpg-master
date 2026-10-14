#!/usr/bin/env python3
"""Testes do motor de missões (tools/quest_engine.py), em raiz temporária."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
os.environ["RPG_SIM_MODULES"] = "quest_engine"
import sim  # noqa: E402

sim.load_modules()
env = {**os.environ, "RPG_ROOT": str(tmp), "RPG_SIM_MODULES": "quest_engine"}


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def evs():
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def st(q):
    return sim.read_meta(C / "quests" / f"{q}.md")["status"]


def Q():
    return json.loads((C / "state" / "sim" / "quests.json").read_text())["quests"]


def flags():
    return json.loads((C / "state" / "sim" / "flags.json").read_text())


def define(q, d, ok=True):
    return run("quest", "define", q, "--json", json.dumps(d), ok=ok)


def now():
    return json.loads((C / "state" / "clock.json").read_text())["now"]


n_op = [0]


def advance(dur):
    n_op[0] += 1
    return run("time", "advance", dur, "--op", f"t{n_op[0]}", "--reason", "teste")


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    run("new", "q", "--name", "Q", "--system", "aventura-2d6")
    C = tmp / "campaigns" / "q"
    (C / "characters" / "pc-ana.md").write_text("---\nid: pc-ana\nname: Ana\nlocation: loc-castelo\n---\n# Ana\n")
    (C / "npcs" / "npc-rei.md").write_text("---\nid: npc-rei\nname: Rei\nstatus: vivo\nlocation: loc-castelo\n---\n# Rei\n")
    (C / "locations" / "loc-castelo.md").write_text("---\nid: loc-castelo\nname: Castelo\n---\n# Castelo\n")
    for q, s in (("quest-mapa", "oculta"), ("quest-tesouro", "disponivel"), ("quest-escolta", "ativa"),
                 ("quest-entrega", "aceita"), ("quest-auto", "em_andamento"), ("quest-filha", "disponivel"),
                 ("quest-a", "disponivel"), ("quest-b", "disponivel")):
        (C / "quests" / f"{q}.md").write_text(
            f"---\nid: {q}\nname: {q[6:].title()}\nstatus: {s}\ngiver: npc-rei\nlocation: loc-castelo\n---\n# {q}\n\n"
            "## Conhecido pelos jogadores\n\n## Objetivos\n- [x] achar o mapa (texto narrativo NÃO conclui)\n")
    run("time", "init", "--start", "1/1/1 08:00", "--seed", "5")

    # --- definição e validação
    define("quest-mapa", {
        "objectives": [{"id": "achar", "desc": "Encontrar o mapa", "done_conditions": [{"type": "flag", "name": "mapa_achado"}]},
                       {"id": "extra", "desc": "Copiar o mapa", "optional": True}],
        "unlock_conditions": [{"type": "flag", "name": "rumor"}],
        "rewards": [{"type": "resource.produce", "who": "pc-ana", "res": "ouro", "qty": 50, "source": "quest-mapa"}],
        "consequences": {"concluida": [{"type": "flag.set", "name": "rei_grato", "value": True}]},
        "public": "O rei quer o mapa antigo.", "gm": "SEGREDO: o mapa é falso"})
    assert "desconhecido" in define("quest-tesouro", {"unlock_conditions": [{"type": "nao.existe"}]}, ok=False)
    assert "não é outra missão" in define("quest-tesouro", {"depends_on": ["quest-fantasma"]}, ok=False)
    assert "não existe" in define("quest-nada", {}, ok=False)
    assert "campos desconhecidos" in define("quest-tesouro", {"descricao": "x"}, ok=False)
    define("quest-a", {"depends_on": ["quest-b"]})
    assert "ciclo" in define("quest-b", {"depends_on": ["quest-a"]}, ok=False)
    define("quest-tesouro", {"depends_on": ["quest-mapa"], "objectives": [{"id": "abrir", "desc": "Abrir o baú"}]})

    # --- visão pública: oculta não aparece nem vaza
    pub = run("quest", "list", "--public")
    assert "quest-mapa" not in pub and "quest-tesouro" in pub
    assert "oculta" in run("quest", "show", "quest-mapa", "--public", ok=False)
    assert "SEGREDO" in run("quest", "show", "quest-mapa")  # o mestre vê tudo

    # --- fluxo inválido recusado (oculta → concluida)
    assert "não permitida" in run("quest", "set-status", "quest-mapa", "concluida", "--reason", "x", ok=False)
    assert st("quest-mapa") == "oculta"

    # --- desbloqueio por condição via listener, com causa rastreável
    run("time", "schedule", "Boato no mercado", "--in", "1h", "--effects", '{"type":"flag.set","name":"rumor","value":true}')
    rumor = next(e["id"] for e in evs() if e.get("text") == "Boato no mercado")
    assert st("quest-mapa") == "oculta"
    advance("2h")
    assert st("quest-mapa") == "disponivel", st("quest-mapa")
    unlock = next(e for e in evs() if e.get("gen_key") == "quest-unlock:quest-mapa")
    assert unlock["cause"] == rumor and unlock["status"] == "processado"
    h = Q()["quest-mapa"]["history"][-1]
    assert (h["de"], h["para"], h["causa"]) == ("oculta", "disponivel", rumor)
    out = run("quest", "history", "quest-mapa")
    assert "desbloqueada" in out and rumor in out and "Boato no mercado" in out

    # público após desbloqueio: texto público e objetivos marcados, sem gm/condições/recompensas
    pub = run("quest", "show", "quest-mapa", "--public")
    assert "O rei quer o mapa antigo." in pub and "[ ] Encontrar o mapa" in pub
    for leak in ("SEGREDO", "flag", "mapa_achado", "ouro", "rei_grato", "done_conditions"):
        assert leak not in pub, leak
    assert "quest-mapa" in run("quest", "list", "--public")

    # --- objetivos estruturados controlam a conclusão (o "- [x]" do texto não conta)
    out = run("quest", "set-status", "quest-mapa", "concluida", "--reason", "x", ok=False)
    assert "objetivos obrigatórios" in out and "achar" in out
    assert "Pode concluir agora: não" in run("quest", "check", "quest-mapa")
    before = (C / "state" / "events.jsonl").read_text()
    run("quest", "check", "quest-mapa")
    assert (C / "state" / "events.jsonl").read_text() == before  # check não altera nada

    # --- dependências bloqueiam a conclusão (mesmo com objetivo marcado manualmente)
    run("time", "schedule", "Ana abre o baú", "--in", "10min", "--effects",
        '{"type":"quest.objective","quest":"quest-tesouro","objective":"abrir","done":true,"reason":"narrado na cena"}')
    advance("10min")
    assert Q()["quest-tesouro"]["manual"]["abrir"]["done"] is True
    out = run("quest", "set-status", "quest-tesouro", "concluida", "--reason", "x", ok=False)
    assert "dependências" in out and "quest-mapa" in out

    # objetivo satisfeito → "pronta para concluir" (auto_complete=false não conclui sozinho)
    run("time", "schedule", "Ana acha o mapa", "--in", "30min", "--effects", '{"type":"flag.set","name":"mapa_achado","value":true}')
    achou = next(e["id"] for e in evs() if e.get("text") == "Ana acha o mapa")
    advance("1h")
    ready = next(e for e in evs() if e.get("gen_key") == "quest-ready:quest-mapa")
    assert ready["cause"] == achou and st("quest-mapa") == "disponivel"

    # --- recompensas/consequências uma única vez
    out = run("quest", "set-status", "quest-mapa", "concluida", "--reason", "entregou o mapa ao rei", "--op", "conclui-mapa")
    assert st("quest-mapa") == "concluida"
    assert sim.balance(C, "pc-ana", "ouro") == 50 and flags()["rei_grato"] is True
    assert "já aplicada" in run("quest", "set-status", "quest-mapa", "concluida", "--reason", "entregou o mapa ao rei", "--op", "conclui-mapa")
    assert "outros dados" in run("quest", "set-status", "quest-mapa", "concluida", "--reason", "x", "--op", "conclui-mapa", ok=False)
    w = sim.World(C)
    done = next(e for e in w.events() if e.get("gen_key") == "quest-op:conclui-mapa")
    for _ in range(2):  # reprocessamento forçado
        sim.process_event(w, dict(done, status="processando"))
    advance("1d")
    advance("1d")
    assert sim.balance(C, "pc-ana", "ouro") == 50
    assert sum(1 for h in Q()["quest-mapa"]["history"] if h["para"] == "concluida") == 1
    assert "entregou o mapa ao rei" in run("quest", "history", "quest-mapa")
    # concluída → em_andamento: fluxo recusa
    assert "não permitida" in run("quest", "set-status", "quest-mapa", "em_andamento", "--reason", "x", ok=False)
    # agora a dependência está concluída
    run("quest", "set-status", "quest-tesouro", "concluida", "--reason", "baú aberto")
    assert st("quest-tesouro") == "concluida"

    # --- prazos: vence no instante certo, não antes; consequências uma vez
    define("quest-escolta", {"deadline": "+2d", "consequences": {"expirada": [
        {"type": "flag.set", "name": "caravana_perdida", "value": True},
        {"type": "resource.produce", "who": "npc-rei", "res": "rancor", "qty": 1, "source": "quest-escolta"}]}})
    define("quest-filha", {"depends_on": ["quest-escolta"], "on_dependency_failed": "cancelada"})
    define("quest-entrega", {"deadline": "+1d", "flow": {"aceita": ["em_andamento"]}})
    dl = Q()["quest-escolta"]["deadline"]
    assert dl == now() + 2 * 24 * 60
    assert dl == Q()["quest-entrega"]["deadline"] + 24 * 60
    assert "quest-entrega" in run("quest", "deadlines").splitlines()[0]
    # fluxo configurado por missão: aceita → concluida não é permitido aqui
    assert "não permitida" in run("quest", "set-status", "quest-entrega", "concluida", "--reason", "x", ok=False)
    run("quest", "set-status", "quest-entrega", "em_andamento", "--reason", "partiu")
    run("quest", "set-status", "quest-entrega", "concluida", "--reason", "entregue a tempo")
    advance(f"{dl - now() - 1}min")  # até 1 minuto antes do prazo da escolta (passa o prazo da entrega)
    assert now() == dl - 1 and st("quest-escolta") == "ativa", (now(), dl, st("quest-escolta"))
    assert st("quest-entrega") == "concluida"  # concluída antes do prazo não expira
    advance("1min")
    assert st("quest-escolta") == "expirada"
    h = Q()["quest-escolta"]["history"][-1]
    assert h["at"] == dl and h["de"] == "ativa" and "prazo" in h["motivo"]
    assert flags()["caravana_perdida"] is True and sim.balance(C, "npc-rei", "rancor") == 1
    assert st("quest-filha") == "cancelada"  # dependência expirou → cancelada, causa = evento do prazo
    dlev = next(e for e in evs() if e.get("gen_key", "").startswith("quest-deadline:quest-escolta"))
    assert next(e for e in evs() if e.get("gen_key") == "quest-depfail:quest-filha")["cause"] == dlev["id"]
    advance("3d")
    assert sim.balance(C, "npc-rei", "rancor") == 1 and st("quest-escolta") == "expirada"
    assert not [e for e in evs() if e.get("gen_key", "").startswith("quest-deadline:quest-entrega") and e["status"] == "processado"]

    # --- auto_complete: conclui sozinha com causa = evento que satisfez o objetivo
    define("quest-auto", {"auto_complete": True, "objectives": [{"id": "x", "desc": "X", "done_conditions": [{"type": "flag", "name": "auto_ok"}]}]})
    run("time", "schedule", "Algo acontece", "--in", "1h", "--effects", '{"type":"flag.set","name":"auto_ok","value":true}')
    advance("1h")
    assert st("quest-auto") == "concluida"
    ae = next(e["id"] for e in evs() if e.get("text") == "Algo acontece")
    assert Q()["quest-auto"]["history"][-1]["causa"] == ae

    # --- CLI 'quest objective': valida, agenda evento com causa, idempotente por --op
    define("quest-b", {"objectives": [{"id": "falar", "desc": "Falar com o rei"}]})
    assert "não definido" in run("quest", "objective", "quest-b", "nada", "--done", "--reason", "x", ok=False)
    assert "encerrada" in run("quest", "objective", "quest-mapa", "achar", "--done", "--reason", "x", ok=False)
    run("quest", "objective", "quest-b", "falar", "--reason", "x", ok=False)  # exige --done ou --undone
    out = run("quest", "objective", "quest-b", "falar", "--done", "--reason", "conversou no salão", "--op", "obj1", "--cause", ae)
    m = Q()["quest-b"]["manual"]["falar"]
    oev = next(e for e in evs() if e.get("gen_key") == "quest-op:obj1")
    assert m["done"] is True and m["by"] == oev["id"] and oev["cause"] == ae and oev["status"] == "processado", (m, oev)
    assert "[x] falar" in run("quest", "check", "quest-b")
    n = len(evs())
    assert "já aplicada" in run("quest", "objective", "quest-b", "falar", "--done", "--reason", "conversou no salão", "--op", "obj1", "--cause", ae)
    assert "outros dados" in run("quest", "objective", "quest-b", "falar", "--done", "--reason", "x", "--op", "obj1", ok=False)
    assert len(evs()) == n and Q()["quest-b"]["manual"]["falar"]["by"] == oev["id"]
    run("quest", "objective", "quest-b", "falar", "--undone", "--reason", "o rei negou a conversa", "--op", "obj2")
    assert Q()["quest-b"]["manual"]["falar"]["done"] is False
    assert "[ ] falar" in run("quest", "check", "quest-b")
    assert "o rei negou a conversa" in run("quest", "history", "quest-b")

    # --- persistência (novo processo) e verificador
    assert "quest-mapa" in run("quest", "list") and "concluida" in run("quest", "show", "quest-mapa")
    out = run("world", "verify")
    assert "[missão]" not in out, out
    data = json.loads((C / "state" / "sim" / "quests.json").read_text())
    data["quests"]["quest-b"] = {"depends_on": ["quest-a"]}  # ciclo injetado à mão
    data["quests"]["quest-fantasma"] = {}
    sim.save_json(C / "state" / "sim" / "quests.json", data)
    sim.set_meta(C / "quests" / "quest-a.md", "status", "pausada")
    out = run("world", "verify")
    assert "ciclo de dependências" in out and "quest-fantasma" in out and "'pausada' desconhecido" in out, out
finally:
    shutil.rmtree(tmp)
print("OK: testes do motor de missões passaram")
