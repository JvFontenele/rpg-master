#!/usr/bin/env python3
"""Testes do núcleo da simulação (tools/sim.py + extensões de world.py), em raiz temporária."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
import sim  # noqa: E402

env = {**os.environ, "RPG_ROOT": str(tmp)}


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def evs(C):
    return {e["id"]: e for e in sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))}


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    run("new", "sim", "--name", "Sim", "--system", "aventura-2d6")
    C = tmp / "campaigns" / "sim"
    for nid, loc in (("npc-guarda", "loc-porto"), ("npc-capita", "loc-quartel"), ("npc-mercador", "loc-longe")):
        (C / "npcs" / f"{nid}.md").write_text(f"---\nid: {nid}\nname: {nid[4:].title()}\nstatus: vivo\nlocation: {loc}\n---\n# {nid}\n")
    for lid in ("loc-porto", "loc-quartel", "loc-longe"):
        (C / "locations" / f"{lid}.md").write_text(f"---\nid: {lid}\nname: {lid[4:].title()}\n---\n# {lid}\n\n## Conhecido pelos jogadores\n")
    (C / "characters" / "pc-ana.md").write_text("---\nid: pc-ana\nname: Ana\nlocation: loc-porto\n---\n# Ana\n")

    # campanha sem relógio: comandos antigos seguem funcionando; time exige init
    assert "não iniciado" in run("time", "now", ok=False)
    run("world", "event-add", "Algo antigo", "--date", "Era antiga")
    run("check")

    # calendário configurável
    cal = {"minutes_per_hour": 60, "hours_per_day": 24, "days_per_month": 28,
           "months": ["Gelo", "Degelo", "Flor", "Sol"], "year_label": "Era", "epoch_year": 300}
    (tmp / "cal.json").write_text(json.dumps(cal))
    out = run("time", "init", "--start", "300/1/1 08:00", "--calendar", str(tmp / "cal.json"), "--seed", "7")
    assert "Dia 1 de Gelo, Era 300, 08:00" in out
    run("time", "init", ok=False)  # não sobrescreve
    assert sim.parse_duration("1d6h30min", cal) == 24 * 60 + 6 * 60 + 30
    assert sim.fmt(sim.parse_when("300/2/3 14:05", cal, 0), cal) == "Dia 3 de Degelo, Era 300, 14:05"
    for bad in ("3x", "", "h6"):
        try:
            sim.parse_duration(bad, cal)
            raise AssertionError(bad)
        except sim.SimError:
            pass

    # agendamento + ordem determinística (mesmo horário: prioridade, depois id)
    run("time", "schedule", "B (prioridade 50)", "--in", "3h", "--effects", '{"type":"flag.set","name":"ordem","value":"B"}')
    run("time", "schedule", "A (prioridade 10)", "--in", "3h", "--priority", "10", "--effects", '{"type":"flag.set","name":"ordem","value":"A"}')
    run("time", "schedule", "Depois", "--in", "5h", "--effects", '{"type":"flag.set","name":"depois","value":true}')
    run("time", "schedule", "Futuro distante", "--in", "3d")
    run("time", "schedule", "efeito inválido", "--in", "1h", "--effects", '{"type":"nao.existe"}', ok=False)
    run("time", "schedule", "passado", "--at", "300/1/1 07:00", ok=False)
    out = run("time", "advance", "4h", "--op", "s1-viagem", "--reason", "viagem ao porto")
    assert out.index("A (prioridade 10)") < out.index("B (prioridade 50)"), out
    assert "Depois" not in out and "Futuro" not in out  # não executa prematuramente
    assert json.loads((C / "state" / "sim" / "flags.json").read_text())["ordem"] == "B"
    assert "12:00" in run("time", "now")
    # mesma operação não avança de novo
    assert "já foi aplicada" in run("time", "advance", "4h", "--op", "s1-viagem", "--reason", "x", ok=False)
    assert "12:00" in run("time", "now")
    out = run("time", "advance", "2h", "--op", "s1-espera", "--reason", "espera")
    assert "Depois" in out and "14:00" in run("time", "now")

    # idempotência: efeito de recurso não duplica ao reprocessar
    (C / "state" / "sim").mkdir(exist_ok=True)
    sim.save_json(sim.res_path(C), {"holdings": {"npc-mercador": {"ouro": 10}}, "allow_negative": []})
    run("time", "schedule", "pagamento", "--in", "1h", "--effects",
        '[{"type":"resource.transfer","from":"npc-mercador","to":"npc-guarda","res":"ouro","qty":4}]')
    run("time", "advance", "1h", "--op", "s1-pagar", "--reason", "pagar")
    w = sim.World(C)
    pay = next(e for e in w.events() if e.get("text") == "pagamento")
    for _ in range(2):  # forçar reprocessamento direto não altera saldo
        sim.process_event(w, dict(pay, status="processando"))
    assert sim.balance(C, "npc-mercador", "ouro") == 6 and sim.balance(C, "npc-guarda", "ouro") == 4
    # transações inválidas
    run("time", "schedule", "calote", "--in", "1h", "--effects",
        '[{"type":"resource.transfer","from":"npc-guarda","to":"npc-mercador","res":"ouro","qty":50}]')
    out = run("time", "advance", "1h", "--op", "s1-calote", "--reason", "x", ok=False)
    assert "saldo negativo" in out
    assert "avanço interrompido" in run("time", "advance", "1h", "--op", "outra", "--reason", "x", ok=False)
    assert "falhou" in run("time", "upcoming")
    assert sim.balance(C, "npc-guarda", "ouro") == 4  # nada mudou
    calote = next(e for e in evs(C).values() if e.get("text") == "calote")
    run("time", "cancel", calote["id"], "--reason", "erro do mestre")
    run("time", "resume")
    assert "já foi aplicada" in run("time", "advance", "1h", "--op", "s1-calote", "--reason", "x", ok=False)
    assert "processado" not in evs(C)[calote["id"]]["status"]
    ev_before = (C / "state" / "events.jsonl").read_text()
    assert "source" in run("time", "schedule", "do nada", "--in", "1h", "--effects",
                           '{"type":"resource.produce","who":"npc-guarda","res":"ouro","qty":5}', ok=False)  # 3.1.1: recusado antes
    assert (C / "state" / "events.jsonl").read_text() == ev_before  # nada gravado
    w0 = sim.World(C)  # o motor ainda trata definição inválida que chegue à execução como falha permanente
    w0.schedule({"type": "evento", "text": "do nada", "due": w0.now + 60,
                 "effects": [{"type": "resource.produce", "who": "npc-guarda", "res": "ouro", "qty": 5}]})
    out = run("time", "advance", "1h", "--op", "s1-nada", "--reason", "x")  # Fase 3.1.1: falha permanente não bloqueia
    assert "source" in out and "FALHA PERMANENTE" in out and not sim.load_clock(C).get("pending_op")
    nada = next(e for e in evs(C).values() if e.get("text") == "do nada")
    assert nada["status"] == "falhou" and nada["result"]["permanente"]
    run("time", "cancel", nada["id"], "--reason", "inválido")  # decisão do mestre registrada; nada a retomar

    # recuperação: falha no meio de um evento com 2 efeitos não duplica o 1º na retomada
    run("time", "schedule", "dois passos", "--in", "1h", "--effects",
        '[{"type":"resource.transfer","from":"npc-mercador","to":"npc-capita","res":"ouro","qty":1},'
        '{"type":"entity.set","id":"npc-capita","field":"location","value":"loc-inexistente"}]')
    run("time", "advance", "1h", "--op", "s1-falha", "--reason", "x", ok=False)
    assert sim.balance(C, "npc-capita", "ouro") == 1
    (C / "locations" / "loc-inexistente.md").write_text("---\nid: loc-inexistente\nname: Agora existe\n---\n# x\n")
    run("time", "resume")
    assert sim.balance(C, "npc-capita", "ouro") == 1 and sim.balance(C, "npc-mercador", "ouro") == 5
    assert "location: loc-inexistente" in (C / "npcs" / "npc-capita.md").read_text()

    # condições: cancela ou adia; nunca aplica sem condição
    run("time", "schedule", "só se flag", "--in", "1h", "--conditions", '{"type":"flag","name":"alarme"}',
        "--effects", '{"type":"flag.set","name":"reagiu","value":true}')
    run("time", "schedule", "espera flag", "--in", "1h", "--on-unmet", "adiar", "--conditions", '{"type":"flag","name":"alarme"}',
        "--effects", '{"type":"flag.set","name":"adiado_ok","value":true}')
    run("time", "advance", "1h", "--op", "s1-cond", "--reason", "x")
    F = json.loads((C / "state" / "sim" / "flags.json").read_text())
    assert "reagiu" not in F and "adiado_ok" not in F
    run("time", "schedule", "alarme", "--in", "1h", "--effects", '{"type":"flag.set","name":"alarme","value":true}')
    run("time", "advance", "2h", "--op", "s1-alarme", "--reason", "x")
    assert json.loads((C / "state" / "sim" / "flags.json").read_text()).get("adiado_ok") is True

    # conhecimento limitado: testemunha × capitã × mercador distante; boato falso
    run("fact", "add", "fact-roubo", "Ana roubou os documentos da guilda", "--about", "pc-ana")
    run("fact", "add", "fact-boato", "O capitão aceita suborno", "--false")
    run("time", "schedule", "roubo no porto", "--in", "10min", "--entities", "pc-ana", "--loc", "loc-porto", "--effects",
        '{"type":"knowledge.witness","fact":"fact-roubo","loc":"loc-porto","except":["pc-ana"]}')
    run("time", "advance", "10min", "--op", "s1-roubo", "--reason", "x")
    roubo = next(e["id"] for e in evs(C).values() if e.get("text") == "roubo no porto")
    assert "fact-roubo: sabe" in run("fact", "knows", "npc-guarda")
    assert "fact-roubo" not in run("fact", "knows", "npc-capita")
    assert "fact-roubo" not in run("fact", "knows", "npc-mercador")
    run("time", "schedule", "capitã sabe?", "--in", "1h", "--conditions", '{"type":"knows","who":"npc-capita","fact":"fact-roubo"}',
        "--effects", '{"type":"flag.set","name":"investiga","value":true}')
    run("time", "schedule", "relato", "--in", "30min", "--cause", roubo, "--effects",
        '{"type":"knowledge.tell","from":"npc-guarda","to":"npc-capita","fact":"fact-roubo"}')
    run("time", "schedule", "mercador conta (não sabe)", "--in", "30min", "--effects",
        '{"type":"knowledge.tell","from":"npc-mercador","to":"npc-capita","fact":"fact-roubo"}')
    out = run("time", "advance", "2h", "--op", "s1-relato", "--reason", "x", ok=False)
    assert "não conhece" in out  # mercador não pode contar o que não sabe
    run("time", "cancel", next(e["id"] for e in evs(C).values() if e.get("text") == "mercador conta (não sabe)"), "--reason", "impossível")
    run("time", "resume")
    assert "fact-roubo: acredita" in run("fact", "knows", "npc-capita")
    assert json.loads((C / "state" / "sim" / "flags.json").read_text()).get("investiga") is True
    run("fact", "learn", "npc-mercador", "fact-boato", "--status", "sabe", "--source", "taverna")
    assert "fact-boato: acredita" in run("fact", "knows", "npc-mercador") and "FALSO" in run("fact", "knows", "npc-mercador")

    # histórico causal e consultas
    relato = next(e["id"] for e in evs(C).values() if e.get("text") == "relato")
    out = run("world", "chain", relato)
    assert out.index(roubo) < out.index(relato)
    assert relato in run("world", "chain", roubo)
    assert "pagamento" in run("world", "why", "npc-guarda")
    assert "relato" in run("world", "timeline", "--cause", roubo)
    assert "Algo antigo" in run("world", "timeline", "--session", "1") or "Algo antigo" in run("world", "timeline")
    pub = run("world", "timeline", "--public")
    assert "Futuro distante" not in pub and "calote" not in pub and "Tempo avançou" not in pub
    run("time", "schedule", "segredo do mestre", "--in", "1h", "--hidden")
    run("time", "advance", "1h", "--op", "s1-seg", "--reason", "x")
    assert "segredo do mestre" not in run("world", "timeline", "--public")
    run("world", "event-add", "Ana confessa", "--entities", "pc-ana", "--cause", roubo)
    assert "Ana confessa" in run("world", "chain", roubo)
    assert "Dia 1 de Gelo" in run("world", "timeline", "--entity", "pc-ana")

    # persistência após "reinício" (novo processo) e verificador
    assert "Era 300" in run("time", "now")
    out = run("world", "verify")
    assert "OK" in out, out

    # backup e restauração preservam relógio, eventos, conhecimento e recursos (cópia isolada)
    run("backup", "sim")
    arc = sorted((tmp / "backups").glob("sim-*.tar.gz"))[-1]
    run("restore", str(arc), "--as", "sim-copia")
    CC = tmp / "campaigns" / "sim-copia"
    for f in ("state/clock.json", "state/events.jsonl", "state/sim/facts.json", "state/sim/resources.json", "state/sim/flags.json"):
        assert (CC / f).read_bytes() == (C / f).read_bytes(), f
    assert "Era 300" in run("time", "now", "--campaign", "sim-copia")
    assert "OK" in run("world", "verify", "--campaign", "sim-copia")
    assert relato in run("world", "chain", roubo, "--campaign", "sim-copia")
    run("time", "advance", "1h", "--op", "copia-1", "--reason", "x", "--campaign", "sim-copia")
    assert "Era 300" in run("time", "now") and (C / "state" / "clock.json").read_bytes() != (CC / "state" / "clock.json").read_bytes()

    # arquivo corrompido: erro claro, nada alterado
    (C / "state" / "sim" / "facts.json").write_text("{corrompido")
    assert "corrompido" in run("fact", "knows", "npc-guarda", ok=False)
    assert "corrompido" in run("world", "verify")
finally:
    shutil.rmtree(tmp)
print("OK: testes do núcleo da simulação passaram")
