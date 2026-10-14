#!/usr/bin/env python3
"""Fase 3.2, etapas 3–4, em raiz temporária: campanha longa (L1–L7) e agência do protagonista (AG1–AG7).

Status de cada verificação:
  OK     comportamento esperado
  FALHA  o motor não sustentou o cenário (coerência, causalidade, privacidade, recuperação)
  VIOLA  o motor decidiu algo pelo protagonista (violação de agência; correção aguarda aprovação)
  INFO   medição ou comportamento registrado sem critério de aprovação (ex.: escala, zona cinzenta)
Código de saída ≠ 0 se houver FALHA ou VIOLA."""
import datetime as dt, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
os.environ.pop("RPG_SIM_MODULES", None)  # todos os módulos ligados
import rpg, sim  # noqa: E402

env = {**os.environ, "RPG_ROOT": str(tmp)}
J = json.dumps
CAMPS = tmp / "campaigns"
TERRA = tmp / "templates" / "calendars" / "terrestre.json"
WEEK = ["segunda", "terça", "quarta", "quinta", "sexta"]
START = "2026/11/09 06:00"  # segunda
RESULTS = []


def check(cid, ok, detail=""):
    RESULTS.append((cid, "OK" if ok else "FALHA", detail))


def agency(cid, ok, detail=""):
    RESULTS.append((cid, "OK" if ok else "VIOLA", detail))


def info(cid, detail):
    RESULTS.append((cid, "INFO", detail))


def sh(*args, camp=None):
    args = [str(x) for x in args] + (["--campaign", camp] if camp else [])
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def must(*args, camp=None):
    rc, out = sh(*args, camp=camp)
    assert rc == 0, f"preparação falhou: {args}\n{out}"
    return out


def ent(D, eid, known="", **meta):
    sub = {"pc": "characters", "npc": "npcs", "loc": "locations", "quest": "quests"}[eid.split("-")[0]]
    body = "\n".join(f"{k}: {v}" for k, v in {"id": eid, "name": eid.split("-", 1)[1].title(), **meta}.items())
    (D / sub / f"{eid}.md").write_text(f"---\n{body}\n---\n# {eid}\n\n## Conhecido pelos jogadores\n{known}\n")


def evs(D):
    return sim.fold_events(sim.read_jsonl(D / "state" / "events.jsonl"))


def gk(D, prefix, status=None):
    return [e for e in evs(D) if (e.get("gen_key") or "").startswith(prefix) and (status is None or e["status"] == status)]


def loc(D, eid):
    sub = "characters" if eid.startswith("pc-") else "npcs"
    return rpg.read_meta(D / sub / f"{eid}.md").get("location")


def feels(D):
    return sim.feelings(sim.read_jsonl(D / "state" / "relations.jsonl"))


def jload(D, rel):
    return sim.load_json(D / "state" / rel, {})


def pc_moves(D):
    """(tipo do evento, destino) de todo efeito processado que mudou o local de pc-ana."""
    out = []
    for e in evs(D):
        for i, f in enumerate(e.get("effects", [])):
            if f.get("type") == "entity.set" and f.get("id") == "pc-ana" and f.get("field") == "location":
                if e["status"] == "processado" or sim.applied_count(e) > i:
                    out.append((e.get("type"), f.get("value")))
    return out


def strip_t(x):
    """Remove o carimbo de tempo REAL ('t') em qualquer profundidade: o resto deve ser determinístico."""
    if isinstance(x, dict):
        return {k: strip_t(v) for k, v in x.items() if k != "t"}
    if isinstance(x, list):
        return [strip_t(v) for v in x]
    return x


# ------------------------------------------------------------------ oráculo independente (datetime, não sim.occurrences)
HOL = {tuple(map(int, h["date"].split("/"))) for h in json.loads((PROJ / "templates" / "calendars" / "terrestre.json").read_text())["holidays"]}


def occ(d0, d1, hhmm, weekdays=None, monthday=None, skip_holiday=False):
    """Instantes (datetime) em (d0, d1] no horário dado; weekdays 0=segunda."""
    h, m = map(int, hhmm.split(":"))
    out, day = [], d0.date()
    while dt.datetime.combine(day, dt.time(h, m)) <= d1:
        t = dt.datetime.combine(day, dt.time(h, m))
        if t > d0 and (weekdays is None or day.weekday() in weekdays) and (monthday is None or day.day == monthday) \
                and not (skip_holiday and (day.month, day.day) in HOL):
            out.append(t)
        day += dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------ campanha longa: construção e roteiro
