#!/usr/bin/env python3
"""Testes da Fase 3.1 (preferências, calendário, vida cotidiana, relações pessoais, economia pessoal), em raiz temporária."""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
import rpg, sim  # noqa: E402

env = {**os.environ, "RPG_ROOT": str(tmp)}
J = json.dumps


def run(*args, ok=True, camp=None):
    args = list(args) + (["--campaign", camp] if camp else [])
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def evs(C):
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def ent(C, eid, **meta):
    sub = {"pc": "characters", "npc": "npcs", "loc": "locations", "quest": "quests"}[eid.split("-")[0]]
    extra = meta.pop("_body", "")
    body = "\n".join(f"{k}: {v}" for k, v in {"id": eid, "name": eid.split("-", 1)[1].title(), **meta}.items())
    (C / sub / f"{eid}.md").write_text(f"---\n{body}\n---\n# {eid}\n\n## Conhecido pelos jogadores\n{extra}")


def tree_hash(d):
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(d)).encode() + p.read_bytes())
    return h.hexdigest()


def bal(C, who, res="dinheiro"):
    return sim.balance(C, who, res)


def L(C):
    return json.loads((C / "state" / "sim" / "life.json").read_text())


def E(C):
    return json.loads((C / "state" / "sim" / "economy.json").read_text())


