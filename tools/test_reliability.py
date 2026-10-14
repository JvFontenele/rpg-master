#!/usr/bin/env python3
"""Fase 3.1.1 — confiabilidade, em raiz temporária: privacidade (A1), validação prévia (A2), integridade econômica (A3),
falhas permanentes × recuperáveis, idempotência de --op, causalidade e eventos parcialmente aplicados.

Coleta TODAS as verificações (não para na primeira) para mostrar o que falha. Verificações em PENDING dependem de
etapas ainda não aprovadas: são mostradas, mas não reprovam a suíte."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
tmp = Path(tempfile.mkdtemp())
os.environ["RPG_ROOT"] = str(tmp)
import rpg, sim  # noqa: E402

env = {**os.environ, "RPG_ROOT": str(tmp)}
J = json.dumps
RESULTS = []
PENDING = set()  # verificações que dependem de etapas ainda não especificadas (nenhuma desde a etapa 5)


def check(cid, ok, detail=""):
    RESULTS.append((cid, bool(ok), detail))


def sh(*args, camp=None):
    args = [str(x) for x in args] + (["--campaign", camp] if camp else [])
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def must(*args, **k):
    rc, out = sh(*args, **k)
    assert rc == 0, f"preparação falhou: {args}\n{out}"
    return out


def ent(C, eid, known="", **meta):
    sub = {"pc": "characters", "npc": "npcs", "loc": "locations", "quest": "quests"}[eid.split("-")[0]]
    body = "\n".join(f"{k}: {v}" for k, v in {"id": eid, "name": eid.split("-", 1)[1].title(), **meta}.items())
    (C / sub / f"{eid}.md").write_text(f"---\n{body}\n---\n# {eid}\n\n## Conhecido pelos jogadores\n{known}\n")


def evs(C):
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def state(C):
    """Tudo que uma entrada inválida NÃO pode alterar."""
    return {p: (C / p).read_text() if (C / p).exists() else None for p in
            ("state/events.jsonl", "state/sim/economy.json", "state/sim/life.json", "state/sim/resources.json", "state/relations.jsonl")}


def unblock(C, why):
    """Só no comportamento ANTIGO: cancela eventos falhos (não permanentes) e retoma o avanço, para as próximas seções rodarem."""
    n = 0
    for e in evs(C):
        if e["status"] == "falhou" and not (e.get("result") or {}).get("permanente"):
            sh("time", "cancel", e["id"], "--reason", f"limpeza do teste ({why})")
            n += 1
    if (sim.load_clock(C) or {}).get("pending_op"):
        sh("time", "resume")
        n += 1
    if n:
        print(f"[limpeza] {why}: {n} desbloqueio(s) necessário(s)")


def routes(C):
    return {r["id"]: r for r in json.loads((C / "state" / "sim" / "economy.json").read_text()).get("routes", [])}


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    must("new", "r", "--name", "R", "--system", "aventura-2d6", "--setting", "moderna")
    C = tmp / "campaigns" / "r"
    must("time", "init", "--calendar", tmp / "templates" / "calendars" / "terrestre.json", "--start", "2026/10/12 08:00", "--seed", "3")
    rpg.set_key(C / "campaign.yaml", "economy", "on")
    ent(C, "pc-ana", "Protagonista", location="loc-casa")
    for nid, k in (("npc-chefe", "Dono da livraria"), ("npc-lia", "Vizinha"), ("npc-senhorio", ""), ("npc-provedor", "")):
        ent(C, nid, k, status="vivo", location="loc-casa")
    for lid, k in (("loc-loja", "Livraria"), ("loc-escola", ""), ("loc-casa", "Casa"), ("loc-bar", "Bar da esquina")):
        ent(C, lid, k)
    must("econ", "open", "pc-ana", "1000", "--reason", "saldo da ficha", "--op", "o1")
    must("econ", "open", "npc-chefe", "5000", "--reason", "caixa da livraria", "--op", "o2")
    must("econ", "open", "npc-lia", "200", "--reason", "economias da Lia", "--op", "o3")
    WEEK = "segunda,terça,quarta,quinta,sexta"

    def pub():
        return must("world", "timeline", "--public")

    # ============================================================ A1 — privacidade
    must("econ", "pay", "npc-chefe", "npc-lia", "50", "--reason", "suborno secreto", "--op", "n2n")
    must("econ", "pay", "pc-ana", "loc-loja", "30", "--reason", "livro de receitas", "--op", "compra")
    rc_h, _ = sh("econ", "pay", "pc-ana", "loc-bar", "10", "--reason", "aposta escondida", "--op", "ap", "--hidden")
    rc_p, _ = sh("econ", "pay", "npc-chefe", "npc-lia", "5", "--reason", "gorjeta anunciada", "--op", "gp", "--public")
    must("life", "add", "cmp-vigia", "--who", "npc-lia", "--kind", "trabalho", "--text", "Lia vigia a loja", "--at", "22:00", "--op", "v1")
    must("life", "job", "cmp-lia-trab", "--who", "npc-lia", "--text", "Lia no caixa", "--employer", "npc-chefe", "--at", "09:00",
         "--days", WEEK, "--salary", "100", "--pay-days", "sexta", "--op", "lj")
    must("life", "job", "cmp-ana-trab", "--who", "pc-ana", "--text", "Ana atendente", "--employer", "npc-chefe", "--loc", "loc-loja",
         "--at", "09:00", "--days", WEEK, "--skip-rest", "--salary", "300", "--pay-days", "sexta", "--pay-at", "18:00", "--op", "aj")
    rc_fp, _ = sh("life", "add", "cmp-festa", "--who", "npc-lia", "--kind", "evento", "--text", "Festa da rua",
                  "--once", "2026/10/13 20:00", "--public", "--op", "fp")
    rc_th, _ = sh("life", "add", "cmp-terapia", "--who", "pc-ana", "--kind", "saude", "--text", "Terapia sigilosa",
                  "--once", "2026/10/13 18:00", "--hidden", "--op", "th")
    must("time", "schedule", "Assalto planejado ao bar", "--in", "5d", "--loc", "loc-bar")
    must("time", "schedule", "Evento que será cancelado", "--in", "5d", "--loc", "loc-bar")
    must("time", "cancel", next(e["id"] for e in evs(C) if e.get("text") == "Evento que será cancelado"), "--reason", "x")
    must("map", "gen", "batalha", "map-b", "--name", "Bar", "--env", "interior", "--party", "pc-ana", "--enemies", "Bandido x1", "--seed", "2")
    must("map", "place", "map-b", "--name", "Balcão", "--kind", "ponto", "--ref", "loc-bar", "--at", "3,3")
    must("map", "reveal", "map-b", "map-b", "--status", "visitado")
    must("map", "reveal", "map-b", next(x["id"] for x in json.loads((C / "maps" / "map-b.json").read_text())["points"]
                                        if x.get("ref") == "loc-bar"), "--status", "visitado")
    must("time", "schedule", "Briga no bar ontem", "--in", "1h", "--loc", "loc-bar")
    must("time", "advance", "2d", "--op", "a1", "--reason", "dois dias")  # → quarta 14/10 08:00
    must("map", "render")
    P = pub()
    check("A1.open-npc-oculto", "Saldo inicial de npc-chefe" not in P)
    check("A1.open-pc-oculto", "Saldo inicial de pc-ana" not in P)
    check("A1.pay-npc-oculto", "suborno secreto" not in P)
    check("A1.pay-pc-visivel", "livro de receitas" in P)
    check("A1.pay-pc---hidden", rc_h == 0 and "aposta escondida" not in P)
    check("A1.pay-npc---public", rc_p == 0 and "gorjeta anunciada" in P)
    check("A1.cmp-npc-oculto", "Lia vigia" not in P)
    check("A1.job-npc-oculto", "Lia no caixa" not in P)
    check("A1.job-pc-visivel", "Ana atendente" in P)
    check("A1.life---public", rc_fp == 0 and "Festa da rua" in P)
    check("A1.life-pc---hidden", rc_th == 0 and "Terapia sigilosa" not in P)
    gm = must("world", "timeline")
    check("A1.visao-mestre-completa", all(x in gm for x in ("Saldo inicial de npc-chefe", "suborno secreto", "Lia vigia", "Lia no caixa")))
    ag = must("life", "agenda", "--public", "--span", "7d")
    check("A1.agenda-publica", "Ana atendente" in ag and "Lia" not in ag and "Terapia" not in ag)
    jog = "".join(p.read_text(errors="ignore") for p in (C / "maps" / "jogador").rglob("*") if p.is_file())
    check("A1.mapa-jogador-controle-positivo", "Briga no bar ontem" in jog)  # o local e seus eventos públicos aparecem
    check("A1.mapa-jogador-sem-futuro", "Assalto planejado" not in jog)
    check("A1.mapa-jogador-sem-cancelado", "será cancelado" not in jog)
    mes = "".join(p.read_text(errors="ignore") for p in (C / "maps" / "mestre").rglob("*") if p.is_file())
    check("A1.mapa-mestre-completo", "Assalto planejado" in mes)
    must("world", "rel-add", "npc-lia", "pc-ana", "admira", "--directed")
    rc_rp, _ = sh("world", "rel-add", "pc-ana", "npc-lia", "confia_em", "--directed", "--public")
    must("world", "rel-add", "npc-lia", "npc-chefe", "colegas")
    R = must("world", "rel", "pc-ana", "--public")
    check("A1.rel-direcional-oculta", "admira" not in R)
    check("A1.rel-direcional---public", rc_rp == 0 and "confia_em" in R)
    check("A1.rel-sem-direcao-compat", "colegas" in must("world", "rel", "npc-lia", "--public"))
    check("A1.rel-mestre-completa", "admira" in must("world", "rel", "pc-ana"))

    # ============================================================ A2 — validação prévia
    sh("econ", "set", "--json", J({"producers": [{"id": "sal-x", "who": "npc-chefe", "res": "dinheiro", "qty": 1, "every": "1d"}]}))
    bad = {
        "A2.empregador-inexistente": ("life", "job", "cmp-x1", "--who", "pc-ana", "--text", "X", "--employer", "npc-fantasma",
                                      "--at", "09:00", "--salary", "100", "--pay-days", "sexta", "--op", "x1"),
        "A2.escola-inexistente": ("life", "course", "cmp-x2", "--who", "pc-ana", "--text", "X", "--school", "loc-fantasma",
                                  "--at", "19:00", "--fee", "50", "--fee-monthday", "5", "--op", "x2"),
        "A2.salario-texto": ("life", "job", "cmp-x3", "--who", "pc-ana", "--text", "X", "--employer", "npc-chefe", "--at", "09:00",
                             "--salary", "abc", "--pay-days", "sexta", "--op", "x3"),
        "A2.salario-negativo": ("life", "job", "cmp-x4", "--who", "pc-ana", "--text", "X", "--employer", "npc-chefe", "--at", "09:00",
                                "--salary", "-5", "--pay-days", "sexta", "--op", "x4"),
        "A2.salario-zero": ("life", "job", "cmp-x5", "--who", "pc-ana", "--text", "X", "--employer", "npc-chefe", "--at", "09:00",
                            "--salary", "0", "--pay-days", "sexta", "--op", "x5"),
        "A2.id-rota-colide": ("life", "job", "cmp-x", "--who", "pc-ana", "--text", "X", "--employer", "npc-chefe", "--at", "09:00",
                              "--salary", "10", "--pay-days", "sexta", "--op", "x6"),
        "A2.open-valor-texto": ("econ", "open", "npc-senhorio", "abc", "--reason", "x", "--op", "x7"),
        "A2.pay-valor-texto": ("econ", "pay", "pc-ana", "loc-loja", "dez", "--reason", "x", "--op", "x8"),
        "A2.efeito-ref-inexistente": ("life", "add", "cmp-x9", "--who", "pc-ana", "--kind", "prova", "--text", "X", "--once", "+1d",
                                      "--on-attend", J([{"type": "flag.set", "name": "a", "value": 1},
                                                        {"type": "relation.feel", "from": "npc-fantasma", "to": "pc-ana", "dim": "afeto", "delta": 1}]),
                                      "--op", "x9"),
        "A2.efeito-feel-pc-sem-declarar": ("life", "add", "cmp-x10", "--who", "pc-ana", "--kind", "prova", "--text", "X", "--once", "+1d",
                                           "--on-miss", J([{"type": "relation.feel", "from": "pc-ana", "to": "npc-lia", "dim": "afeto", "delta": -1}]),
                                           "--op", "x10"),
        "A2.efeito-valor-invalido": ("life", "add", "cmp-x11", "--who", "pc-ana", "--kind", "prova", "--text", "X", "--once", "+1d",
                                     "--on-attend", J([{"type": "resource.produce", "who": "pc-ana", "res": "dinheiro", "qty": 5}]), "--op", "x11"),
        "A2.causa-inexistente-add": ("life", "add", "cmp-x12", "--who", "pc-ana", "--kind", "social", "--text", "X", "--once", "+1d",
                                     "--cause", "evt-9999", "--op", "x12"),
        "A2.causa-inexistente-end": ("life", "end", "cmp-vigia", "--reason", "x", "--cause", "evt-9999", "--op", "x13"),
        "A2.causa-inexistente-attend": ("life", "attend", "cmp-ana-trab", "presente", "--cause", "evt-9999", "--op", "x14"),
    }
    for cid, args in bad.items():
        before = state(C)
        rc, out = sh(*args)
        check(cid, rc != 0 and "Traceback" not in out and state(C) == before, out.strip().splitlines()[-1:] if rc == 0 or "Traceback" in out else "")
    check("A2.sem-evento-falho", not [e for e in evs(C) if e["status"] == "falhou"])
    unblock(C, "eventos falhos deixados por entradas inválidas")

    # ============================================================ falha permanente × recuperável
    imposs = {"type": "relation.feel", "from": "npc-lia", "to": "pc-ana", "dim": "ciume", "delta": 5}
    s_before = state(C)
    rc, out = sh("time", "schedule", "efeito impossível", "--in", "1h", "--effects", J(imposs))
    check("ENG.definicao-invalida-recusada-no-agendamento", rc != 0 and "ciume" in out and state(C) == s_before)
    w0 = sim.World(C)  # motor: definição inválida que chegue à execução (ex.: gerada por módulo) é falha permanente
    w0.schedule({"type": "evento", "text": "efeito impossível", "due": w0.now + 60, "effects": [imposs]})
    t0 = sim.load_clock(C)["now"]
    rc, out = sh("time", "advance", "2h", "--op", "perm", "--reason", "x")
    clk = sim.load_clock(C)
    imp = next(e for e in evs(C) if e.get("text") == "efeito impossível")
    check("ENG.permanente-nao-bloqueia", rc == 0 and clk["now"] == t0 + 120 and not clk.get("pending_op"), out.strip()[-200:])
    check("ENG.permanente-marcada", imp["status"] == "falhou" and (imp.get("result") or {}).get("permanente") is True)
    check("ENG.permanente-avisada", "permanente" in out)
    rc2, _ = sh("time", "advance", "1h", "--op", "perm2", "--reason", "x")
    check("ENG.permanente-nao-retentada", rc2 == 0 and len([h for h in imp.get("history", []) if h["status"] == "falhou"]) == 1)
    check("ENG.verify-aponta-permanente", "permanente" in must("world", "verify"))
    unblock(C, "falha de definição travando o relógio")
    # recuperável: bloqueia com instrução clara; corrigido o dado, 'time resume' conclui sem duplicar
    must("time", "schedule", "cobrança grande", "--in", "1h", "--effects",
         J({"type": "resource.transfer", "from": "npc-senhorio", "to": "npc-lia", "res": "dinheiro", "qty": 70}))
    rc, out = sh("time", "advance", "2h", "--op", "rec", "--reason", "x")
    check("ENG.recuperavel-bloqueia", rc != 0 and sim.load_clock(C).get("pending_op") and "time resume" in out and "time cancel" in out, out[-300:])
    sim.save_json(sim.res_path(C), {**sim.load_res(C), "holdings": {**sim.load_res(C)["holdings"], "npc-senhorio": {"dinheiro": 100}}})
    rc, _ = sh("time", "resume")
    check("ENG.recuperavel-resume", rc == 0 and sim.balance(C, "npc-senhorio", "dinheiro") == 30)
    lines = [ln for ln in must("world", "timeline").splitlines() if ln.startswith(next(e["id"] for e in evs(C) if e.get("text") == "cobrança grande") + " ")]
    check("CAUSE.evento-recuperado-id-unico", len(lines) == 1)
    # exceção de programação: não é escondida nem tratada como permanente
    sim.load_modules()

    @sim.effect("teste.bug")
    def _bug(w, f, key, ev):
        raise KeyError("campo_que_nao_existe")
    w = sim.World(C)
    w.schedule({"type": "teste", "text": "bug interno", "due": w.now + 30, "effects": [{"type": "teste.bug"}]})
    try:
        sim.advance(C, 60, "bug", "x")
        check("ENG.bug-visivel", False, "advance terminou sem erro")
    except Exception as ex:  # noqa: BLE001 — o teste exige que a exceção chegue até aqui
        bug = next(e for e in evs(C) if e.get("text") == "bug interno")
        check("ENG.bug-visivel", "KeyError" in str(ex) and bug["status"] == "falhou" and not (bug.get("result") or {}).get("permanente"))
    bug = next(e for e in evs(C) if e.get("text") == "bug interno")
    must("time", "cancel", bug["id"], "--reason", "bug de teste")
    unblock(C, "bloqueio ESPERADO: erro de programação não vira falha permanente")

    # ============================================================ idempotência de --op
    bal = sim.balance(C, "pc-ana", "dinheiro")
    rc, out = sh("econ", "pay", "pc-ana", "loc-loja", "5", "--reason", "outra coisa", "--op", "compra")
    check("IDEMP.op-reusada-com-outros-dados", rc != 0 and "outros dados" in out and sim.balance(C, "pc-ana", "dinheiro") == bal, out.strip()[-150:])
    rc, out = sh("econ", "pay", "pc-ana", "loc-loja", "30", "--reason", "livro de receitas", "--op", "compra")
    check("IDEMP.op-repetida-igual", rc == 0 and "já aplicado" in out and sim.balance(C, "pc-ana", "dinheiro") == bal)
    rc, out = sh("econ", "open", "npc-lia", "999", "--reason", "x", "--op", "o3")
    check("IDEMP.open-reusado-com-outros-dados", rc != 0 and "outros dados" in out)
    rc, out = sh("life", "add", "cmp-vigia2", "--who", "npc-lia", "--kind", "trabalho", "--text", "Outra", "--at", "23:00", "--op", "v1")
    check("IDEMP.life-reusado-com-outros-dados", rc != 0 and "outros dados" in out)

    # ============================================================ A3 — integridade econômica
    r_before = routes(C)
    rc, _ = sh("econ", "set", "--json", J({"routes": [{"id": "aluguel", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "authorized": "jogador: débito automático autorizado", "qty": 5000,
                                                       "bill": True, "partial": True, "when": {"at": "10:00", "monthday": 15}}]}))
    must("time", "advance", "2d", "--op", "a3", "--reason", "x")  # cruza 15/10 10:00 e sexta 16/10
    r0 = routes(C)
    check("A3.set-adiciona-sem-apagar", {"sal-lia-trab", "sal-ana-trab", "aluguel"} <= set(r0), sorted(r0))
    if not set(r_before) <= set(r0):  # comportamento antigo apagou rotas: restaura para medir o resto
        E = json.loads((C / "state" / "sim" / "economy.json").read_text())
        E["routes"] = [r for k, r in r_before.items() if k not in r0] + E["routes"]
        sim.save_json(C / "state" / "sim" / "economy.json", E)
        print("[limpeza] rotas apagadas pelo econ set restauradas")
        r0 = routes(C)
    arrears0 = r0.get("aluguel", {}).get("arrears", 0)
    check("A3.aluguel-com-atraso", arrears0 > 0)
    snap = routes(C)
    rc, out = sh("econ", "set", "--json", J({"routes": []}))
    check("A3.lista-vazia-nao-apaga", routes(C) == snap, f"rc={rc}")
    if routes(C) != snap:  # restaura para os passos seguintes poderem medir o resto
        E = json.loads((C / "state" / "sim" / "economy.json").read_text())
        E["routes"] = list(snap.values())
        sim.save_json(C / "state" / "sim" / "economy.json", E)
    rc, _ = sh("econ", "set", "--json", J({"routes": [{"id": "aluguel", "qty": 4800}]}))
    r1 = routes(C).get("aluguel", {})
    check("A3.update-por-id-preserva", rc == 0 and r1.get("qty") == 4800 and r1.get("arrears") == arrears0 and r1.get("bill") is True)
    for cid, patch in (("A3.protege-arrears", {"arrears": 0}), ("A3.protege-status", {"status": "interrompida"}),
                       ("A3.protege-history", {"history": []})):
        before = routes(C)
        rc, _ = sh("econ", "set", "--json", J({"routes": [{"id": "aluguel", **patch}]}))
        check(cid, rc != 0 and routes(C) == before)
    rc, _ = sh("econ", "set", "--json", J({"consumers": [{"id": "aluguel", "who": "pc-ana", "res": "x", "qty": 1, "every": "1d"}]}))
    check("A3.id-unico-entre-tipos", rc != 0)
    before = routes(C)
    rc, out = sh("econ", "remove", "sal-ana-trab", "--reason", "x", "--op", "r1")
    check("A3.remove-em-uso-recusada", rc != 0 and routes(C) == before and "life end" in out, out.strip()[-150:])
    must("life", "end", "cmp-ana-trab", "--reason", "pediu demissão", "--op", "le1")
    rc, _ = sh("econ", "remove", "sal-ana-trab", "--reason", "fim do vínculo", "--op", "r2")
    rs = routes(C).get("sal-ana-trab", {})
    check("A3.remove-auditada", rc == 0 and rs.get("status") == "removida" and rs.get("history", [{}])[-1].get("reason") == "fim do vínculo"
          and any(e.get("gen_key") == "econ:remove:r2" for e in evs(C)))
    rc, out = sh("econ", "remove", "aluguel", "--reason", "x", "--op", "r3")
    check("A3.remove-com-atraso-recusada", rc != 0 and "--forgive" in out and routes(C).get("aluguel", {}).get("status", "ativa") == "ativa",
          out.strip()[-150:])
    rc, _ = sh("econ", "remove", "aluguel", "--reason", "senhorio perdoou", "--forgive", "--op", "r4")
    ra = routes(C).get("aluguel", {})
    check("A3.perdao-auditado", rc == 0 and ra.get("status") == "removida" and ra.get("arrears") == 0
          and any(h.get("perdoado") == arrears0 for h in ra.get("history", [])))
    before = state(C)
    rc, _ = sh("life", "end", "cmp-ana-trab", "--reactivate", "--reason", "x", "--op", "le2")
    check("A3.reativar-com-rota-removida-recusada", rc != 0 and state(C) == before)
    rc, _ = sh("econ", "set", "--json", J({"routes": [{"id": "sal-ana-trab", "from": "npc-chefe", "to": "pc-ana", "res": "dinheiro",
                                                       "qty": 1, "every": "1d"}]}))
    check("A3.id-removido-nao-reutilizado", rc != 0 or routes(C).get("sal-ana-trab", {}).get("status") == "removida")
    n_sal = len([e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:sal-ana-trab")])
    must("time", "advance", "7d", "--op", "a4", "--reason", "x")
    check("A3.removida-nao-gera-ciclos", len([e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:sal-ana-trab")]) == n_sal)
    v = must("world", "verify")
    check("A3.referencias-intactas", "[vida]" not in v and "[economia]" not in v, v[-300:])

    # ============================================================ M3 — evento parcialmente aplicado: cancelar não desfaz nada
    must("life", "add", "cmp-p", "--who", "pc-ana", "--kind", "prova", "--text", "Prova", "--once", "+1h", "--op", "p0",
         "--on-attend", J([{"type": "flag.set", "name": "fez_prova", "value": True},
                           {"type": "relation.feel", "from": "npc-provedor", "to": "pc-ana", "dim": "respeito", "delta": 1}]))
    must("time", "advance", "2h", "--op", "a5", "--reason", "x")
    moved = C / "npcs" / "npc-provedor.md"
    moved.rename(tmp / "npc-provedor.md")  # dano manual: a entidade some depois de validada
    rc, out = sh("life", "attend", "cmp-p", "presente", "--op", "at1")
    att = next(e for e in evs(C) if e.get("gen_key") == "life:attend:at1")
    check("M3.attend-falha-parcial-identificada", rc != 0 and "PARCIAL" in out and att["status"] == "falhou", out.strip()[-200:])
    before = state(C)
    rc, out = sh("time", "cancel", att["id"], "--reason", "desistiu")
    check("M3.cancel-simples-recusado", rc != 0 and "--accept-partial" in out and state(C) == before)
    rc, out = sh("time", "cancel", att["id"], "--reason", "jogador desistiu da prova", "--accept-partial")
    att = next(e for e in evs(C) if e.get("gen_key") == "life:attend:at1")
    L = json.loads((C / "state" / "sim" / "life.json").read_text())
    rec = [k for k in L["attendance"] if k.startswith("cmp-p@")]
    check("M3.accept-partial-auditado", rc == 0 and att["status"] == "cancelado" and att["result"]["parcial"]
          and att["result"]["aplicados"] == ["life.attend", "flag.set"] and att["result"]["nao_aplicados"] == ["relation.feel"])
    check("M3.fatos-aplicados-nao-revertidos", len(rec) == 1 and sim.load_json(sim.flags_path(C), {}).get("fez_prova") is True)
    rc, out = sh("time", "cancel", att["id"], "--reason", "de novo", "--accept-partial")
    check("M3.cancel-repetido-recusado", rc != 0 and "já está cancelado" in out)
    (tmp / "npc-provedor.md").rename(moved)
    rc, out = sh("life", "attend", "cmp-p", "presente", "--op", "at1")
    check("M3.mesma-op-apos-cancelamento-recusada", rc != 0 and "cancelado" in out)
    rc, out = sh("life", "attend", "cmp-p", "presente", "--op", "at2")
    check("M3.ocorrencia-ja-registrada", rc != 0 and "já registrada" in out)
finally:
    shutil.rmtree(tmp)
    if sys.exc_info()[0]:  # preparação quebrou: mostra o que já foi verificado antes de propagar o erro
        for c, ok, d in RESULTS:
            print(f"{'OK  ' if ok else 'FALHA'} {c}" + (f"  — {d}" if d and not ok else ""))
        print("INTERROMPIDO: uma etapa de preparação falhou (veja o erro abaixo)")

fails = [(c, d) for c, ok, d in RESULTS if not ok and c not in PENDING]
pend = [(c, d) for c, ok, d in RESULTS if not ok and c in PENDING]
for c, ok, d in RESULTS:
    print(f"{'OK  ' if ok else ('PEND' if c in PENDING else 'FALHA')} {c}" + (f"  — {d}" if d and not ok else ""))
print(f"\n{len(RESULTS) - len(fails) - len(pend)} ok, {len(fails)} falha(s), {len(pend)} pendente(s) de etapa futura")
if fails:
    sys.exit(1)
print("OK: testes de confiabilidade (Fase 3.1.1) passaram")