def build(cid, seed=7):
    must("new", cid, "--name", cid, "--system", "aventura-2d6", "--setting", "moderna", "--styles", "vida-cotidiana, investigacao")
    D = CAMPS / cid
    rpg.set_key(D / "campaign.yaml", "economy", "on")
    must("time", "init", "--calendar", TERRA, "--start", START, "--seed", seed, camp=cid)
    ent(D, "pc-ana", "Protagonista", location="loc-casa", home="loc-casa")
    for nid, where in (("npc-chefe", "loc-loja"), ("npc-lia", "loc-casa"), ("npc-senhorio", "loc-casa"), ("npc-det", "loc-bar")):
        ent(D, nid, "Conhecido", status="vivo", location=where)
    for lid in ("loc-casa", "loc-loja", "loc-bar", "loc-escola"):
        ent(D, lid, "Lugar")
    (D / "state" / "current.md").write_text("# Estado atual\n\n- **Local atual:** loc-casa\n- **Personagens presentes:** pc-ana\n")
    must("econ", "open", "pc-ana", "300", "--reason", "saldo da ficha", "--op", "o-ana", camp=cid)
    must("econ", "open", "npc-chefe", "100000", "--reason", "caixa da loja", "--op", "o-chefe", camp=cid)
    miss = [{"type": "relation.feel", "from": "npc-chefe", "to": "pc-ana", "dim": "confianca", "delta": -10, "reason": "faltou"}]
    hit = [{"type": "relation.feel", "from": "npc-chefe", "to": "pc-ana", "dim": "respeito", "delta": 1, "reason": "veio"}]
    must("life", "job", "cmp-trab", "--who", "pc-ana", "--text", "Atendente na loja", "--employer", "npc-chefe", "--loc", "loc-loja",
         "--at", "09:00", "--days", ",".join(WEEK), "--skip-rest", "--duration", "8h", "--salary", "500", "--pay-days", "sexta",
         "--pay-at", "18:00", "--on-miss", J(miss), "--on-attend", J(hit), "--reason", "o jogador aceitou a vaga", "--op", "j1", camp=cid)
    must("life", "course", "cmp-curso", "--who", "pc-ana", "--text", "Curso noturno", "--school", "loc-escola", "--loc", "loc-escola",
         "--at", "19:00", "--days", "terça,quinta", "--fee", "200", "--fee-monthday", "10", "--authorized", "jogador: mensalidade em débito automático", "--op", "c1", camp=cid)
    must("life", "add", "cmp-vigia", "--who", "npc-det", "--kind", "trabalho", "--text", "Vigia a loja de Ana", "--hidden",
         "--at", "21:00", "--days", "sexta", "--op", "v1", camp=cid)
    must("econ", "set", "--json", J({"routes": [{"id": "aluguel", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "qty": 900,
                                                 "bill": True, "partial": True, "when": {"at": "10:00", "monthday": 15},
                                                 "authorized": "jogador: aluguel em débito automático",
                                                 "on_arrears": [{"type": "relation.feel", "from": "npc-senhorio", "to": "pc-ana",
                                                                 "dim": "confianca", "delta": -15},
                                                                {"type": "flag.set", "name": "aluguel_atrasado", "value": True}]}]}), camp=cid)
    must("npc", "set", "npc-lia", "--routine", J([{"from": "08:00", "to": "18:00", "loc": "loc-loja", "days": WEEK, "skip_rest": True},
                                                  {"from": "18:30", "to": "23:00", "loc": "loc-casa"}]), camp=cid)
    must("fact", "add", "fact-briga", "Briga feia no bar na sexta à noite", "--about", "loc-bar", camp=cid)
    must("npc", "set", "npc-det", "--tier", "2", "--goals", J([{"id": "dever", "desc": "investigar", "priority": 3, "actions": [
        {"id": "investigar", "desc": "abre investigação sobre a briga", "duration": "2h", "utility": 2, "risk": 0,
         "preconditions": [{"type": "knows", "who": "npc-det", "fact": "fact-briga"},
                           {"type": "not", "of": {"type": "flag", "name": "investigacao"}}],
         "effects": [{"type": "flag.set", "name": "investigacao", "value": True}]}]}]), camp=cid)
    return D


def drive(cid, hook=lambda stage: None):
    """6 semanas: 14 avanços de 1 dia, 2 de 1 semana, 1 de 2 semanas. Ações DECLARADAS do jogador em pontos fixos."""
    for i in range(1, 15):
        if not hook(f"d{i}"):
            must("time", "advance", "1d", "--op", f"d{i}", "--reason", "um dia", camp=cid)
        if i == 1:
            must("life", "attend", "cmp-trab", "presente", "--at", "2026/11/09 09:00", "--op", "att1", camp=cid)
        if i == 2:
            must("life", "attend", "cmp-trab", "faltou", "--at", "2026/11/10 09:00", "--note", "jogador: dormiu", "--op", "att2", camp=cid)
        if i == 4:  # sexta 13, 06:00: o jogador declara que Ana passa o dia no bar
            must("world", "move", "pc-ana", "loc-bar", "--reason", "jogador: Ana vai ao bar", "--op", "mv1", camp=cid)
            must("time", "schedule", "Briga no bar", "--at", "2026/11/13 22:00", "--loc", "loc-bar", "--hidden", "--effects",
                 J({"type": "knowledge.witness", "fact": "fact-briga", "loc": "loc-bar"}), "--op", "briga", camp=cid)
        if i == 5:
            must("world", "move", "pc-ana", "loc-casa", "--reason", "jogador: Ana volta para casa", "--op", "mv2", camp=cid)
            must("world", "feel", "pc-ana", "npc-lia", "afeto", "+5", "--declared", "--reason", "jogador: Ana gosta da Lia",
                 "--op", "fe1", camp=cid)
    for stage, dur in (("w3", "1sem"), ("w4", "1sem"), ("w56", "2sem")):
        if not hook(stage):
            must("time", "advance", dur, "--op", stage, "--reason", "semanas passam", camp=cid)


