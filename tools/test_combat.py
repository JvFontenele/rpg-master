#!/usr/bin/env python3
"""Testes do motor de combate (tools/combat_engine.py + rulesets/), em raiz temporária."""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
os.environ["RPG_SIM_MODULES"] = "combat_engine"
from rulesets import aventura_2d6 as R  # noqa: E402

env = {**os.environ}


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def cb(camp, *args, ok=True):
    return run("combat", *args, "--campaign", camp, ok=ok)


def state(C, cid="cbt-0001"):
    return json.loads((C / "state" / "sim" / "combat" / f"{cid}.json").read_text())


def part(C, pid, cid="cbt-0001"):
    return next(p for p in state(C, cid)["participants"] if p["id"] == pid)


def rolls(C):
    f = C / "state" / "rolls.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def turn_of(out):
    return re.findall(r"\] rodada \d+ — vez de (\S+)", out)[-1]


def until_turn(camp, cid, pid, out=""):
    for _ in range(12):
        if out and turn_of(out) == pid:
            return out
        out = cb(camp, "next", cid)
    raise AssertionError(f"{pid} nunca chegou na vez:\n{out}")


SHEET = """---
id: pc-ana
name: Ana
system: aventura-2d6
---
# Ana

## Atributos
- **Vigor** +1
- **Agilidade** +2
- **Mente** 0
- **Presença** +1

## Perícias
- Armas Brancas (treinado)
- Tiro +2

## Recursos (PV, energia, etc. — atual / máximo)
PV 11/11 · Determinação 3

## Defesa
Defesa 10 (7 + Agilidade 2 + armadura leve 1)
"""