def feels(C):
    return sim.feelings(sim.read_jsonl(C / "state" / "relations.jsonl"))


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    TERRA = str(tmp / "templates" / "calendars" / "terrestre.json")

    # ================================================================ 1. configuração com vários estilos
    assert "não reconhecido" in run("new", "ruim", "--name", "R", "--system", "aventura-2d6", "--styles", "vida cotidiana, xadrez", ok=False)
    assert "equilibrado" in run("new", "ruim", "--name", "R", "--system", "aventura-2d6", "--styles", "equilibrado, romance", ok=False)
    assert not (tmp / "campaigns" / "ruim").exists()  # inválido não cria nada
    run("new", "vida", "--name", "Vida", "--system", "aventura-2d6", "--genre", "drama urbano", "--tone", "leve",
        "--setting", "Moderna", "--styles", "Vida cotidiana, romance, Mistério, romance", "--depth", "equilibrado",
        "--pace", "calmo", "--autonomy", "Baixa")
    C = tmp / "campaigns" / "vida"
    m = rpg.read_meta(C / "campaign.yaml")
    assert m["setting"] == "moderna" and m["styles"] == "vida-cotidiana, romance, investigacao", m
    assert m["depth"] == "equilibrada" and m["pace"] == "calmo" and m["autonomy"] == "baixa" and m["genre"] == "drama urbano"
    assert m["economy"] == "off"  # nada é ligado na criação
    out = run("style", "show")
    assert "vida-cotidiana, romance, investigacao" in out and "(padrão)" not in out
    run("check")

    # padrões numa campanha sem preferências informadas
    run("new", "simples", "--name", "S", "--system", "aventura-2d6")
    assert rpg.prefs(tmp / "campaigns" / "simples") == {"setting": None, "styles": "equilibrado", "depth": "equilibrada",
                                                         "pace": "equilibrado", "autonomy": "equilibrada"}
    run("use", "vida")

    # ================================================================ 2. alteração de preferências: auditável, sem duplicar, recuperável
    run("world", "event-add", "Ana chega à cidade", "--date", "antes de tudo")  # histórico anterior (Fase 2, sem relógio)
    ev_before = (C / "state" / "events.jsonl").read_text()
    out = run("style", "set", "--styles", "investigacao, amizades", "--reason", "o mistério ganhou força")
    assert "investigacao, amizades" in rpg.read_meta(C / "campaign.yaml")["styles"]
    assert "Preferências sem mudanças" in run("style", "set", "--styles", "investigação,amizade")  # sinônimos → mesmo valor
    pe = [e for e in evs(C) if e.get("type") == "preferencias"]
    assert len(pe) == 1 and pe[0]["result"]["de"] == {"styles": "vida-cotidiana, romance, investigacao"}
    assert pe[0]["result"]["motivo"] == "o mistério ganhou força" and pe[0]["date"] == "(sem relógio)"
    assert (C / "state" / "events.jsonl").read_text().startswith(ev_before)  # passado intacto (append-only)
    assert "não reconhecido" in run("style", "set", "--depth", "abissal", ok=False)

    # queda entre o evento e a gravação do campaign.yaml → repetir conclui sem evento duplicado
    real_set_key = rpg.set_key

    def boom(*a, **k):
        raise RuntimeError("queda simulada")
    rpg.set_key = boom
    try:
        rpg.main(["style", "set", "--pace", "intenso", "--reason", "virada"])
        raise AssertionError("deveria ter caído")
    except RuntimeError:
        pass
    rpg.set_key = real_set_key
    assert rpg.prefs(C)["pace"] == "calmo" and len([e for e in evs(C) if e.get("type") == "preferencias"]) == 2
    run("style", "set", "--pace", "intenso", "--reason", "virada")
    assert rpg.prefs(C)["pace"] == "intenso" and len([e for e in evs(C) if e.get("type") == "preferencias"]) == 2
    # ida e volta continuam auditadas (cada mudança real = 1 evento)
    run("style", "set", "--pace", "calmo"); run("style", "set", "--pace", "intenso")
    assert len([e for e in evs(C) if e.get("type") == "preferencias"]) == 4
    # valor inválido editado à mão: check acusa, style show não quebra
    y = (C / "campaign.yaml").read_text()
    (C / "campaign.yaml").write_text(y.replace("autonomy: baixa", "autonomy: total"))
    assert "autonomy" in run("check", ok=False)
    (C / "campaign.yaml").write_text(y)

    # ================================================================ 3. calendário
    cal = sim.validate_calendar(json.loads(Path(TERRA).read_text()))
    assert sim.parse_when("2028/2/29", cal, 0) and sim.weekday(sim.parse_when("2028/2/29", cal, 0), cal) == "terça"
    for bad in ("2027/2/29", "2026/4/31"):
        try:
            sim.parse_when(bad, cal, 0)
            raise AssertionError(bad)
        except sim.SimError:
            pass
    for broken in ({**cal, "month_days": [30] * 11}, {**cal, "leap": "juliano"}, {**cal, "rest_days": ["feriadão"]},
                   {**cal, "holidays": [{"date": "x"}]}, {**cal, "week": ["a", "a"]}):
        try:
            sim.validate_calendar(dict(broken))
            raise AssertionError(broken)
        except sim.SimError:
            pass
    out = run("time", "init", "--calendar", TERRA, "--start", "2026/10/12 08:00", "--seed", "11")
    assert "segunda, Dia 12 de outubro, 2026, 08:00" in out, out
    now0 = sim.load_clock(C)["now"]
    assert sim.holiday(now0, cal) == "Nossa Senhora Aparecida" and sim.is_rest(now0, cal)
    # recorrência determinística: mensal no dia 31 cai no último dia dos meses curtos; mesma janela → mesmos instantes
    occ = list(sim.occurrences(now0, now0 + 120 * 1440, "10:00", cal, monthday=31))
    assert [sim.ymd(t, cal)[1:3] for t in occ] == [(10, 31), (11, 30), (12, 31), (1, 31)]
    assert occ == list(sim.occurrences(now0, now0 + 120 * 1440, "10:00", cal, monthday=31))
    assert len(list(sim.occurrences(now0, now0 + 7 * 1440, "09:00", cal, ["segunda", "sexta"]))) == 2  # seg 12 e sex 16
    assert len(list(sim.occurrences(now0, now0 + 7 * 1440, "09:00", cal, ["segunda", "sexta"], skip_rest=True))) == 1  # 12/10 é feriado

    # campanha com calendário fictício: semana de 5 dias, meses desiguais, sem semana no início
    run("new", "cal", "--name", "Cal", "--system", "aventura-2d6", "--setting", "mundo original")
    CC = tmp / "campaigns" / "cal"
    run("time", "init", "--start", "1/1/1 06:00", camp="cal")
    run("time", "schedule", "evento antigo", "--in", "1h", camp="cal")
    run("time", "advance", "2h", "--op", "c1", "--reason", "x", camp="cal")
    old_dates = [e["date"] for e in evs(CC)]
    assert "dias da semana" not in run("time", "now", camp="cal")
    run("time", "calendar", "--week", "lua,fogo,água,terra,vento", camp="cal", ok=False)  # exige --today
    out = run("time", "calendar", "--week", "lua,fogo,água,terra,vento", "--today", "terra", "--rest", "vento",
              "--holiday", "1/3=Dia da Fundação", camp="cal")
    assert "terra, Dia 1 de Mês 1" in out, out
    assert "Calendário sem mudanças" in run("time", "calendar", "--rest", "vento", "--holiday", "1/3=Dia da Fundação", camp="cal")
    assert "trocá-la" in run("time", "calendar", "--week", "a,b,c", "--today", "a", camp="cal", ok=False)
    c2 = sim.load_clock(CC)
    assert c2["calendar"]["days_per_month"] == 30 and c2["calendar"]["months"] == sim.DEFAULT_CAL["months"]
    assert [e["date"] for e in evs(CC)][:len(old_dates)] == old_dates  # datas passadas não foram reinterpretadas
    assert len([e for e in evs(CC) if e.get("type") == "calendario"]) == 1
    t = c2["now"]
    assert [sim.weekday(t + i * 1440, c2["calendar"]) for i in range(6)] == ["terra", "vento", "lua", "fogo", "água", "terra"]
    assert sim.holiday(t + 2 * 1440, c2["calendar"]) == "Dia da Fundação" and sim.holiday(t + 1440, c2["calendar"]) is None
    assert sim.is_rest(t + 1440, c2["calendar"]) and not sim.is_rest(t, c2["calendar"])
    fict = {"minutes_per_hour": 60, "hours_per_day": 20, "months": ["Gelo", "Sol"], "month_days": [10, 25], "epoch_year": 900,
            "week": ["um", "dois", "três"]}
    (tmp / "fict.json").write_text(J(fict))
    run("new", "fict", "--name", "F", "--system", "aventura-2d6")
    out = run("time", "init", "--calendar", str(tmp / "fict.json"), "--start", "900/2/25 19:00", "--today", "dois", camp="fict")
    assert "dois, Dia 25 de Sol, Ano 900, 19:00" in out, out
    assert "dois dias" not in out and "25 de Sol" in run("time", "now", camp="fict")
    run("time", "init", "--start", "900/1/11 00:00", "--calendar", str(tmp / "fict.json"), camp="simples", ok=False)  # 11 > 10

    # ================================================================ 4. mundo da campanha 'vida'
    run("use", "vida")
    for lid in ("loc-casa", "loc-loja", "loc-escola", "loc-bar", "loc-delegacia", "loc-apto"):
        ent(C, lid)
    ent(C, "pc-ana", location="loc-casa", home="loc-casa", _body="Ana")
    ent(C, "npc-chefe", status="vivo", location="loc-loja")
    ent(C, "npc-lia", status="vivo", location="loc-casa")
    ent(C, "npc-senhorio", status="vivo", location="loc-casa")
    ent(C, "npc-detetive", status="vivo", location="loc-bar")
    run("check")

    # ================================================================ 5. relações pessoais direcionais
    run("world", "rel-add", "npc-lia", "pc-ana", "admira", "--directed")
    run("world", "rel-add", "pc-ana", "npc-lia", "admira", "--directed")  # o sentido oposto é outra relação
    assert "já existe" in run("world", "rel-add", "npc-lia", "pc-ana", "admira", "--directed", ok=False)
    run("world", "rel-add", "npc-lia", "npc-chefe", "colegas")
    assert "já existe" in run("world", "rel-add", "npc-chefe", "npc-lia", "colegas", ok=False)  # sem direção: mesmo par
    n = len(evs(C))
    assert "declaração" in run("world", "feel", "pc-ana", "npc-lia", "afeto", "+10", "--reason", "x", "--op", "f0", ok=False)
    assert "desconhecida" in run("world", "feel", "npc-lia", "pc-ana", "ciume", "+10", "--reason", "x", "--op", "f0", ok=False)
    assert "não existe" in run("world", "feel", "npc-lia", "npc-ninguem", "afeto", "+10", "--reason", "x", "--op", "f0", ok=False)
    assert len(evs(C)) == n  # recusas não criam evento
    run("world", "feel", "npc-lia", "pc-ana", "afeto", "+30", "--reason", "Ana a ajudou na mudança", "--op", "f1")
    out = run("world", "feel", "npc-lia", "pc-ana", "afeto", "+30", "--reason", "Ana a ajudou na mudança", "--op", "f1")
    assert "já aplicado" in out
    run("world", "feel", "npc-lia", "pc-ana", "intimidade", "-20", "--reason", "x", "--op", "f2")  # 0..100: não fica negativo
    run("world", "feel", "npc-lia", "pc-ana", "admiracao", "+150", "--reason", "x", "--op", "f3")  # limitado a 100
    run("world", "feel", "npc-lia", "pc-ana", "admiracao", "-30", "--reason", "x", "--op", "f4")
    run("world", "feel", "pc-ana", "npc-lia", "confianca", "+5", "--reason", "o jogador disse que confia", "--op", "f5", "--declared")
    F = feels(C)
    assert F[("npc-lia", "pc-ana")] == {"afeto": 30, "intimidade": 0, "admiracao": 70}, F
    assert F[("pc-ana", "npc-lia")] == {"confianca": 5}  # nunca recíproco
    with open(C / "state" / "relations.jsonl", "a") as f:  # registro de uma versão futura (dimensão nova) é preservado
        f.write(J({"op": "feel", "from": "npc-lia", "to": "pc-ana", "dim": "saudade", "delta": 7, "key": "futuro"}) + "\n")
    assert feels(C)[("npc-lia", "pc-ana")]["saudade"] == 7
    out = run("world", "rel", "pc-ana")
    assert "npc-lia ⇢ pc-ana" in out and "afeto +30" in out and "pc-ana ⇢ npc-lia" in out and "[MESTRE]" in out
    pub = run("world", "rel", "pc-ana", "--public")
    assert "⇢" not in pub and "afeto" not in pub and "[MESTRE]" not in pub
    assert "f1" not in run("world", "timeline", "--public") and "Ana a ajudou" not in run("world", "timeline", "--public")
    assert "relation.feel" in run("world", "why", "pc-ana") or "afeto" in run("world", "why", "pc-ana")

    # ================================================================ 6. economia pessoal (opt-in explícito)
    assert "desligada" in run("econ", "open", "pc-ana", "100", "--reason", "x", "--op", "o0", ok=False)
    assert not (C / "state" / "sim" / "resources.json").exists()
    rpg.set_key(C / "campaign.yaml", "economy", "on")
    # a ficha com saldo numérico concorrente é acusada pelo verificador
    pc = C / "characters" / "pc-ana.md"
    pc.write_text(pc.read_text() + "\n## Dinheiro\n- 100 reais\n")
    assert "Dinheiro" in run("world", "verify")
    run("econ", "open", "pc-ana", "100", "--reason", "saldo da ficha", "--op", "o1")
    assert "já aplicado" in run("econ", "open", "pc-ana", "100", "--reason", "saldo da ficha", "--op", "o1")
    assert "já tem saldo" in run("econ", "open", "pc-ana", "50", "--reason", "de novo", "--op", "o2", ok=False)
    pc.write_text(pc.read_text().replace("- 100 reais", "- saldo: `rpg.py res pc-ana`"))
    run("econ", "open", "npc-chefe", "5000", "--reason", "caixa da livraria", "--op", "o3")
    assert bal(C, "pc-ana") == 100

    # ================================================================ 7. trabalho, compromissos e lembretes
    cut = J([{"type": "relation.feel", "from": "npc-chefe", "to": "pc-ana", "dim": "confianca", "delta": -10, "reason": "faltou"}])
    ok_ = J([{"type": "relation.feel", "from": "npc-chefe", "to": "pc-ana", "dim": "respeito", "delta": 2, "reason": "pontual"}])
    assert "não existe" in run("life", "job", "cmp-trabalho", "--who", "pc-ana", "--text", "x", "--employer", "npc-chefe",
                               "--loc", "loc-sumida", "--at", "09:00", "--op", "j0", ok=False)
    run("life", "job", "cmp-trabalho", "--who", "pc-ana", "--text", "Atendente na livraria", "--employer", "npc-chefe",
        "--loc", "loc-loja", "--at", "09:00", "--days", "segunda,terça,quarta,quinta,sexta", "--skip-rest", "--duration", "8h",
        "--salary", "300", "--pay-days", "sexta", "--pay-at", "18:00", "--on-miss", cut, "--on-attend", ok_,
        "--reason", "o jogador aceitou a vaga", "--op", "j1")
    assert "outros dados" in run("life", "job", "cmp-trabalho", "--who", "pc-ana", "--text", "Atendente na livraria",
                                 "--employer", "npc-chefe", "--loc", "loc-loja", "--at", "09:00", "--days", "segunda", "--op", "j1", ok=False)
    assert [r["id"] for r in E(C)["routes"]] == ["sal-trabalho"] and L(C)["commitments"]["cmp-trabalho"]["route"] == "sal-trabalho"
    run("life", "add", "cmp-corrida", "--who", "pc-ana", "--kind", "lazer", "--text", "Corrida no parque", "--at", "06:30", "--op", "a1")
    run("life", "add", "cmp-cinema", "--who", "pc-ana", "--kind", "lazer", "--text", "Estreia no cinema", "--important",
        "--once", "2026/10/16 21:00", "--op", "a2")
    run("life", "add", "cmp-jantar", "--who", "pc-ana", "--kind", "social", "--text", "Jantar com Lia", "--with", "npc-lia",
        "--move-with", "--loc", "loc-bar", "--once", "2026/10/14 20:00", "--op", "a3")
    run("life", "add", "cmp-segredo", "--who", "npc-detetive", "--kind", "trabalho", "--text", "Vigia Ana", "--hidden",
        "--at", "22:00", "--op", "a4")
    agenda = run("life", "agenda", "--public")
    assert "Atendente" in agenda and "Jantar" in agenda and "Vigia" not in agenda and "relation" not in agenda
    assert "segunda, Dia 12" not in agenda.split("Atendente")[0][-40:]  # feriado: sem expediente
    assert "(resumido)" in run("life", "agenda")  # corrida (rotineira) não vira lembrete em 'equilibrada'

    run("time", "advance", "5d", "--op", "sem1", "--reason", "primeira semana")  # → sábado 17/10 08:00
    lemb = [e for e in evs(C) if e.get("type") == "compromisso"]
    by = lambda cid: [e for e in lemb if e["gen_key"].startswith(f"life:{cid}:")]  # noqa: E731
    assert len(by("cmp-trabalho")) == 4, [e["date"] for e in by("cmp-trabalho")]  # ter–sex (segunda é feriado)
    assert not by("cmp-corrida") and len(by("cmp-cinema")) == 1 and len(by("cmp-jantar")) == 1
    assert by("cmp-segredo") and by("cmp-segredo")[0]["hidden"]
    meta = lambda eid: rpg.read_meta(C / ("characters" if eid.startswith("pc-") else "npcs") / f"{eid}.md")  # noqa: E731
    assert meta("pc-ana")["location"] == "loc-casa"  # o motor nunca move o protagonista
    assert meta("npc-lia")["location"] == "loc-bar"  # NPC do jantar foi ao local
    assert bal(C, "pc-ana") == 400 and bal(C, "npc-chefe") == 4700  # um salário (sexta 18h)
    # nenhuma presença foi presumida
    assert not L(C)["attendance"] and ("npc-chefe", "pc-ana") not in feels(C)

    # repetir o processamento de todos os ciclos econômicos e lembretes não duplica nada
    w = sim.World(C)
    sim.load_modules()
    for e in [e for e in evs(C) if e.get("type") in ("economia", "compromisso")]:
        sim.process_event(w, dict(e, status="processando"))
    assert bal(C, "pc-ana") == 400 and bal(C, "npc-chefe") == 4700

    # ================================================================ 8. presença declarada pelo jogador
    out = run("life", "attend", "cmp-trabalho", "presente", "--op", "p1")  # padrão: ocorrência mais recente (sexta)
    assert "sexta, Dia 16" in out and "1 consequência" in out, out
    assert "já aplicado" in run("life", "attend", "cmp-trabalho", "presente", "--op", "p1")
    assert "já registrada" in run("life", "attend", "cmp-trabalho", "faltou", "--op", "p2", ok=False)
    run("life", "attend", "cmp-trabalho", "faltou", "--at", "2026/10/15 09:00", "--note", "jogador disse que dormiu", "--op", "p3")
    assert "não tem ocorrência" in run("life", "attend", "cmp-trabalho", "faltou", "--at", "2026/10/15 10:00", "--op", "p4", ok=False)
    assert "não tem ocorrência" in run("life", "attend", "cmp-trabalho", "faltou", "--at", "2026/10/12 09:00", "--op", "p4", ok=False)
    assert "futura" in run("life", "attend", "cmp-trabalho", "presente", "--at", "2026/10/19 09:00", "--op", "p5", ok=False)
    assert feels(C)[("npc-chefe", "pc-ana")] == {"respeito": 2, "confianca": -10}
    assert sorted(v["status"] for v in L(C)["attendance"].values()) == ["faltou", "presente"]  # quarta e terça: sem registro
    att = next(e for e in evs(C) if e.get("type") == "compromisso.presenca")
    assert att["cause"] == by("cmp-trabalho")[-1]["id"]  # causa = o lembrete da ocorrência

    # prova de curso: consequência que falha no meio é retomada sem repetir a parte já aplicada
    ent(C, "quest-curso", status="em_andamento", type="pessoal", horizon="medio")
    run("quest", "define", "quest-curso", "--json", J({"objectives": [{"id": "prova", "desc": "Passar na prova"}]}))
    run("life", "course", "cmp-ingles", "--who", "pc-ana", "--text", "Curso de inglês", "--school", "loc-escola",
        "--loc", "loc-escola", "--at", "19:00", "--days", "terça,quinta", "--fee", "120", "--fee-monthday", "20", "--authorized", "jogador: mensalidade em débito automático", "--op", "c1")
    prova_ok = J([{"type": "quest.objective", "quest": "quest-curso", "objective": "prova", "reason": "foi à prova"},
                  {"type": "relation.feel", "from": "npc-professora", "to": "pc-ana", "dim": "respeito", "delta": 5}])
    prova_args = ("life", "add", "cmp-prova", "--who", "pc-ana", "--kind", "prova", "--text", "Prova de inglês", "--loc", "loc-escola",
                  "--once", "2026/10/17 09:00", "--on-attend", prova_ok, "--op", "c2")
    assert "npc-professora" in run(*prova_args, ok=False)  # consequência com referência inexistente: recusada antes
    ent(C, "npc-professora", status="vivo", location="loc-escola")
    run(*prova_args)
    run("time", "advance", "2h", "--op", "prova", "--reason", "prova")
    prof = C / "npcs" / "npc-professora.md"
    prof.rename(tmp / "npc-professora.md")  # dano manual depois de validado: falha RECUPERÁVEL
    assert "falhou" in run("life", "attend", "cmp-prova", "presente", "--op", "p6", ok=False)
    (tmp / "npc-professora.md").rename(prof)
    run("life", "attend", "cmp-prova", "presente", "--op", "p6")  # mesma op: conclui
    q = json.loads((C / "state" / "sim" / "quests.json").read_text())
    assert q["quests"]["quest-curso"]["manual"]["prova"]["done"] and feels(C)[("npc-professora", "pc-ana")] == {"respeito": 5}
    assert len([k for k in L(C)["attendance"] if k.startswith("cmp-prova@")]) == 1
    assert len([r for r in sim.read_jsonl(C / "state" / "relations.jsonl") if r.get("from") == "npc-professora"]) == 1

    # ================================================================ 9. contas: parcial, insuficiente, atraso, consequência única, falha
    routes = E(C)["routes"] + [
        {"id": "aluguel", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "authorized": "jogador: débito automático autorizado", "qty": 500, "bill": True, "partial": True,
         "when": {"at": "10:00", "monthday": 19},
         "on_arrears": [{"type": "relation.feel", "from": "npc-senhorio", "to": "pc-ana", "dim": "confianca", "delta": -15},
                        {"type": "flag.set", "name": "aluguel_atrasado", "value": True}]},
        {"id": "internet", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "authorized": "jogador: débito automático autorizado", "qty": 80, "bill": True,
         "when": {"at": "10:00", "monthday": 19},
         "on_arrears": [{"type": "relation.feel", "from": "npc-provedor", "to": "pc-ana", "dim": "confianca", "delta": -5}]}]
    assert "só valem em rotas" in run("econ", "set", "--json", J({"consumers": [{"id": "x", "who": "pc-ana", "res": "d", "qty": 1,
                                                                          "every": "1d", "bill": True}]}), ok=False)
    assert "npc-provedor" in run("econ", "set", "--json", J({"routes": routes}), ok=False)  # referência validada antes
    ent(C, "npc-provedor", status="vivo", location="loc-loja")
    run("econ", "set", "--json", J({"routes": routes}))
    assert bal(C, "pc-ana") == 400
    prov = C / "npcs" / "npc-provedor.md"
    prov.rename(tmp / "npc-provedor.md")  # dano manual depois de validado: falha RECUPERÁVEL no meio do avanço
    out = run("time", "advance", "2d", "--op", "dia19", "--reason", "fim de semana", ok=False)
    assert "falhou" in out and "time resume" in out and sim.load_clock(C).get("pending_op")
    (tmp / "npc-provedor.md").rename(prov)
    run("time", "resume")
    run("time", "advance", "1d", "--op", "dia19b", "--reason", "segunda")  # → segunda 19/10 após 10:00
    assert bal(C, "pc-ana") == 0 and bal(C, "npc-senhorio") == 400  # parcial: pagou 400 de 500; internet: nada
    rr = {r["id"]: r for r in E(C)["routes"]}
    assert rr["aluguel"]["arrears"] == 100 and rr["internet"]["arrears"] == 80
    atr = [e for e in evs(C) if (e.get("gen_key") or "").startswith("atraso:")]
    assert len(atr) == 3 and all(e["hidden"] for e in atr)  # aluguel, internet e a mensalidade do curso (dia 20)
    assert rr["mens-ingles"]["arrears"] == 120
    assert feels(C)[("npc-senhorio", "pc-ana")] == {"confianca": -15} and feels(C)[("npc-provedor", "pc-ana")] == {"confianca": -5}
    w = sim.World(C)
    for e in [e for e in evs(C) if e.get("type") == "economia"]:
        sim.process_event(w, dict(e, status="processando"))
    rr = {r["id"]: r for r in E(C)["routes"]}
    assert bal(C, "pc-ana") == 0 and rr["aluguel"]["arrears"] == 100 and rr["internet"]["arrears"] == 80
    assert feels(C)[("npc-senhorio", "pc-ana")] == {"confianca": -15}  # consequência única por ocorrência
    assert len([e for e in evs(C) if (e.get("gen_key") or "").startswith("atraso:")]) == 3

    # quitação: valida antes (sem evento), idempotente
    n = len(evs(C))
    assert "não pode pagar" in run("econ", "pay", "pc-ana", "npc-senhorio", "50", "--route", "aluguel", "--reason", "x", "--op", "q0", ok=False)
    run("econ", "pay", "npc-chefe", "pc-ana", "250", "--reason", "adiantamento", "--op", "adiant")
    assert "não dá para quitar" in run("econ", "pay", "pc-ana", "npc-senhorio", "150", "--route", "aluguel", "--reason", "x", "--op", "q1", ok=False)
    assert "não é uma conta" in run("econ", "pay", "pc-ana", "npc-chefe", "10", "--route", "aluguel", "--reason", "x", "--op", "q1", ok=False)
    assert len(evs(C)) == n + 1
    run("econ", "pay", "pc-ana", "npc-senhorio", "100", "--route", "aluguel", "--reason", "quitou o aluguel", "--op", "q2")
    assert "já aplicado" in run("econ", "pay", "pc-ana", "npc-senhorio", "100", "--route", "aluguel", "--reason", "quitou o aluguel", "--op", "q2")
    assert bal(C, "pc-ana") == 150 and {r["id"]: r for r in E(C)["routes"]}["aluguel"]["arrears"] == 0
    ledger = run("econ", "ledger", "pc-ana")
    assert "adiantamento" in ledger and "quitou o aluguel" in ledger

    # ================================================================ 10. demissão interrompe a rota; reativação retoma
    run("life", "end", "cmp-trabalho", "--reason", "pediu demissão", "--op", "e1")
    assert "já aplicado" in run("life", "end", "cmp-trabalho", "--reason", "pediu demissão", "--op", "e1")
    assert "outros dados" in run("life", "end", "cmp-trabalho", "--reason", "x", "--op", "e1", ok=False)
    assert "já está" in run("life", "end", "cmp-trabalho", "--reason", "x", "--op", "e2", ok=False)
    assert {r["id"]: r for r in E(C)["routes"]}["sal-trabalho"]["status"] == "interrompida"
    chefe = bal(C, "npc-chefe")
    n_trab = len([e for e in evs(C) if (e.get("gen_key") or "").startswith("life:cmp-trabalho:")])
    run("time", "advance", "5d", "--op", "sem2", "--reason", "semana sem emprego")  # cruza sexta 23/10 18:00
    assert bal(C, "npc-chefe") == chefe
    assert len([e for e in evs(C) if (e.get("gen_key") or "").startswith("life:cmp-trabalho:")]) == n_trab
    run("life", "end", "cmp-trabalho", "--reactivate", "--reason", "foi recontratada", "--op", "e3")
    assert {r["id"]: r for r in E(C)["routes"]}["sal-trabalho"]["status"] == "ativa"
    run("time", "advance", "7d", "--op", "sem3", "--reason", "semana de volta")
    assert bal(C, "npc-chefe") == chefe - 300  # exatamente um salário na semana

    # ================================================================ 11. profundidade muda só o detalhe da rotina
    run("style", "set", "--depth", "leve", "--reason", "jogador quer menos rotina")
    k0 = {e["gen_key"] for e in evs(C) if e.get("type") == "compromisso"}
    run("life", "add", "cmp-entrevista", "--who", "pc-ana", "--kind", "entrevista", "--text", "Entrevista", "--once", "+1d", "--op", "a5")
    sal0 = len([e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:sal-trabalho")])
    run("time", "advance", "7d", "--op", "leve", "--reason", "x")
    new = {e["gen_key"] for e in evs(C) if e.get("type") == "compromisso"} - k0
    assert any(k.startswith("life:cmp-entrevista:") for k in new)  # importante/entrevista sempre
    assert not any(k.startswith(("life:cmp-trabalho:", "life:cmp-corrida:")) for k in new), new
    # a economia não é "rotina": o salário continua exatamente uma vez por semana em qualquer profundidade
    assert len([e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:sal-trabalho")]) == sal0 + 1
    run("style", "set", "--depth", "profunda", "--reason", "x")
    run("time", "advance", "1d", "--op", "prof", "--reason", "x")
    assert any((e.get("gen_key") or "").startswith("life:cmp-corrida:") for e in evs(C))

    # ================================================================ 12. objetivos pessoais: concluir e substituir
    ent(C, "quest-poupar", status="em_andamento", type="pessoal", horizon="curto")
    run("quest", "define", "quest-poupar", "--json", J({"auto_complete": True, "objectives": [
        {"id": "juntar", "desc": "Juntar 2000", "done_conditions": [{"type": "resource.gte", "who": "pc-ana", "res": "dinheiro", "qty": 2000}]}]}))
    ent(C, "quest-mudar", status="em_andamento", type="pessoal", horizon="longo")
    run("quest", "set-status", "quest-mudar", "cancelada", "--reason", "substituída por quest-apto", "--op", "sub1")
    sub = next(e for e in evs(C) if e.get("quest") == "quest-mudar" and e.get("type") == "missao")
    ent(C, "quest-apto", status="em_andamento", type="pessoal", horizon="medio")
    run("quest", "define", "quest-apto", "--json", J({"auto_complete": True, "objectives": [
        {"id": "mudou", "desc": "Morar no apartamento", "done_conditions": [{"type": "entity", "id": "pc-ana", "field": "home", "eq": "loc-apto"}]}]}))
    run("econ", "pay", "npc-chefe", "pc-ana", "2000", "--reason", "bônus", "--op", "bonus")
    run("time", "schedule", "Ana assina o contrato", "--in", "1h", "--cause", sub["id"], "--effects",
        J({"type": "entity.set", "id": "pc-ana", "field": "home", "value": "loc-apto"}))
    run("time", "advance", "2h", "--op", "mudanca", "--reason", "x")
    st = lambda q: rpg.read_meta(C / "quests" / f"{q}.md")["status"]  # noqa: E731
    assert st("quest-poupar") == "concluida" and st("quest-mudar") == "cancelada" and st("quest-apto") == "concluida"
    assert meta("pc-ana")["home"] == "loc-apto" and meta("pc-ana")["location"] == "loc-casa"
    assert "mora_em" in run("world", "rel", "pc-ana")

    # ================================================================ 13. privacidade
    pub = run("world", "timeline", "--public")
    for secret in ("Vigia Ana", "Atraso", "Ciclo econômico", "⇢", "relation.feel", "npc.decis"):
        assert secret not in pub, (secret, pub)
    assert "começa a trabalhar" in pub  # o que o jogador fez/sabe aparece
    assert "Vigia" not in run("life", "agenda", "--public", "--span", "3d")
    assert "Vigia" in run("life", "agenda", "--span", "3d")

    # ================================================================ 14. verificador íntegro
    out = run("world", "verify")
    assert "OK: nenhuma inconsistência" in out, out
    run("check")

    # ================================================================ 15. estilo secundário com causa plausível acontece; estilos não mudam o motor
    def scenario(cid, styles):
        run("new", cid, "--name", cid, "--system", "aventura-2d6", "--setting", "moderna", "--styles", styles)
        D = tmp / "campaigns" / cid
        for lid in ("loc-bar", "loc-delegacia"):
            ent(D, lid)
        ent(D, "pc-ana", location="loc-bar")
        ent(D, "npc-detetive", status="vivo", location="loc-bar")
        run("time", "init", "--calendar", TERRA, "--start", "2026/10/13 20:00", "--seed", "5", camp=cid)
        run("fact", "add", "fact-roubo", "Alguém roubou o caixa do bar", camp=cid)
        run("npc", "set", "npc-detetive", "--tier", "2", "--goals", J([{"id": "dever", "priority": 3, "actions": [
            {"id": "investigar", "desc": "abrir investigação do roubo", "utility": 4,
             "preconditions": [{"type": "knows", "who": "npc-detetive", "fact": "fact-roubo"}],
             "effects": [{"type": "flag.set", "name": "investigacao_aberta", "value": True}]}]}]), camp=cid)
        run("time", "schedule", "O caixa some durante a noite", "--in", "1h", "--loc", "loc-bar", "--effects",
            J({"type": "knowledge.witness", "fact": "fact-roubo", "loc": "loc-bar"}), camp=cid)
        run("time", "advance", "1d", "--op", "noite", "--reason", "x", camp=cid)
        return D, [(e["type"], e["text"], e["due"], e["status"]) for e in evs(D)]
    D1, a = scenario("cotidiano", "vida-cotidiana")
    D2, b = scenario("acao", "combate, aventura")
    assert a == b  # mesmas causas → mesmos acontecimentos, qualquer que seja o foco
    assert sim.load_json(sim.flags_path(D1), {}).get("investigacao_aberta") is True  # investigação numa campanha de cotidiano
    assert sim.knows(D1, "pc-ana", "fact-roubo")  # a protagonista viu (estava no bar)

    # ================================================================ 16. compatibilidade: campanha antiga real (cópia), sem nada novo
    src = PROJ / "campaigns" / "teste-validacao"
    OLD = tmp / "campaigns" / "antiga"
    shutil.copytree(src, OLD)
    h0 = tree_hash(OLD)
    out = run("style", "show", camp="antiga")
    assert "não definida — gênero: fantasia sombria" in out and "equilibrado  (padrão)" in out, out
    run("check", camp="antiga")
    run("world", "verify", camp="antiga")
    run("world", "rel", "npc-bento-quaresma", camp="antiga")
    assert "não iniciado" in run("life", "agenda", camp="antiga", ok=False)
    assert "desligada" in run("econ", "status", camp="antiga")
    assert tree_hash(OLD) == h0  # nenhuma leitura reescreveu a campanha antiga
    assert tree_hash(src) == tree_hash(OLD)  # e a original não foi tocada
finally:
    shutil.rmtree(tmp)
print("OK: testes da Fase 3.1 (vida cotidiana, preferências, calendário, relações, economia pessoal) passaram")
