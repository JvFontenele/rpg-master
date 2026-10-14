#!/usr/bin/env python3
"""Integração da Fase 3: cadeia causal completa entre módulos, privacidade, mapas, economia desligada,
backup/restauração e preservação das campanhas reais e da configuração global. Raiz temporária."""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
import sim  # noqa: E402


def h(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def tree(d):
    return {str(p.relative_to(d)): h(p) for p in sorted(Path(d).rglob("*")) if p.is_file()} if Path(d).exists() else {}


GLOBAL = [Path.home() / ".claude" / f for f in ("settings.json", "settings.local.json", "CLAUDE.md")]
before_global = {str(p): h(p) for p in GLOBAL}
before_real = {c: tree(PROJ / "campaigns" / c) for c in ("teste-validacao", "teste-mapas")}
before_backups = tree(PROJ / "backups")

tmp = Path(tempfile.mkdtemp())
env = {k: v for k, v in os.environ.items() if k != "RPG_SIM_MODULES"}  # todos os módulos ligados
env["RPG_ROOT"] = str(tmp)


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def evs():
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def find(pred):
    return next(e for e in evs() if pred(e))


def md(path, **meta):
    head = "\n".join(f"{k}: {v}" for k, v in meta.items())
    path.write_text(f"---\n{head}\n---\n# {meta['name']}\n\n## Conhecido pelos jogadores\n\n## Agenda\n")


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    run("new", "viva", "--name", "Viva", "--system", "aventura-2d6")
    C = tmp / "campaigns" / "viva"
    (C / "world.md").write_text((C / "world.md").read_text() + "\n- fac-guilda: Guilda dos Escribas\n- fac-ladroes: Mãos Leves\n")
    for lid in ("loc-porto", "loc-quartel", "loc-taverna", "loc-longe"):
        md(C / "locations" / f"{lid}.md", id=lid, name=lid[4:].title())
    md(C / "npcs" / "npc-guarda.md", id="npc-guarda", name="Guarda Rui", status="vivo", location="loc-porto", faction="fac-guilda")
    md(C / "npcs" / "npc-capitao.md", id="npc-capitao", name="Capitão Brás", status="vivo", location="loc-quartel", faction="fac-guilda")
    md(C / "npcs" / "npc-contato.md", id="npc-contato", name="Lia", status="viva", location="loc-taverna", faction="fac-ladroes")
    md(C / "npcs" / "npc-mercador.md", id="npc-mercador", name="Mercador Távora", status="vivo", location="loc-longe")
    md(C / "characters" / "pc-ana.md", id="pc-ana", name="Ana", location="loc-porto")
    (C / "quests" / "quest-documentos.md").write_text(
        "---\nid: quest-documentos\nname: Os Documentos\nstatus: oculta\ntype: principal\ngiver: npc-contato\nlocation: loc-taverna\n---\n"
        "# Os Documentos\n\n## Conhecido pelos jogadores (objetivo como os PJs o entendem)\n")
    run("check")

    # mapa preexistente (Fase 2): deve continuar válido, sem regeneração nem revelação acidental
    run("map", "gen", "cidade", "map-porto", "--name", "Porto", "--seed", "5")
    run("map", "place", "map-porto", "--name", "Cais do Porto", "--kind", "porto", "--ref", "loc-porto", "--at", "10,10")
    map_before = h(C / "maps" / "map-porto.json")

    run("time", "init", "--start", "1/1/1 08:00", "--seed", "11")
    run("fact", "add", "fact-roubo", "Ana roubou os documentos secretos da guilda", "--about", "pc-ana,fac-guilda")
    run("time", "schedule", "Inventário da guilda", "--effects",
        '{"type":"resource.produce","who":"fac-guilda","res":"documentos","qty":1,"source":"arquivo da guilda"}')
    run("time", "process")

    # NPC guarda: só relata se souber do roubo
    goals = [{"id": "dever", "desc": "relatar crimes", "priority": 5, "actions": [
        {"id": "relatar", "desc": "relata o roubo ao capitão", "duration": "30min", "utility": 2, "risk": 0,
         "preconditions": [{"type": "knows", "who": "npc-guarda", "fact": "fact-roubo"}],
         "effects": [{"type": "knowledge.tell", "from": "npc-guarda", "to": "npc-capitao", "fact": "fact-roubo"}]}]}]
    run("npc", "set", "npc-guarda", "--tier", "2", "--goals", json.dumps(goals))
    assert "sem ação" in run("npc", "decide", "npc-guarda").lower()  # ainda não sabe de nada

    # facção: investigação em etapas, disparada por conhecimento do capitão
    plan = {"leader": "npc-capitao", "plans": [{"id": "investigar", "goal": "recuperar documentos", "status": "ativo", "steps": [
        {"id": "abrir", "desc": "abrir investigação", "duration": "2h",
         "start_conditions": [{"type": "knows", "who": "npc-capitao", "fact": "fact-roubo"}],
         "done_conditions": [], "effects": [{"type": "flag.set", "name": "investigacao", "value": True}]},
        {"id": "interrogar", "desc": "interrogar o contato dos ladrões", "duration": "1d", "start_conditions": [],
         "done_conditions": [], "effects": [
             {"type": "knowledge.learn", "who": "npc-capitao", "fact": "fact-roubo", "status": "sabe", "source": "interrogatório de npc-contato"},
             {"type": "relation.add", "a": "npc-capitao", "b": "npc-contato", "rel": "interrogou", "secret": True},
             {"type": "faction.stance", "a": "fac-guilda", "b": "fac-ladroes", "stance": "hostil", "score_delta": -40,
              "reason": "contato dos ladrões ligado ao roubo"},
             {"type": "faction.reputation", "faction": "fac-guilda", "who": "pc-ana", "delta": -30}]}]}]}
    run("faction", "set", "fac-guilda", "--json", json.dumps(plan))

    # missão: desbloqueia quando a investigação abre; prazo de 3 dias com consequência
    qdef = {"objectives": [{"id": "devolver", "desc": "devolver os documentos",
                            "done_conditions": [{"type": "resource.gte", "who": "fac-ladroes", "res": "documentos", "qty": 1}]}],
            "unlock_conditions": [{"type": "flag", "name": "investigacao"}], "unlock_to": "disponivel", "deadline": "+3d",
            "consequences": {"expirada": [{"type": "flag.set", "name": "lia_presa", "value": True}]},
            "public": "Lia pede ajuda para sumir com os documentos.", "gm": "SEGREDO-GM-LIA-DELATORA"}
    run("quest", "define", "quest-documentos", "--json", json.dumps(qdef))

    # 1. o roubo (só quem está no porto testemunha)
    run("time", "schedule", "Ana rouba os documentos no porto", "--in", "10min", "--entities", "pc-ana", "--loc", "loc-porto",
        "--hidden", "--effects", json.dumps([
            {"type": "knowledge.witness", "fact": "fact-roubo", "loc": "loc-porto", "except": ["pc-ana"]},
            {"type": "resource.transfer", "from": "fac-guilda", "to": "pc-ana", "res": "documentos", "qty": 1}]))
    run("time", "advance", "10min", "--op", "s1-roubo", "--reason", "o roubo")
    roubo = find(lambda e: e.get("text") == "Ana rouba os documentos no porto")["id"]
    assert "fact-roubo" in run("fact", "knows", "npc-guarda")
    for who in ("npc-capitao", "npc-mercador", "npc-contato"):
        assert "fact-roubo" not in run("fact", "knows", who), who  # sem onisciência

    # 2-3. o guarda decide relatar (causa = roubo); 4. capitão passa a acreditar; facção abre investigação
    run("npc", "decide", "npc-guarda", "--commit", "--op", "s1-guarda", "--cause", roubo)
    run("time", "advance", "6h", "--op", "s1-relato", "--reason", "tarde passa")
    assert "fact-roubo: acredita" in run("fact", "knows", "npc-capitao")
    assert "fact-roubo" not in run("fact", "knows", "npc-mercador")
    assert json.loads((C / "state" / "sim" / "flags.json").read_text()).get("investigacao") is True
    assert "status: disponivel" in (C / "quests" / "quest-documentos.md").read_text()
    # 5-6. interrogatório e piora da postura entre facções
    run("time", "advance", "1d6h", "--op", "s1-dia2", "--reason", "um dia")
    F = json.loads((C / "state" / "sim" / "factions.json").read_text())["factions"]
    assert F["fac-guilda"]["stance"]["fac-ladroes"]["stance"] == "hostil"
    assert F["fac-guilda"]["reputation"]["pc-ana"] == -30
    assert "interrogou" in run("world", "rel", "npc-contato")

    # cadeia causal: do evento que mudou a postura até o roubo
    stance_ev = find(lambda e: any(f.get("type") == "faction.step.done" and f.get("step") == "interrogar"
                                   for f in e.get("effects", [])) and e["status"] == "processado")
    chain = run("world", "chain", stance_ev["id"])
    assert roubo in chain, chain
    assert "relata o roubo" in chain or "relatar" in chain or "npc.acao" in chain, chain
    assert roubo in run("world", "why", "fac-guilda") or "hostil" in run("world", "why", "fac-guilda")

    # missão: prazo vence sem os jogadores; consequência aplicada uma vez
    run("time", "advance", "2d", "--op", "s1-prazo", "--reason", "dias passam")
    assert "status: expirada" in (C / "quests" / "quest-documentos.md").read_text()
    assert json.loads((C / "state" / "sim" / "flags.json").read_text()).get("lia_presa") is True
    n_exp = sum(1 for e in evs() if "expirada" in json.dumps(e.get("result") or {}, ensure_ascii=False))
    run("time", "process")
    run("time", "advance", "1d", "--op", "s1-depois", "--reason", "mais um dia")
    assert sum(1 for e in evs() if "expirada" in json.dumps(e.get("result") or {}, ensure_ascii=False)) == n_exp

    # idempotência global: mesma operação não avança de novo
    now = run("time", "now")
    run("time", "advance", "1d", "--op", "s1-depois", "--reason", "repetido", ok=False)
    assert run("time", "now") == now

    # privacidade: saídas públicas sem segredos
    run("time", "schedule", "Emboscada planejada pelos ladrões", "--in", "5d", "--hidden")
    pub = (run("world", "timeline", "--public") + run("quest", "list", "--public") + run("world", "rel", "npc-contato", "--public")
           + run("time", "now", "--public"))
    assert "Emboscada planejada" in run("time", "now") + run("time", "upcoming") and "Emboscada planejada" not in pub
    for s in ("documentos secretos", "SEGREDO-GM-LIA-DELATORA", "interrogou", "Ana rouba", "npc.decisao", "relata o roubo"):
        assert s not in pub, f"vazou: {s}"
    qpub = run("quest", "show", "quest-documentos", "--public")
    assert "Lia pede ajuda" in qpub and "SEGREDO-GM" not in qpub and "resource.gte" not in qpub and "lia_presa" not in qpub

    # mapas: não regenerados nem revelados pela simulação; visão do jogador sem segredos
    assert h(C / "maps" / "map-porto.json") == map_before
    assert "OK" in run("map", "validate")
    player = "".join(f.read_text() for f in (C / "maps" / "jogador").glob("*")) if (C / "maps" / "jogador").exists() else ""
    for s in ("documentos secretos", "Ana rouba", "SEGREDO-GM"):
        assert s not in player

    # economia desligada: nada criado, comandos avisam
    assert "deslig" in run("econ", "status").lower()
    assert not (C / "state" / "sim" / "economy.json").exists()

    # combate: regras do sistema da campanha, idempotente, persistente, registrado na linha do tempo
    out = run("combat", "start", "--party", "pc-ana", "--enemies", "capanga x2", "--enemy-agi", "0",
              "--set", "pc-ana:pv=11,pvmax=11,vig=1,agi=2,def=10,det=3", "--seed", "3", "--op", "c1")
    cbt = next(f.stem for f in (C / "state" / "sim" / "combat").glob("cbt-*.json"))
    run("combat", "start", "--party", "pc-ana", "--enemies", "capanga x2", "--enemy-agi", "0",
        "--set", "pc-ana:pv=11,pvmax=11,vig=1,agi=2,def=10,det=3", "--seed", "3", "--op", "c1")
    assert len(list((C / "state" / "sim" / "combat").glob("cbt-*.json"))) == 1  # mesma op não duplica
    status = run("combat", "status", cbt)
    assert "Ana" in status or "pc-ana" in status
    run("combat", "act", cbt, "--actor", "pc-ana", "--action", "attack", "--target", "x", "--action-id", "a0", ok=False)
    run("combat", "act", cbt, "--actor", "pc-ana", "--action", "attack", "--target", "x", "--cover", "--action-id", "a0", ok=False)
    run("combat", "end", cbt, "--outcome", "fuga", "--reason", "Ana foge pelo cais")
    assert sum(1 for e in evs() if e.get("type") == "combate") == 1
    assert "combate" in run("world", "timeline", "--type", "combate")

    # verificador sem falsos positivos
    out = run("world", "verify")
    assert "OK" in out, out

    # backup + restauração em cópia isolada preservam o estado completo e as referências
    run("backup", "viva")
    arc = sorted((tmp / "backups").glob("viva-*.tar.gz"))[-1]
    run("restore", str(arc), "--as", "viva-copia")
    CC = tmp / "campaigns" / "viva-copia"
    for f in ("state/clock.json", "state/events.jsonl", "state/relations.jsonl", "state/sim/facts.json",
              "state/sim/factions.json", "state/sim/npcs.json", "state/sim/quests.json", "state/sim/resources.json",
              f"state/sim/combat/{cbt}.json"):
        assert (CC / f).read_bytes() == (C / f).read_bytes(), f
    assert roubo in run("world", "chain", stance_ev["id"], "--campaign", "viva-copia")
    assert "OK" in run("world", "verify", "--campaign", "viva-copia")
    assert "OK" in run("map", "validate", "--campaign", "viva-copia")

    # campanha antiga (sem relógio/sim) segue funcionando com todos os módulos ligados
    run("new", "antiga", "--name", "Antiga", "--system", "aventura-2d6")
    for cmd in (("check",), ("status",), ("world", "verify"), ("world", "timeline"), ("map", "list"), ("quest", "list"),
                ("econ", "status")):
        run(*cmd, "--campaign", "antiga") if cmd != ("status",) else run(*cmd)
    assert "não iniciado" in run("time", "now", "--campaign", "antiga", ok=False)
finally:
    shutil.rmtree(tmp)

assert before_global == {str(p): h(p) for p in GLOBAL}, "configuração global mudou"
assert before_real == {c: tree(PROJ / "campaigns" / c) for c in before_real}, "campanha real alterada!"
assert before_backups == tree(PROJ / "backups"), "backups reais alterados!"
print("OK: integração da Fase 3 passou")