try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "systems" / "outro").mkdir()
    (tmp / "systems" / "outro" / "system.md").write_text("---\nid: outro\nname: Outro\n---\n# Outro\n")
    (tmp / "campaigns").mkdir()

    # ---- leitura de ficha e desempate (unidade)
    st, miss = R.read_sheet(SHEET)
    assert st["attrs"] == {"vig": 1, "agi": 2, "men": 0, "pre": 1} and (st["pv"], st["pv_max"], st["defense"], st["determination"]) == (11, 11, 10, 3), st
    assert st["skills"] == {"armas brancas": 1, "tiro": 2} and not miss, (st, miss)
    _, miss = R.read_sheet("## Atributos\nVigor +1\nVigor +2\n")  # ambíguo → não chuta
    assert "vig" in miss and "pv" in miss
    assert R.init_order([{"id": "capanga-1", "ref": None}, {"id": "pc-ana", "ref": "pc-ana"}], {"capanga-1": 9, "pc-ana": 9}) == ["pc-ana", "capanga-1"]

    # ---- sistema sem adaptador → erro claro
    run("new", "semad", "--name", "Sem", "--system", "outro")
    (tmp / "campaigns" / "semad" / "characters" / "pc-ana.md").write_text(SHEET)
    out = cb("semad", "start", "--party", "pc-ana", "--enemies", "capanga", ok=False)
    assert "não tem adaptador" in out and "rulesets/__init__.py" in out, out

    # ================= c1: pc_rolls mestre, SEM relógio
    for cid in ("c1", "c1b"):
        run("new", cid, "--name", cid, "--system", "aventura-2d6")
        C = tmp / "campaigns" / cid
        (C / "characters" / "pc-ana.md").write_text(SHEET)
        (C / "characters" / "pc-bruno.md").write_text("---\nid: pc-bruno\nname: Bruno\n---\n# Bruno\nForte e calado.\n")
        (C / "npcs" / "npc-guarda.md").write_text("---\nid: npc-guarda\nname: Guarda\n---\n# Guarda\n")
    C1 = tmp / "campaigns" / "c1"
    base = ["start", "--party", "pc-ana,pc-bruno", "--npcs", "npc-guarda:soldado", "--enemies", "capanga x2", "--seed", "5"]
    out = cb("c1", *base, "--enemy-agi", "1", ok=False)
    assert "pc-bruno" in out and "--set pc-bruno" in out, out  # ficha ilegível → exige --set
    out = cb("c1", *base, "--set", "pc-bruno:pv=10,vig=0,agi=1,def=8,det=3", ok=False)
    assert "regra não definida pelo sistema" in out and "Agilidade" in out, out  # inimigo sem Agilidade
    full = base + ["--set", "pc-bruno:pv=10,vig=0,agi=1,def=8,det=3", "--enemy-agi", "1"]
    out1 = cb("c1", *full)
    cb("c1b", *full)
    s1, s1b = state(C1), state(tmp / "campaigns" / "c1b")
    tot = {r["reason"].split("de ")[-1]: r["total"] for r in s1["initiative"]}
    assert s1["order"] == s1b["order"] and [r["total"] for r in s1["initiative"]] == [r["total"] for r in s1b["initiative"]]
    for x, y in zip(s1["order"], s1["order"][1:]):  # decrescente; empate → PJ antes
        assert tot[x] > tot[y] or (tot[x] == tot[y] and (x.startswith("pc-") or not y.startswith("pc-"))), (s1["order"], tot)
    for r in s1["initiative"]:  # iniciativa = 2d6 + Agilidade, conferida pelos dados registrados
        dice = sum(int(v) for v in re.search(r"\[(.*?)\]", r["detail"]).group(1).split(","))
        who = r["reason"].split("de ")[-1]
        assert r["total"] == dice + {"pc-ana": 2, "pc-bruno": 1}.get(who, 1), r
    assert len([r for r in rolls(C1) if "iniciativa" in r["reason"]]) == 5
    assert set(rolls(C1)[0]) == {"t", "expr", "detail", "total", "vs", "reason", "hidden"}

    out = until_turn("c1", "cbt-0001", "pc-ana", out1)
    out = cb("c1", "act", "cbt-0001", "--actor", "capanga-1", "--action", "attack", "--target", "pc-ana", ok=False)
    assert "não é o turno" in out
    out = cb("c1", "act", "cbt-0001", "--actor", "pc-ana", "--action", "attack", "--target", "capanga-1", "--weapon", "leve", "--roll", "9", ok=False)
    assert "só vale para PJ com pc_rolls: jogador" in out
    out = cb("c1", "act", "cbt-0001", "--actor", "pc-ana", "--action", "attack", "--target", "capanga-1", "--weapon", "leve", "--cover", ok=False)
    assert "regra não definida pelo sistema" in out and "cobertura" in out and "--mod" in out
    n0 = len(rolls(C1))
    out = cb("c1", "act", "cbt-0001", "--actor", "pc-ana", "--action", "attack", "--target", "capanga-1", "--weapon", "leve",
             "--skill", "Armas Brancas", "--action-id", "c1-a1")
    rs = rolls(C1)[n0:]
    atk = rs[0]
    assert atk["expr"] == "2d6+2" and atk["vs"] == 8 and "c1-a1" in atk["reason"]  # Vigor 1 + Armas Brancas 1 vs Defesa 8
    d = [int(v) for v in re.search(r"\[(.*?)\]", atk["detail"]).group(1).split(",")]
    assert atk["total"] == sum(d) + 2
    margin = atk["total"] - 8
    a1 = next(x for x in state(C1)["log"] if x["action_id"] == "c1-a1")
    if margin >= 0:
        dmg = rs[1]
        assert dmg["expr"] == ("1d6+1d6" if d == [6, 6] else "1d6") and part(C1, "capanga-1")["pv"] == max(0, 4 - dmg["total"])
    elif margin >= -2:
        assert a1["status"] == "pendente" and part(C1, "capanga-1")["pv"] == 4 and len(rs) == 1
        cb("c1", "act", "cbt-0001", "--actor", "pc-ana", "--action", "attack", "--action-id", "c1-a1", "--choice", "falha")
    else:
        assert a1["result"]["final"] == "falha" and part(C1, "capanga-1")["pv"] == 4 and len(rs) == 1

    # atordoado (§6): sem ação no próximo turno; some ao fim dele
    cb("c1", "act", "cbt-0001", "--action", "condition", "--target", "pc-bruno", "--add", "atordoado")
    out = until_turn("c1", "cbt-0001", "pc-bruno")
    assert "atordoado" in cb("c1", "act", "cbt-0001", "--actor", "pc-bruno", "--action", "attack", "--target", "capanga-2", "--weapon", "leve", ok=False)
    cb("c1", "act", "cbt-0001", "--actor", "pc-bruno", "--action", "move")  # movimento continua permitido
    cb("c1", "next", "cbt-0001")
    assert not any(c["name"] == "atordoado" for c in part(C1, "pc-bruno")["conditions"])

    # Determinação antes = vantagem (3d6kh2); --cover aceito com --mod do mestre, registrado
    until_turn("c1", "cbt-0001", "pc-ana")
    n0 = len(rolls(C1))
    cb("c1", "act", "cbt-0001", "--actor", "pc-ana", "--action", "attack", "--target", "npc-guarda", "--weapon", "leve", "--skill", "armas brancas",
       "--determination", "antes", "--cover", "--mod", "-1", "--mod-reason", "meia cobertura", "--action-id", "c1-a2")
    assert rolls(C1)[n0]["expr"] == "3d6kh2+1" and part(C1, "pc-ana")["determination"] == 2
    if next(x for x in state(C1)["log"] if x["action_id"] == "c1-a2")["status"] == "pendente":
        cb("c1", "act", "cbt-0001", "--actor", "pc-ana", "--action", "attack", "--action-id", "c1-a2", "--choice", "falha")
    log = cb("c1", "log", "cbt-0001")
    assert "decisão do mestre: meia cobertura (mod -1)" in log and "Agilidade 1 para inimigos" in log

    # verificador: PV acima do máximo, ação duplicada, participante inexistente
    sp = C1 / "state" / "sim" / "combat" / "cbt-0001.json"
    orig = sp.read_text()
    bad = json.loads(orig)
    bad["participants"][0]["pv"] = 99
    bad["log"].append(dict(bad["log"][0]))
    bad["participants"][1]["ref"] = "npc-fantasma"
    sp.write_text(json.dumps(bad))
    out = run("world", "verify", "--campaign", "c1")
    assert "acima do máximo" in out and "ação duplicada" in out and "npc-fantasma" in out, out
    sp.write_text(orig)
    assert "[combate]" not in run("world", "verify", "--campaign", "c1")

    # retomada em novo processo; fim sem relógio (sem evento na linha do tempo)
    assert "cbt-0001 [ativo]" in cb("c1", "resume")
    assert "Retomado" in cb("c1", "resume", "cbt-0001")
    out = cb("c1", "end", "cbt-0001", "--outcome", "fuga", "--reason", "recuaram")
    assert "relógio do mundo não iniciado" in out and "pc-ana: " in out and "Determinação 3→2" in out
    assert not (C1 / "state" / "events.jsonl").exists() or "combate" not in (C1 / "state" / "events.jsonl").read_text()
    assert state(C1)["result"]["summary"]["pc-ana"]["determinacao"] == 2

    # ================= c2: pc_rolls jogador, COM relógio, mapa tático
    run("new", "c2", "--name", "c2", "--system", "aventura-2d6")
    C2 = tmp / "campaigns" / "c2"
    (C2 / "campaign.yaml").write_text((C2 / "campaign.yaml").read_text().replace("pc_rolls: mestre", "pc_rolls: jogador"))
    (C2 / "characters" / "pc-ana.md").write_text(SHEET)
    run("time", "init", "--seed", "9", "--campaign", "c2")
    run("map", "gen", "batalha", "map-briga", "--name", "Briga", "--seed", "3", "--env", "campo", "--party", "pc-ana",
        "--enemies", "Capanga x2, Chefe", "--campaign", "c2")
    mp = C2 / "maps" / "map-briga.json"
    msha = hashlib.sha256(mp.read_bytes()).hexdigest()
    st2 = ["start", "--party", "pc-ana", "--enemies", "capanga x2, chefe", "--enemy-agi", "0", "--map", "map-briga", "--seed", "1", "--op", "s1-briga"]
    assert "pc_rolls: jogador" in cb("c2", *st2, ok=False)  # PJ sem --init
    out = cb("c2", *st2, "--init", "pc-ana=10")
    assert turn_of(out) == "pc-ana"  # 10+2=12: nenhum inimigo passa; empate → PJ primeiro
    assert "já criou cbt-0001" in cb("c2", *st2, "--init", "pc-ana=10")  # --op não cria duas vezes
    assert not (C2 / "state" / "sim" / "combat" / "cbt-0002.json").exists()
    assert all(p["pos"] for p in state(C2)["participants"])  # posições lidas dos tokens

    A = ["act", "cbt-0001", "--actor", "pc-ana", "--action", "attack"]
    assert "pc_rolls: jogador" in cb("c2", *A, "--target", "capanga-1", "--weapon", "media", ok=False)
    # falha por 1: 5 + Vigor 1 + Armas Brancas 1 = 7 vs 8 → escolha pendente, sem dano
    out = cb("c2", *A, "--target", "capanga-1", "--weapon", "media", "--skill", "armas brancas", "--roll", "5", "--action-id", "a1")
    assert "PENDENTE" in out and "--choice custo" in out and part(C2, "capanga-1")["pv"] == 4
    assert "pendente" in cb("c2", "next", "cbt-0001", ok=False)
    assert "já registrada" in cb("c2", *A, "--action-id", "a1")
    out = cb("c2", *A, "--action-id", "a1", "--choice", "custo")
    assert "--damage-roll" in out and part(C2, "capanga-1")["pv"] == 4
    out = cb("c2", *A, "--action-id", "a1", "--damage-roll", "3")
    c1p = part(C2, "capanga-1")
    assert c1p["pv"] == 0 and {"caido", "ferido-grave"} <= {c["name"] for c in c1p["conditions"]}, c1p  # 3+1 = 4 de dano
    assert "contra-ataque" in out and c1p["reaction"]["against"] == "pc-ana"
    assert rolls(C2)[-1]["expr"] == "1d6+1" and rolls(C2)[-1]["total"] == 4 and "jogador" in rolls(C2)[-1]["reason"]
    # mesmo action-id de novo: nada reaplicado, nada duplicado
    nlog, nroll = len(state(C2)["log"]), len(rolls(C2))
    assert "já registrada" in cb("c2", *A, "--action-id", "a1", "--choice", "custo")
    assert len(state(C2)["log"]) == nlog and len(rolls(C2)) == nroll and part(C2, "capanga-1")["pv"] == 0
    assert len({r["reason"] for r in rolls(C2)}) == len(rolls(C2))
    assert "--roll" in cb("c2", "act", "cbt-0001", "--actor", "chefe", "--action", "test", "--attr", "vig", "--vs", "8", "--roll", "7", ok=False)

    # rodada 2: crítico (duplo 6) soma +1d6; metade dos inimigos fora → moral sinalizada
    out = until_turn("c2", "cbt-0001", "pc-ana")
    assert any(x["kind"] == "deathsave" and x["actor"] == "capanga-1" for x in state(C2)["log"])  # §10 a cada rodada
    out = cb("c2", *A, "--target", "capanga-2", "--weapon", "media", "--skill", "armas brancas", "--roll", "12", "--action-id", "a2")
    assert "CRÍTICO" in out and "1d6+1d6" in out
    assert "impossível" in cb("c2", *A, "--action-id", "a2", "--damage-roll", "13", ok=False)
    out = cb("c2", *A, "--action-id", "a2", "--damage-roll", "7")
    assert part(C2, "capanga-2")["pv"] == 0 and rolls(C2)[-1]["expr"] == "1d6+1d6+1" and rolls(C2)[-1]["total"] == 8
    assert "MORAL" in out and state(C2)["morale"]["text"].startswith("2 de 3")

    # 0 PV no PJ: caído + ferido grave; teste por rodada informado pelo jogador
    cb("c2", "act", "cbt-0001", "--action", "adjust", "--target", "pc-ana", "--pv", "-20", "--mod-reason", "explosão narrada")
    ana = part(C2, "pc-ana")
    assert ana["pv"] == 0 and {"caido", "ferido-grave"} <= {c["name"] for c in ana["conditions"]}
    out = until_turn("c2", "cbt-0001", "pc-ana")
    assert "teste de ferido grave" in out
    assert "ferido grave" in cb("c2", "next", "cbt-0001", ok=False)
    cb("c2", "act", "cbt-0001", "--actor", "pc-ana", "--action", "deathsave", "--roll", "3")
    assert part(C2, "pc-ana")["markers"] == 1
    cb("c2", "act", "cbt-0001", "--actor", "pc-ana", "--action", "rise")
    assert (part(C2, "pc-ana")["pv"], part(C2, "pc-ana")["determination"]) == (1, 2)

    # fim: evento na linha do tempo (idempotente), resumo, mudanças para a ficha
    out = cb("c2", "end", "cbt-0001", "--outcome", "vitoria", "--reason", "capangas caíram")
    assert "Evento registrado: evt-" in out and "pc-ana: PV 11→1/11; Determinação 3→2" in out and "marcadores de ferido grave: 1" in out, out
    cb("c2", "end", "cbt-0001", "--outcome", "vitoria", "--reason", "de novo")
    evs = [json.loads(x) for x in (C2 / "state" / "events.jsonl").read_text().splitlines()]
    cmb = [e for e in evs if e.get("type") == "combate"]
    assert len(cmb) == 1 and cmb[0]["entities"] == ["pc-ana"] and "vitoria" in cmb[0]["text"]
    assert state(C2)["result"]["summary"]["capanga-2"]["caiu"] is True
    assert "encerrado" in cb("c2", *A, "--target", "chefe", "--weapon", "leve", "--roll", "8", ok=False)
    assert "a2" in cb("c2", "log", "cbt-0001") and "[encerrado]" in cb("c2", "status", "cbt-0001")
    assert hashlib.sha256(mp.read_bytes()).hexdigest() == msha and not (C2 / "maps" / "jogador" / "cbt-0001").exists()
    assert "OK" in run("world", "verify", "--campaign", "c2")
finally:
    shutil.rmtree(tmp)
print("OK: testes do motor de combate passaram")