def outcome(D):
    """Estado de RESULTADO (não o registro): o que uma execução com falhas recuperadas deve reproduzir exatamente."""
    E = jload(D, "sim/economy.json")
    return {"saldos": jload(D, "sim/resources.json").get("holdings"),
            "atrasos": {r["id"]: (r.get("arrears", 0), r.get("status", "ativa")) for r in E.get("routes", [])},
            "presencas": {k: v.get("status") for k, v in jload(D, "sim/life.json").get("attendance", {}).items()},
            "flags": jload(D, "sim/flags.json"),
            "sentimentos": {f"{a}>{b}": v for (a, b), v in feels(D).items()},
            "conhecimento": {w: sorted(k) for w, k in jload(D, "sim/facts.json").get("known", {}).items()},
            "locais": {p.stem: rpg.read_meta(p).get("location") for sub in ("characters", "npcs") for p in sorted((D / sub).glob("*.md"))},
            "contagens": {p: len(gk(D, p, "processado")) for p in ("econ:sal-trab", "econ:mens-curso", "econ:aluguel", "atraso:",
                                                                  "life:cmp-trab:", "life:cmp-curso:", "routine:npc-lia",
                                                                  "npc-daily:npc-det")},
            "relogio": sim.load_clock(D)["now"]}


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    CAMPS.mkdir()
    t0 = dt.datetime(2026, 11, 9, 6, 0)
    t1 = t0 + dt.timedelta(days=42)

    # ============================================================ L1 — seis semanas, vários sistemas juntos
    T = time.perf_counter()
    A = build("longa")
    drive("longa")
    dur_l1 = time.perf_counter() - T
    clock = sim.load_clock(A)
    cal = clock["calendar"]
    check("L1.relogio-no-instante-esperado", sim.fmt(clock["now"], cal).endswith("21 de dezembro, 2026, 06:00") and not clock.get("pending_op"),
          sim.fmt(clock["now"], cal))
    job = occ(t0, t1, "09:00", range(5), skip_holiday=True)
    curso = occ(t0, t1, "19:00", [1, 3])
    sal = occ(t0, t1, "18:00", [4])
    fee = occ(t0, t1, "09:00", monthday=10)
    rent = occ(t0, t1, "10:00", monthday=15)
    lia_loja = occ(t0, t1, "08:00", range(5), skip_holiday=True)
    o = outcome(A)
    c = o["contagens"]
    check("L1.lembretes-de-trabalho", c["life:cmp-trab:"] == len(job) == 29, f"motor {c['life:cmp-trab:']} × oráculo {len(job)}")
    check("L1.lembretes-de-curso", c["life:cmp-curso:"] == len(curso) == 12, f"motor {c['life:cmp-curso:']} × oráculo {len(curso)}")
    check("L1.salarios", c["econ:sal-trab"] == len(sal) == 6, f"motor {c['econ:sal-trab']} × oráculo {len(sal)}")
    check("L1.mensalidades-e-aluguel", (c["econ:mens-curso"], c["econ:aluguel"]) == (len(fee), len(rent)) == (2, 2), str(c))
    lia_moves = [e for e in gk(A, "routine:npc-lia", "processado") if e["effects"][0]["value"] == "loc-loja"]
    check("L1.rotina-npc-por-dia-util", len(lia_moves) == len(lia_loja) and loc(A, "npc-lia") == "loc-casa",
          f"{len(lia_moves)} idas à loja × oráculo {len(lia_loja)}; agora {loc(A, 'npc-lia')}")
    # dinheiro (regras documentadas): 300 −200 (mens. 10/11) +500 (13/11) −600 parcial de 900 (15/11, atraso 300) +500×3
    # −200 (10/12) +500 −900 (15/12) +500 = 1400
    check("L1.saldos", o["saldos"]["pc-ana"]["dinheiro"] == 1400 and o["saldos"]["npc-senhorio"]["dinheiro"] == 1500
          and o["saldos"]["npc-chefe"]["dinheiro"] == 100000 - 6 * 500 and o["saldos"]["loc-escola"]["dinheiro"] == 400, J(o["saldos"]))
    check("L1.atraso-acumulado-uma-vez", o["atrasos"]["aluguel"] == (300, "ativa") and c["atraso:"] == 1
          and o["flags"].get("aluguel_atrasado") is True and o["sentimentos"].get("npc-senhorio>pc-ana") == {"confianca": -15}, J(o["atrasos"]))
    keys = [e["gen_key"] for e in evs(A) if e.get("gen_key")]
    check("L1.sem-gen-key-duplicada", len(keys) == len(set(keys)), f"{len(keys)} chaves")
    late = [e["id"] for e in evs(A) if e["status"] in ("pendente", "processando", "falhou") and e.get("due", 0) <= clock["now"]]
    check("L1.nada-vencido-pendente", not late, str(late[:5]))
    early = [e["id"] for e in evs(A) if e["status"] == "processado" and e.get("processed_at") is not None
             and e["processed_at"] < e.get("due", 0)]
    check("L1.nada-processado-antes-da-hora", not early, str(early[:5]))
    briga = gk(A, "schedule:briga")[0]
    know = o["conhecimento"]
    check("L1.testemunhas-pelo-local-declarado", "fact-briga" in know.get("pc-ana", []) and "fact-briga" in know.get("npc-det", [])
          and "fact-briga" not in know.get("npc-lia", []) and "fact-briga" not in know.get("npc-chefe", []), J(know))
    inv = [e for e in evs(A) if e.get("type") == "npc.acao" and e["status"] == "processado"]
    check("L1.npc-age-uma-vez-pelo-que-sabe", o["flags"].get("investigacao") is True and len(inv) == 1
          and inv[0]["due"] > briga["due"], f"{len(inv)} ação(ões)")
    chain = must("world", "chain", inv[0]["id"], camp="longa") if inv else ""
    check("L1.cadeia-causal-ate-a-briga", briga["id"] in chain, chain[-300:])
    out = must("world", "verify", camp="longa")
    check("L1.verify-limpo", "OK" in out, out[-300:])
    info("L1.duracao", f"{dur_l1:.1f}s; {len(evs(A))} eventos; {sum(1 for _ in (A / 'state' / 'events.jsonl').open())} linhas em events.jsonl")

    # ============================================================ AG1 — compromissos são lembretes; só a declaração tem consequência
    agency("AG1.presenca-so-declarada", sorted(o["presencas"].values()) == ["faltou", "presente"], J(o["presencas"]))
    agency("AG1.sem-on-miss-presumido", o["sentimentos"].get("npc-chefe>pc-ana") == {"respeito": 1, "confianca": -10},
           J(o["sentimentos"].get("npc-chefe>pc-ana")))
    agency("AG1.local-do-pj-so-por-declaracao", pc_moves(A) == [("movimento", "loc-bar"), ("movimento", "loc-casa")]
           and loc(A, "pc-ana") == "loc-casa", str(pc_moves(A)))
    agency("AG1.sentimento-do-pj-so-declarado", [k for k in o["sentimentos"] if k.startswith("pc-ana>")] == ["pc-ana>npc-lia"]
           and all(r.get("declared") for r in sim.read_jsonl(A / "state" / "relations.jsonl") if r.get("from") == "pc-ana"), J(o["sentimentos"]))
    check("AG1.mundo-reage-a-escolha-declarada", o["sentimentos"]["npc-chefe>pc-ana"]["confianca"] == -10)  # controle positivo

    # ============================================================ L6 — determinismo: mesma seed e mesmas entradas → mesmo mundo
    B = build("longa-b")
    drive("longa-b")
    diffs = []
    for rel in ["events.jsonl", "relations.jsonl"]:
        if strip_t(sim.read_jsonl(A / "state" / rel)) != strip_t(sim.read_jsonl(B / "state" / rel)):
            diffs.append(rel)
    for p in sorted((A / "state" / "sim").glob("*.json")):
        if strip_t(sim.load_json(p, {})) != strip_t(sim.load_json(B / "state" / "sim" / p.name, {})):
            diffs.append(p.name)
    for sub in ("characters", "npcs", "quests"):
        for p in sorted((A / sub).glob("*.md")):
            if p.read_text() != (B / sub / p.name).read_text():
                diffs.append(f"{sub}/{p.name}")
    check("L6.reproducao-identica", not diffs, f"diferem: {diffs}")

    # ============================================================ L3 — falhas no meio da campanha longa, retomada, mesmo resultado
    def failures(stage):
        Cd = CAMPS / "longa-c"
        if stage == "d5":  # queda do processo DENTRO de um efeito de economia (sem registro de falha)
            sim.load_modules()
            orig, calls = sim.EFFECTS["econ.run"], []

            def crash(w, f, key, ev):
                calls.append(key)
                if len(calls) == 1:
                    raise KeyboardInterrupt("queda simulada")
                return orig(w, f, key, ev)
            sim.EFFECTS["econ.run"] = crash
            try:
                rpg.main(["time", "advance", "1d", "--op", "d5", "--reason", "um dia", "--campaign", "longa-c"])
                raise AssertionError("a queda não ocorreu")
            except KeyboardInterrupt:
                pass
            finally:
                sim.EFFECTS["econ.run"] = orig
            pend = sim.load_clock(Cd).get("pending_op")
            stuck = [e for e in evs(Cd) if e["status"] == "processando"]
            check("L3.queda-deixa-avanco-pendente", pend and pend["op"] == "d5" and len(stuck) == 1, J(pend))
            out = must("time", "inspect", stuck[0]["id"], camp="longa-c") if stuck else ""
            check("L3.queda-inspecionavel", "processando" in out and "INCERTO" in out, out[-200:])
            must("time", "resume", camp="longa-c")
            return True
        if stage == "w3":  # falha PERMANENTE no meio: não bloqueia; o mestre registra a decisão
            w = sim.World(Cd)
            w.schedule({"type": "evento", "text": "doação sem origem", "due": w.now + 3 * 1440,
                        "effects": [{"type": "resource.produce", "who": "npc-lia", "res": "dinheiro", "qty": 10}]})
            rc, out = sh("time", "advance", "1sem", "--op", "w3", "--reason", "semanas passam", camp="longa-c")
            bad = [e for e in evs(Cd) if sim.is_permanent(e)]
            check("L3.permanente-nao-bloqueia", rc == 0 and "ATENÇÃO" in out and len(bad) == 1 and not sim.load_clock(Cd).get("pending_op"),
                  out[-200:])
            must("time", "cancel", bad[0]["id"], "--reason", "mestre: doação inventada, descartada", camp="longa-c")
            return True
        if stage == "w56":  # falha RECUPERÁVEL: a escola some do disco; avanço para; corrige; resume
            esc = Cd / "locations" / "loc-escola.md"
            esc.rename(tmp / "loc-escola.md")
            rc, out = sh("time", "advance", "2sem", "--op", "w56", "--reason", "semanas passam", camp="longa-c")
            pend = sim.load_clock(Cd).get("pending_op")
            failed = [e for e in evs(Cd) if e["status"] == "falhou" and not sim.is_permanent(e)]
            check("L3.recuperavel-para-o-avanco", rc != 0 and "time resume" in out and pend and len(failed) == 1, out[-250:])
            rc2, _ = sh("time", "advance", "1d", "--op", "outro", "--reason", "x", camp="longa-c")
            check("L3.outro-avanco-recusado-enquanto-pendente", rc2 != 0)
            (tmp / "loc-escola.md").rename(esc)
            must("time", "advance", "2sem", "--op", "w56", "--reason", "semanas passam", camp="longa-c")  # mesma op conclui
            return True
        return False

    Cc = build("longa-c")
    drive("longa-c", failures)
    oc, oa = outcome(Cc), outcome(A)
    diff = {k: (oa[k], oc[k]) for k in oa if oa[k] != oc[k]}
    check("L3.mesmo-resultado-que-sem-falhas", not diff, J(diff)[:600])
    check("L3.nada-aplicado-duas-vezes", len([e["gen_key"] for e in evs(Cc) if e.get("gen_key")])
          == len({e["gen_key"] for e in evs(Cc) if e.get("gen_key")}))

    # ============================================================ L4 — calendário muda no meio sem reinterpretar o passado
    Dd = build("longa-d")
    for i in range(1, 15):
        must("time", "advance", "1d", "--op", f"d{i}", "--reason", "um dia", camp="longa-d")  # → segunda 23/11 06:00
    tl0 = must("world", "timeline", camp="longa-d")
    dates0 = [(e["id"], e.get("date")) for e in evs(Dd)]
    n_trab0 = len(gk(Dd, "life:cmp-trab:"))
    must("time", "calendar", "--rest", "domingo", "--holiday", "11/25=Dia Inventado", camp="longa-d")
    tl1 = must("world", "timeline", camp="longa-d")
    gone = [ln for ln in tl0.splitlines() if ln not in tl1.splitlines()]
    check("L4.passado-identico-na-linha-do-tempo", not gone and [(e["id"], e.get("date")) for e in evs(Dd)][:len(dates0)] == dates0,
          f"linhas que mudaram: {gone[:3]}")
    cal = sim.load_clock(Dd)["calendar"]
    check("L4.folga-antiga-vale-no-passado", sim.is_rest(sim.parse_when("2026/11/14 12:00", cal, 0), cal)
          and not sim.is_rest(sim.parse_when("2026/11/28 12:00", cal, 0), cal))
    must("time", "advance", "1sem", "--op", "w3", "--reason", "semana", camp="longa-d")
    new = sorted(sim.fmt(e["due"], cal) for e in gk(Dd, "life:cmp-trab:")[n_trab0:])
    check("L4.feriado-novo-vale-adiante", len(new) == 4 and not any("25 de novembro" in x for x in new), str(new))
    check("L4.ocorrencias-passadas-intactas", len([e for e in gk(Dd, "life:cmp-trab:") if e["due"] < sim.parse_when("2026/11/23 06:00", cal, 0)])
          == n_trab0)
    rc, out = sh("life", "attend", "cmp-trab", "presente", "--at", "2026/11/12 09:00", "--op", "late-att", camp="longa-d")
    check("L4.presenca-passada-ainda-valida", rc == 0, out[-150:])
    rc, out = sh("life", "attend", "cmp-trab", "presente", "--at", "2026/11/25 09:00", "--op", "hol-att", camp="longa-d")
    check("L4.feriado-novo-sem-ocorrencia", rc != 0 and "não tem ocorrência" in out, out[-150:])

    # ============================================================ L2 — investigação encadeada: boato, testemunha, relato, facção, missão, prazo
    must("new", "invest", "--name", "Invest", "--system", "aventura-2d6", "--setting", "fantasia-medieval")
    I = CAMPS / "invest"
    (I / "world.md").write_text((I / "world.md").read_text() + "\n- fac-guarda: Guarda da Cidade\n")
    must("time", "init", "--start", "1/1/1 05:00", "--seed", "3", camp="invest")
    ent(I, "pc-ana", "Protagonista", location="loc-praca")
    for nid, where in (("npc-guarda", "loc-praca"), ("npc-capitao", "loc-quartel"), ("npc-fofoca", "loc-mercado")):
        ent(I, nid, "Conhecido", status="vivo", location=where, **({"faction": "fac-guarda"} if nid != "npc-fofoca" else {}))
    for lid in ("loc-praca", "loc-quartel", "loc-mercado"):
        ent(I, lid, "Lugar")
    ent(I, "quest-furto", "", status="oculta", type="principal", giver="npc-capitao")
    (I / "state" / "current.md").write_text("# Estado atual\n\n- **Local atual:** loc-mercado\n- **Personagens presentes:** pc-ana\n")
    must("fact", "add", "fact-furto", "Roubaram o cofre da praça", "--about", "loc-praca", camp="invest")
    must("fact", "add", "fact-boato", "Ana é a ladra", "--false", "--about", "pc-ana", camp="invest")
    must("npc", "set", "npc-guarda", "--tier", "2", "--goals", J([{"id": "dever", "priority": 5, "actions": [
        {"id": "relatar", "desc": "relata o furto ao capitão", "duration": "1h", "utility": 2, "risk": 0,
         "preconditions": [{"type": "knows", "who": "npc-guarda", "fact": "fact-furto"},
                           {"type": "not", "of": {"type": "flag", "name": "relatado"}}],
         "effects": [{"type": "knowledge.tell", "from": "npc-guarda", "to": "npc-capitao", "fact": "fact-furto"},
                     {"type": "flag.set", "name": "relatado", "value": True}]}]}]), camp="invest")
    out = must("npc", "decide", "npc-guarda", camp="invest")
    check("L2.sem-conhecimento-sem-acao", "sem ação" in out.lower(), out[-120:])
    must("time", "schedule", "Fundos da guarda", "--effects",
         J({"type": "resource.produce", "who": "fac-guarda", "res": "moedas", "qty": 50, "source": "tesouro"}), camp="invest")
    must("faction", "set", "fac-guarda", "--json", J({"leader": "npc-capitao", "plans": [{"id": "cacada", "goal": "achar o ladrão",
         "status": "ativo", "steps": [{"id": "patrulha", "desc": "patrulhar a praça", "duration": "4h", "cost": {"moedas": 20},
                                       "start_conditions": [{"type": "knows", "who": "npc-capitao", "fact": "fact-furto"}],
                                       "done_conditions": [], "effects": [{"type": "flag.set", "name": "patrulha", "value": True}]}]}]}),
         camp="invest")
    must("quest", "define", "quest-furto", "--json", J({"objectives": [{"id": "achar", "desc": "achar o ladrão"}],
         "unlock_conditions": [{"type": "flag", "name": "patrulha"}], "unlock_to": "disponivel", "deadline": "+3d",
         "consequences": {"expirada": [{"type": "flag.set", "name": "ladrao_fugiu", "value": True}]},
         "public": "O capitão procura o ladrão.", "gm": "SEGREDO-GM-FURTO"}), camp="invest")
    must("world", "move", "pc-ana", "loc-mercado", "--reason", "jogador: Ana vai ao mercado", "--op", "mv1", camp="invest")
    must("time", "schedule", "Furto do cofre", "--in", "2h", "--loc", "loc-praca", "--hidden", "--op", "furto",
         "--effects", J({"type": "knowledge.witness", "fact": "fact-furto", "loc": "loc-praca"}), camp="invest")
    must("fact", "learn", "npc-fofoca", "fact-boato", "--status", "acredita", "--source", "ouviu na feira", "--confidence", "0.9", camp="invest")
    must("time", "schedule", "Fofoca chega à guarda", "--in", "3h", "--hidden", "--op", "fofoca",
         "--effects", J({"type": "knowledge.tell", "from": "npc-fofoca", "to": "npc-guarda", "fact": "fact-boato"}), camp="invest")
    for i in range(1, 7):
        must("time", "advance", "1d", "--op", f"i{i}", "--reason", "um dia", camp="invest")
    kn = jload(I, "sim/facts.json")["known"]
    check("L2.so-testemunha-sabe", kn.get("npc-guarda", {}).get("fact-furto", {}).get("status") == "sabe"
          and "fact-furto" not in kn.get("pc-ana", {}) and "fact-furto" not in kn.get("npc-fofoca", {}), J({k: list(v) for k, v in kn.items()}))
    check("L2.relato-vira-crenca", kn.get("npc-capitao", {}).get("fact-furto", {}).get("status") == "acredita")
    check("L2.boato-falso-nunca-sabido", kn.get("npc-guarda", {}).get("fact-boato", {}).get("status") in ("acredita", "suspeita")
          and all(v.get("fact-boato", {}).get("status") != "sabe" for v in kn.values()))
    fl = jload(I, "sim/flags.json")
    F = jload(I, "sim/factions.json")
    st_q = rpg.read_meta(I / "quests" / "quest-furto.md")["status"]
    check("L2.faccao-paga-custo-e-age", fl.get("patrulha") is True and sim.balance(I, "fac-guarda", "moedas") == 30, J(F)[:200])
    check("L2.missao-desbloqueia-e-expira-uma-vez", st_q == "expirada" and fl.get("ladrao_fugiu") is True
          and len([e for e in evs(I) if "expirada" in J(e.get("result") or {}, ensure_ascii=False) and e["status"] == "processado"]) >= 1, st_q)
    qev = lambda to: next((e for e in evs(I) if e.get("type") == "missao" and any(f.get("to") == to for f in e.get("effects", []))), None)  # noqa: E731
    unl, exp = qev("disponivel"), qev("expirada")
    furto = gk(I, "schedule:furto")[0]
    chain = must("world", "chain", unl["id"], camp="invest") if unl else ""
    check("L2.cadeia-do-desbloqueio-ate-o-furto", unl and furto["id"] in chain, chain.splitlines()[1][:160] if chain else "sem desbloqueio")
    check("L2.expiracao-tem-causa", exp and exp.get("cause"), f"{exp and exp['id']}: cause={exp and exp.get('cause')}")
    info("L2.prazo-conta-da-definicao", f"definida Dia 1 05:00 com '+3d'; desbloqueada {unl and unl['date']}; expirada {exp and exp['date']}")
    out = must("world", "verify", camp="invest")
    check("L2.verify-limpo", "OK" in out, out[-300:])

    # ============================================================ L5 — privacidade depois de semanas de simulação
    must("map", "gen", "cidade", "map-cidade", "--name", "Cidade", "--seed", "5", camp="longa")
    must("map", "place", "map-cidade", "--name", "Bar do Zé", "--kind", "taverna", "--ref", "loc-bar", "--at", "10,10", camp="longa")
    must("map", "place", "map-cidade", "--name", "Esconderijo do Detetive", "--kind", "ruina", "--at", "20,20", camp="longa")
    must("map", "reveal", "map-cidade", "poi-bar-do-ze", "--status", "visitado", "--reason", "Ana esteve lá", camp="longa")
    pub = "".join(must(*c, camp="longa") for c in (("world", "timeline", "--public"), ("world", "rel", "pc-ana", "--public"),
                                                    ("time", "now", "--public"), ("life", "agenda", "--public", "--span", "7d")))
    pub_i = "".join(must(*c, camp="invest") for c in (("world", "timeline", "--public"), ("quest", "list", "--public"),
                                                       ("quest", "show", "quest-furto", "--public")))
    player = "".join(p.read_text() for p in (A / "maps" / "jogador").glob("*"))
    leaks = [s for s in ("Briga no bar", "Ciclo econômico", "Atraso", "abre investigação", "Vigia a loja", "⇢", "100000", "97000",
                         "aluguel_atrasado", "reavalia") if s in pub]
    leaks += [s for s in ("SEGREDO-GM-FURTO", "Furto do cofre", "Fofoca", "Ana é a ladra", "relata o furto", "patrulhar") if s in pub_i]
    leaks += [s for s in ("Esconderijo do Detetive", "Briga no bar", "Vigia") if s in player]
    check("L5.sem-vazamento", not leaks, str(leaks))
    check("L5.controle-positivo", "Atendente na loja" in pub and "loc-bar" in pub and "Bar do Zé" in player
          and "O capitão procura o ladrão" in pub_i, "")

    # ============================================================ L7 — escala: 3 meses de avanços diários (medição)
    shutil.copytree(A, CAMPS / "escala")
    S = CAMPS / "escala"
    sim.load_modules()
    per = []
    for i in range(90):
        T = time.perf_counter()
        sim.advance(S, 1440, f"esc{i}", "escala")
        per.append(time.perf_counter() - T)
    n_ev = len(evs(S))
    first, last = sum(per[:10]) / 10, sum(per[-10:]) / 10
    check("L7.relogio-e-sem-pendencias", not sim.load_clock(S).get("pending_op")
          and not [e for e in evs(S) if e["status"] in ("pendente", "processando", "falhou") and e.get("due", 0) <= sim.load_clock(S)["now"]])
    info("L7.custo-por-dia", f"{len(per)} dias: 10 primeiros {first:.3f}s/dia, 10 últimos {last:.3f}s/dia (×{last / first:.1f}); "
                             f"{n_ev} eventos; total {sum(per):.1f}s")

    # ============================================================ AG2–AG7 — agência do protagonista (regressão da Etapa 3.2.1)
    # Duas camadas: (1) a definição NOVA que decide pelo PJ é recusada sem alterar nada; (2) uma definição ANTIGA (gravada
    # antes da guarda — injetada aqui direto no estado) falha de forma PERMANENTE na execução: nada é aplicado ao PJ e o
    # relógio não trava. Cada sonda que pode mover o PJ roda numa campanha própria (prova pelo local final isolado).
    def ag_camp(cid):
        must("new", cid, "--name", cid, "--system", "aventura-2d6", "--setting", "moderna")
        G = CAMPS / cid
        rpg.set_key(G / "campaign.yaml", "economy", "on")
        (G / "world.md").write_text((G / "world.md").read_text() + "\n- fac-seita: Seita\n")
        must("time", "init", "--calendar", TERRA, "--start", "2026/11/09 05:00", "--seed", "4", camp=cid)
        ent(G, "pc-ana", "Protagonista", location="loc-casa")
        for nid in ("npc-agente", "npc-lia", "npc-amigo", "npc-senhorio"):
            ent(G, nid, "Conhecido", status="vivo", location="loc-casa")
        for lid in ("loc-casa", "loc-porao", "loc-templo", "loc-cela", "loc-festa"):
            ent(G, lid, "Lugar")
        ent(G, "quest-trama", "Uma proposta", status="disponivel", type="principal", giver="npc-agente")
        (G / "state" / "current.md").write_text("# Estado atual\n\n- **Local atual:** loc-casa\n- **Personagens presentes:** pc-ana\n")
        must("econ", "open", "pc-ana", "500", "--reason", "saldo da ficha", "--op", "o1", camp=cid)
        return G

    def snapshot(G):
        return {str(p.relative_to(G)): p.read_bytes() for p in sorted(G.rglob("*")) if p.is_file()}

    HARMLESS = [{"type": "flag.set", "name": "inofensivo", "value": True}]

    def npc_goals(effects):
        return J([{"id": "g", "priority": 5, "actions": [{"id": "a", "desc": "age", "duration": "1h", "utility": 3, "risk": 0,
                                                          "preconditions": [], "effects": effects}]}])

    def fac_plan(effects):
        return J({"plans": [{"id": "p", "goal": "converter Ana", "status": "ativo", "steps": [
            {"id": "ritual", "desc": "ritual", "duration": "1h", "start_conditions": [], "done_conditions": [], "effects": effects}]}]})

    def refused(cid, G, *args):
        s0 = snapshot(G)
        rc, out = sh(*args, camp=cid)
        return rc != 0 and "não decide pelo protagonista" in out and snapshot(G) == s0, out.strip()[-160:]

    def legacy(G, rel, path, effects):
        """Grava a definição antiga direto no estado (como estaria antes da guarda)."""
        S = sim.load_json(G / "state" / "sim" / rel, {})
        x = S
        for k in path:
            x = x[k]
        x["effects"] = effects
        sim.save_json(G / "state" / "sim" / rel, S)

    def blocked(G):
        """Eventos com falha permanente por agência + o relógio não ficou travado."""
        bad = [e for e in evs(G) if sim.is_permanent(e) and "não decide pelo protagonista" in J(e.get("result"), ensure_ascii=False)]
        return bad, not sim.load_clock(G).get("pending_op")

    def day(cid):
        return must("time", "advance", "1d", "--op", "dia1", "--reason", "um dia", camp=cid)

    MOVE = lambda dest: [{"type": "entity.set", "id": "pc-ana", "field": "location", "value": dest}]  # noqa: E731

    # AG4 — local do PJ só muda por 'world move' (deslocamento declarado)
    G = ag_camp("ag-npc-move")
    ok, out = refused("ag-npc-move", G, "npc", "set", "npc-agente", "--tier", "2", "--goals", npc_goals(MOVE("loc-porao")))
    check("AG4.acao-de-npc.definicao-recusada", ok, out)
    must("npc", "set", "npc-agente", "--tier", "2", "--goals", npc_goals(HARMLESS), camp="ag-npc-move")
    legacy(G, "npcs.json", ["npcs", "npc-agente", "goals", 0, "actions", 0], MOVE("loc-porao"))
    out = day("ag-npc-move")
    bad, free = blocked(G)
    agency("AG4.acao-de-npc", loc(G, "pc-ana") == "loc-casa", f"pc-ana em {loc(G, 'pc-ana')}")
    check("AG4.acao-de-npc.falha-permanente-sem-travar", len(bad) == 1 and free and "ATENÇÃO" in out, out[-160:])

    G = ag_camp("ag-fac-move")
    ok, out = refused("ag-fac-move", G, "faction", "set", "fac-seita", "--json", fac_plan(MOVE("loc-templo")))
    check("AG4.etapa-de-faccao.definicao-recusada", ok, out)
    must("faction", "set", "fac-seita", "--json", fac_plan(HARMLESS), camp="ag-fac-move")
    legacy(G, "factions.json", ["factions", "fac-seita", "plans", 0, "steps", 0], MOVE("loc-templo"))
    day("ag-fac-move")
    bad, free = blocked(G)
    agency("AG4.etapa-de-faccao", loc(G, "pc-ana") == "loc-casa", f"pc-ana em {loc(G, 'pc-ana')}")
    check("AG4.etapa-de-faccao.falha-permanente-sem-travar", len(bad) == 1 and free, str([e["id"] for e in bad]))

    G = ag_camp("ag-quest-move")
    ok, out = refused("ag-quest-move", G, "quest", "define", "quest-trama", "--json",
                      J({"objectives": [{"id": "pagar", "desc": "pagar"}], "deadline": "+20h", "consequences": {"expirada": MOVE("loc-cela")}}))
    check("AG4.consequencia-de-missao.definicao-recusada", ok, out)
    ok, out = refused("ag-quest-move", G, "time", "schedule", "Mestre: Ana é levada", "--in", "2h", "--effects", J(MOVE("loc-porao")))
    check("AG4.evento-do-mestre.definicao-recusada", ok, out)
    must("quest", "define", "quest-trama", "--json", J({"objectives": [{"id": "pagar", "desc": "pagar"}], "deadline": "+20h",
                                                          "consequences": {"expirada": HARMLESS}}), camp="ag-quest-move")
    Q = sim.load_json(G / "state" / "sim" / "quests.json", {})
    Q["quests"]["quest-trama"]["consequences"]["expirada"] = MOVE("loc-cela")
    sim.save_json(G / "state" / "sim" / "quests.json", Q)
    sim.load_modules()
    w = sim.World(G)  # evento antigo do mestre (gravado antes da guarda), sem marca de declaração
    w.schedule({"type": "evento", "text": "Mestre: Ana é levada", "due": w.now + 22 * 60, "effects": MOVE("loc-porao")})
    day("ag-quest-move")
    bad, free = blocked(G)
    exp_st = rpg.read_meta(G / "quests" / "quest-trama.md")["status"]
    agency("AG4.consequencia-de-missao-e-evento-do-mestre", loc(G, "pc-ana") == "loc-casa", f"pc-ana em {loc(G, 'pc-ana')}")
    check("AG4.consequencia-de-missao.parcial-auditavel", exp_st == "expirada" and len(bad) == 2 and free,
          f"missão {exp_st}; {len(bad)} falha(s) permanente(s)")

    # AG3, AG5, AG6 e AG2 numa campanha só (não mexem no local do PJ)
    M = "ag-misc"
    G = ag_camp(M)
    FEEL = [{"type": "relation.feel", "from": "pc-ana", "to": "npc-agente", "dim": "afeto", "delta": 20, "declared": True}]
    ACCEPT = [{"type": "quest.status", "quest": "quest-trama", "to": "aceita", "reason": "o NPC decidiu"}]
    TAKE = [{"type": "resource.transfer", "from": "pc-ana", "to": "npc-agente", "res": "dinheiro", "qty": 100}]
    for cid_, effs in (("AG3.acao-de-npc", FEEL), ("AG5.acao-de-npc", ACCEPT), ("AG6.acao-de-npc", TAKE)):
        ok, out = refused(M, G, "npc", "set", "npc-agente", "--tier", "2", "--goals", npc_goals(effs))
        check(f"{cid_}.definicao-recusada", ok, out)
    ok, out = refused(M, G, "npc", "propose", "npc-agente", "--action", J({"desc": "leva Ana", "effects": MOVE("loc-porao")}), "--op", "pp")
    check("AG4.proposta-do-mestre.definicao-recusada", ok and "pp" not in J([e.get("gen_key") for e in evs(G)]), out)
    ok, out = refused(M, G, "faction", "set", "fac-seita", "--json", fac_plan([dict(FEEL[0], to="npc-lia", dim="confianca", delta=10)]))
    check("AG3.etapa-de-faccao.definicao-recusada", ok, out)
    for nid in ("npc-cupido", "npc-aliciador", "npc-cobrador"):
        ent(G, nid, "Conhecido", status="vivo", location="loc-casa")
        must("npc", "set", nid, "--tier", "2", "--goals", npc_goals(HARMLESS), camp=M)
    legacy(G, "npcs.json", ["npcs", "npc-cupido", "goals", 0, "actions", 0], FEEL)
    legacy(G, "npcs.json", ["npcs", "npc-aliciador", "goals", 0, "actions", 0], ACCEPT)
    legacy(G, "npcs.json", ["npcs", "npc-cobrador", "goals", 0, "actions", 0], [dict(TAKE[0], to="npc-cobrador")])
    must("faction", "set", "fac-seita", "--json", fac_plan(HARMLESS), camp=M)
    legacy(G, "factions.json", ["factions", "fac-seita", "plans", 0, "steps", 0], [dict(FEEL[0], to="npc-lia", dim="confianca", delta=10)])
    # economia: autorizada × não autorizada × saldo insuficiente × consumidor
    rc, out = sh("econ", "set", "--json", J({"consumers": [{"id": "assinatura", "who": "pc-ana", "res": "dinheiro", "qty": 1, "every": "1d"}]}), camp=M)
    check("AG6.consumidor-sem-autorizacao-recusado", rc != 0 and "authorized" in out, out[-160:])
    must("econ", "set", "--json", J({"routes": [
        {"id": "conta-luz", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "qty": 50, "bill": True, "when": {"at": "10:00", "monthday": 9},
         "authorized": "jogador: conta de luz em débito automático"},
        {"id": "divida", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "qty": 10000, "bill": True, "when": {"at": "11:00", "monthday": 9},
         "authorized": "jogador: parcela em débito automático"},
        {"id": "antiga", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "qty": 30, "bill": True, "when": {"at": "12:00", "monthday": 9},
         "authorized": "provisória"}],
        "consumers": [{"id": "assinatura", "who": "pc-ana", "res": "dinheiro", "qty": 1, "every": "1d",
                       "authorized": "jogador: assinatura diária"}]}), camp=M)
    E = sim.load_json(G / "state" / "sim" / "economy.json", {})
    next(r for r in E["routes"] if r["id"] == "antiga").pop("authorized")  # entrada gravada antes da regra (sem autorização)
    sim.save_json(G / "state" / "sim" / "economy.json", E)
    check("AG6.verify-lista-entrada-antiga", "antiga: debita pc-ana" in sh("world", "verify", camp=M)[1])
    E0 = jload(G, "sim/economy.json")
    rc_mw, out_mw = sh("life", "add", "cmp-festa", "--who", "npc-lia", "--kind", "social", "--text", "Festa da Lia", "--with", "npc-amigo,pc-ana",
                       "--move-with", "--loc", "loc-festa", "--once", "2026/11/09 20:00", "--op", "festa", camp=M)
    day(M)

    # AG2 — --move-with nunca leva o protagonista
    agency("AG2.move-with-nao-move-pj", loc(G, "pc-ana") == "loc-casa", f"life add rc={rc_mw}; pc-ana em {loc(G, 'pc-ana')}")
    if rc_mw == 0:
        check("AG2.move-with-move-npc", loc(G, "npc-amigo") == "loc-festa", loc(G, "npc-amigo"))
    else:
        info("AG2.pj-em-with-recusado", out_mw.strip()[-120:])
    # AG3 — sentimento do PJ só por declaração do jogador
    fe = {f"{a}>{b}": v for (a, b), v in feels(G).items()}
    agency("AG3.acao-de-npc", "pc-ana>npc-agente" not in fe, J(fe))
    agency("AG3.etapa-de-faccao", "pc-ana>npc-lia" not in fe, J(fe))
    # AG5 — missão do PJ não é aceita por decisão do motor
    st_t = rpg.read_meta(G / "quests" / "quest-trama.md")["status"]
    agency("AG5.acao-de-npc", st_t == "disponivel", f"quest-trama: {st_t}")
    # AG6 — débito automático só do que foi autorizado; sem saldo: atraso; nada pela iniciativa de NPC
    E1 = jload(G, "sim/economy.json")
    R = jload(G, "sim/resources.json")
    routes = {r["id"]: r for r in E1["routes"]}
    paid = {d["entry"]: d["paid"] for d in E1.get("_bills", {}).values()}
    check("AG6.conta-autorizada-debitada", paid.get("conta-luz") == 50 and routes["conta-luz"].get("arrears", 0) == 0, J(paid))
    check("AG6.sem-saldo-vira-atraso", paid.get("divida") == 0 and routes["divida"]["arrears"] == 10000
          and all(v >= 0 for h in R["holdings"].values() for v in h.values()), J(R["holdings"]))
    check("AG6.consumidor-autorizado-debitado", len(gk(G, "econ:assinatura", "processado")) == 1)
    check("AG6.entrada-sem-autorizacao-nao-debita-nem-atrasa", "antiga" not in paid and not routes["antiga"].get("arrears")
          and any(sim.is_permanent(e) for e in gk(G, "econ:antiga")), J(routes["antiga"]))
    check("AG6.sem-nova-divida-nem-decisao", sorted(routes) == sorted(r["id"] for r in E0["routes"])
          and [x["id"] for x in E1.get("consumers", [])] == ["assinatura"] and not gk(G, "econ:pay:")
          and len(gk(G, "atraso:")) == 1, "")
    agency("AG6.acao-de-npc-nao-paga-pelo-pj", R["holdings"].get("npc-cobrador", {}).get("dinheiro", 0) == 0
           and R["holdings"]["pc-ana"]["dinheiro"] == 500 - 50 - 1, f"cobrador {R['holdings'].get('npc-cobrador')}; pc-ana {R['holdings'].get('pc-ana')}")
    bad, free = blocked(G)
    check("AG3-6.falhas-permanentes-sem-travar", len(bad) == 4 and free, str(sorted(e.get("type") for e in bad)))
    # AG7 — presença só para ocorrência passada e declarada; sentimento do PJ só declarado
    rc, out = sh("life", "attend", "cmp-festa", "presente", "--at", "2026/11/20 20:00", "--op", "fut", camp=M)
    check("AG7.ocorrencia-inexistente-ou-futura-recusada", rc != 0, out[-120:])
    rc, out = sh("life", "attend", "cmp-trab", "presente", camp="longa")
    check("AG7.presenca-exige-op", rc != 0 and "--op" in out, out[-120:])
    rc, out = sh("world", "feel", "pc-ana", "npc-lia", "afeto", "+1", "--reason", "x", "--op", "nd", camp=M)
    agency("AG7.feel-do-pj-sem-declaracao-recusado", rc != 0 and "declara" in out, out[-120:])
finally:
    shutil.rmtree(tmp)
    if sys.exc_info()[0]:
        for c, s, d in RESULTS:
            print(f"{s:5} {c}" + (f"  — {d}" if d and s != "OK" else ""))
        print("INTERROMPIDO: uma etapa de preparação falhou (veja o erro abaixo)")

for c, s, d in RESULTS:
    print(f"{s:5} {c}" + (f"  — {d}" if d and s != "OK" else ""))
n = {k: sum(1 for _, s, _ in RESULTS if s == k) for k in ("OK", "FALHA", "VIOLA", "INFO")}
print(f"\n{n['OK']} ok, {n['FALHA']} falha(s), {n['VIOLA']} violação(ões) de agência, {n['INFO']} informativo(s)")
if n["FALHA"] or n["VIOLA"]:
    sys.exit(1)
print("OK: campanha longa e agência do protagonista (Fase 3.2) passaram")
