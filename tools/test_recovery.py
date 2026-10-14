#!/usr/bin/env python3
"""Fase 3.1.1, etapas 5–6, em raiz temporária: conflitos de --op, validação de --cause, eventos parcialmente aplicados
(falha em cada efeito, queda de processo, efeito composto), retomada repetida, cancelamento parcial, preservação do
histórico; e os riscos revisados na etapa 6 (calendário, economia desligada, local do PJ, rotinas por dia, condição feel,
on_late, --until, prazo de objetivo pessoal, contrato do 'time schedule'). Mostra cada verificação."""
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
RESULTS = []


def check(cid, ok, detail=""):
    RESULTS.append((cid, bool(ok), detail))


def sh(*args):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *[str(x) for x in args]], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def must(*args):
    rc, out = sh(*args)
    assert rc == 0, f"preparação falhou: {args}\n{out}"
    return out


def ent(C, eid, known="", **meta):
    sub = {"pc": "characters", "npc": "npcs", "loc": "locations", "quest": "quests"}[eid.split("-")[0]]
    body = "\n".join(f"{k}: {v}" for k, v in {"id": eid, "name": eid.split("-", 1)[1].title(), **meta}.items())
    (C / sub / f"{eid}.md").write_text(f"---\n{body}\n---\n# {eid}\n\n## Conhecido pelos jogadores\n{known}\n")


def evs(C):
    return sim.fold_events(sim.read_jsonl(C / "state" / "events.jsonl"))


def ev(C, **kw):
    return next(e for e in evs(C) if all(e.get(k) == v for k, v in kw.items()))


def snap(C):
    """Hash de todo o estado mutável: entrada recusada não pode mudar nada disto."""
    h = hashlib.sha256()
    for d in ("state", "characters", "npcs", "quests", "locations"):
        for p in sorted((C / d).rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(C)).encode() + p.read_bytes())
    return h.hexdigest()


def flags(C):
    return sim.load_json(sim.flags_path(C), {})


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    must("new", "rec", "--name", "Rec", "--system", "aventura-2d6", "--setting", "moderna")
    C = tmp / "campaigns" / "rec"
    TERRA = tmp / "templates" / "calendars" / "terrestre.json"
    must("time", "init", "--calendar", TERRA, "--start", "2026/10/12 08:00", "--seed", "9")  # segunda, feriado
    rpg.set_key(C / "campaign.yaml", "economy", "on")
    ent(C, "pc-ana", "Protagonista", location="loc-casa")
    for nid, loc in (("npc-lia", "loc-casa"), ("npc-chefe", "loc-loja"), ("npc-senhorio", "loc-casa"), ("npc-det", "loc-bar"),
                     ("npc-fiador", "loc-casa")):
        ent(C, nid, "Conhecido", status="vivo", location=loc)
    for lid in ("loc-casa", "loc-loja", "loc-bar", "loc-escola"):
        ent(C, lid, "Lugar")
    (C / "state" / "current.md").write_text("# Estado atual\n\n- **Local atual:** loc-bar\n- **Personagens presentes:** pc-ana\n")
    must("econ", "open", "pc-ana", "1000", "--reason", "ficha", "--op", "o1")
    must("econ", "open", "npc-chefe", "5000", "--reason", "caixa", "--op", "o2")
    must("econ", "open", "npc-chefe", "5", "--res", "livro", "--reason", "estoque", "--op", "o3")
    must("life", "add", "cmp-ontem", "--who", "pc-ana", "--kind", "social", "--text", "Café", "--once", "+30min", "--op", "s1",
         "--on-attend", J([{"type": "flag.set", "name": "cafe", "value": True}]))
    WEEK = "segunda,terça,quarta,quinta,sexta"
    must("life", "job", "cmp-trab", "--who", "pc-ana", "--text", "Atendente", "--employer", "npc-chefe", "--loc", "loc-loja",
         "--at", "09:00", "--days", WEEK, "--skip-rest", "--salary", "100", "--pay-days", "sexta", "--pay-at", "18:00", "--op", "s2")
    must("econ", "set", "--json", J({"producers": [{"id": "prod-x", "who": "npc-chefe", "res": "papel", "qty": 1, "every": "1d"},
                                                   {"id": "prod-y", "who": "npc-chefe", "res": "tinta", "qty": 1, "every": "1d"}]}))
    ent(C, "quest-x", "", status="em_andamento", type="pessoal", horizon="curto")
    ent(C, "quest-y", "", status="em_andamento", type="pessoal", horizon="curto")
    must("quest", "define", "quest-y", "--json", J({"objectives": [{"id": "o", "desc": "O"}]}))
    must("quest", "define", "quest-x", "--json", J({"objectives": [{"id": "o", "desc": "O"}]}))
    must("time", "advance", "1h", "--op", "s0", "--reason", "setup")  # → 09:00; café às 08:30 aconteceu
    base_evt = evs(C)[0]["id"]

    # ============================================================ 5.1 — --op: mesmos dados = idempotente; outros = conflito
    cases = [  # (id, comando base, mesmos dados ok?, variação com outros dados)
        ("pay", ["econ", "pay", "pc-ana", "loc-loja", "10", "--reason", "café", "--op", "p1"], True, {4: "11"}),
        ("open", ["econ", "open", "npc-lia", "50", "--reason", "economias", "--op", "o-lia"], True, {3: "60"}),
        ("trade", ["econ", "trade", "pc-ana", "npc-chefe", "livro", "1", "--price-res", "dinheiro", "--unit-price", "20", "--op", "t1"],
         False, {5: "2"}),
        ("life-add", ["life", "add", "cmp-cafe", "--who", "pc-ana", "--kind", "social", "--text", "Café 2", "--once", "+1d", "--op", "l1"],
         True, {8: "Café 3"}),
        ("feel", ["world", "feel", "npc-lia", "pc-ana", "afeto", "+5", "--reason", "x", "--op", "f1"], True, {5: "+6"}),
        ("move", ["world", "move", "npc-det", "loc-loja", "--op", "m1"], True, {3: "loc-casa"}),
        ("quest", ["quest", "set-status", "quest-x", "cancelada", "--reason", "fim", "--op", "q1"], True, {5: "outro motivo"}),
        ("propose", ["npc", "propose", "npc-det", "--action", J({"desc": "vigiar", "effects": [{"type": "flag.set", "name": "vig", "value": True}]}),
                     "--op", "np1"], True, {4: J({"desc": "dormir", "effects": [{"type": "flag.set", "name": "dorme", "value": True}]})}),
        ("decide", ["npc", "decide", "npc-det", "--commit", "--op", "nd1"], True, {"+": ["--cause", base_evt]}),
        ("remove", ["econ", "remove", "prod-x", "--reason", "fechou", "--op", "rm1"], True, {4: "outro"}),
    ]
    for cid, cmd, same_ok, var in cases:
        must(*cmd)
        s0 = snap(C)
        rc, out = sh(*cmd)
        check(f"5.1.{cid}.mesmos-dados", (rc == 0 if same_ok else rc != 0 and "já foi aplicada" in out) and snap(C) == s0, out.strip()[-120:])
        other = list(cmd)
        for k, v in var.items():
            if k == "+":
                other += v
            else:
                other[k] = v
        rc, out = sh(*other)
        check(f"5.1.{cid}.outros-dados-conflito", rc != 0 and ("outros dados" in out or "outra" in out) and snap(C) == s0, out.strip()[-160:])

    # ============================================================ 5.2 — --cause inválido: recusado antes, nada muda
    must("life", "add", "cmp-c2", "--who", "pc-ana", "--kind", "social", "--text", "C2", "--once", "+2d", "--op", "c2")
    X = "evt-9999"
    for cid, cmd in (("econ-pay", ["econ", "pay", "pc-ana", "loc-loja", "1", "--reason", "x", "--op", "x1", "--cause", X]),
                     ("econ-remove", ["econ", "remove", "prod-y", "--reason", "x", "--op", "x2", "--cause", X]),
                     ("world-feel", ["world", "feel", "npc-lia", "pc-ana", "afeto", "+1", "--reason", "x", "--op", "x3", "--cause", X]),
                     ("world-move", ["world", "move", "pc-ana", "loc-loja", "--op", "x4", "--cause", X]),
                     ("life-add", ["life", "add", "cmp-x5", "--who", "pc-ana", "--kind", "social", "--text", "x", "--once", "+1d", "--op", "x5", "--cause", X]),
                     ("life-end", ["life", "end", "cmp-c2", "--reason", "x", "--op", "x6", "--cause", X]),
                     ("life-attend", ["life", "attend", "cmp-ontem", "presente", "--op", "x7", "--cause", X]),
                     ("quest-objective", ["quest", "objective", "quest-y", "o", "--done", "--reason", "x", "--op", "x8", "--cause", X]),
                     ("npc-decide", ["npc", "decide", "npc-lia", "--commit", "--op", "x9", "--cause", X]),
                     ("npc-propose", ["npc", "propose", "npc-lia", "--action", J({"desc": "x", "effects": [{"type": "flag.set", "name": "x", "value": 1}]}),
                                      "--op", "x10", "--cause", X]),
                     ("time-schedule", ["time", "schedule", "x", "--in", "1h", "--cause", X]),
                     ("world-event-add", ["world", "event-add", "x", "--cause", X])):
        s0 = snap(C)
        rc, out = sh(*cmd)
        check(f"5.2.causa-invalida.{cid}", rc != 0 and "Traceback" not in out and snap(C) == s0, out.strip()[-120:])

    # ============================================================ 5.3 — falha em cada efeito de um evento de 3 efeitos
    for k in (0, 1, 2):
        effs = [{"type": "flag.set", "name": f"k{k}-{i}", "value": 1} for i in range(3)]
        effs[k] = {"type": "entity.set", "id": "npc-lia", "field": "location", "value": f"loc-falta-{k}"}
        out = must("time", "schedule", f"passos {k}", "--in", "10min", "--effects", J(effs))
        check(f"5.3.k{k}.aviso-referencia-futura", f"loc-falta-{k}" in out and "AVISO" in out)
        rc, out = sh("time", "advance", "20min", "--op", f"adv-{k}", "--reason", "x")
        e = ev(C, text=f"passos {k}")
        st, n, inc = sim.application(e)
        check(f"5.3.k{k}.classificacao", rc != 0 and e["status"] == "falhou" and n == k and st == ("parcial" if k else "nao_iniciado")
              and ("PARCIAL" in out) == bool(k), f"st={st} n={n} rc={rc}")
        ins = must("time", "inspect", e["id"])
        check(f"5.3.k{k}.inspect", all(f"#{i} flag.set: aplicado" in ins for i in range(k)) and f"#{k} entity.set: não aplicado" in ins)
        if k:
            s0 = snap(C)
            rc, out = sh("time", "cancel", e["id"], "--reason", "x")
            check(f"5.3.k{k}.cancel-simples-recusado", rc != 0 and "PARCIALMENTE" in out and snap(C) == s0)
            F = flags(C)
            F[f"k{k}-0"] = "mudado-por-outro-evento"  # outro fato posterior: a retomada NÃO pode sobrescrevê-lo
            sim.save_json(sim.flags_path(C), F)
        ent(C, f"loc-falta-{k}", "Lugar")
        rc, out = sh("time", "retry", e["id"])
        F = flags(C)
        expect_first = "mudado-por-outro-evento" if k else 1
        check(f"5.3.k{k}.retry-conclui-sem-reaplicar", rc == 0 and F.get(f"k{k}-0", 1 if k == 0 else None) == expect_first
              and all(F.get(f"k{k}-{i}") == 1 for i in range(k + 1, 3)) and rpg.read_meta(C / "npcs" / "npc-lia.md")["location"] == f"loc-falta-{k}",
              f"{out.strip()[-120:]} flags={ {x: F.get(x) for x in F if x.startswith(f'k{k}')} }")
        rc2, out2 = sh("time", "retry", e["id"])
        check(f"5.3.k{k}.retry-repetido-recusado", rc2 != 0 and "só eventos falhos" in out2)
        must("time", "resume")
        rc3, out3 = sh("time", "resume")
        check(f"5.3.k{k}.resume-repetido-recusado", rc3 != 0 and "nenhum avanço pendente" in out3)
        e = ev(C, text=f"passos {k}")
        check(f"5.3.k{k}.processado-uma-vez", [h["status"] for h in e["history"]].count("processado") == 1)
    # falha recuperável sem nada aplicado: cancelar é simples e diz isso
    must("time", "schedule", "conta impossível", "--in", "10min", "--effects",
         J({"type": "resource.transfer", "from": "npc-senhorio", "to": "pc-ana", "res": "dinheiro", "qty": 5}))
    sh("time", "advance", "20min", "--op", "adv-nada", "--reason", "x")
    e = ev(C, text="conta impossível")
    rc, out = sh("time", "cancel", e["id"], "--reason", "erro do mestre")
    check("5.3.nao-iniciado-cancel-simples", rc == 0 and "nada dele havia sido aplicado" in out and "time resume" in out)
    must("time", "resume")

    # ============================================================ 5.4 — queda do processo no meio de um evento
    sim.load_modules()
    w = sim.World(C)
    bal0 = sim.balance(C, "pc-ana", "dinheiro")
    eid = w.schedule({"type": "teste", "text": "queda no meio", "due": w.now,
                      "effects": [{"type": "resource.transfer", "from": "pc-ana", "to": "npc-lia", "res": "dinheiro", "qty": 7},
                                  {"type": "flag.set", "name": "depois_da_queda", "value": True}]},
                     declared=True)  # pagamento declarado pelo jogador (como 'econ pay'): o teste é a queda, não a agência
    real = sim.EFFECTS["flag.set"]

    def crash(*a, **k):
        raise KeyboardInterrupt("processo morto")
    sim.EFFECTS["flag.set"] = crash
    try:
        sim.process_event(w, w.event(eid))
    except KeyboardInterrupt:
        pass
    sim.EFFECTS["flag.set"] = real
    e = ev(C, id=eid)
    st, n, inc = sim.application(e)
    check("5.4.queda-identificada", e["status"] == "processando" and st == "parcial" and n == 1 and inc, f"{st} {n} {inc}")
    rc, out = sh("time", "cancel", eid, "--reason", "x")
    check("5.4.cancel-simples-recusado", rc != 0 and "PARCIALMENTE" in out)
    rc, out = sh("time", "retry", eid)
    check("5.4.retry-sem-duplicar", rc == 0 and sim.balance(C, "pc-ana", "dinheiro") == bal0 - 7 and flags(C).get("depois_da_queda") is True)

    # ============================================================ 5.5 — efeito COMPOSTO (conta com consequência) falha por dentro
    must("econ", "set", "--json", J({"routes": [{"id": "conta-x", "from": "pc-ana", "to": "npc-senhorio", "res": "dinheiro", "authorized": "jogador: débito automático autorizado", "qty": 999999,
                                                "bill": True, "when": {"at": "10:00", "monthday": 13},
                                                "on_arrears": [{"type": "relation.feel", "from": "npc-fiador", "to": "pc-ana", "dim": "confianca", "delta": -3}]}]}))
    fia = C / "npcs" / "npc-fiador.md"
    fia.rename(tmp / "npc-fiador.md")
    rc, out = sh("time", "advance", "1d", "--op", "dia13", "--reason", "x")  # → terça 13, depois das 10:00
    e = next(e for e in evs(C) if (e.get("gen_key") or "").startswith("econ:conta-x"))
    st, n, inc = sim.application(e)
    check("5.5.composto-parcial-incerto", rc != 0 and st == "parcial" and n == 0 and inc and "pode ter sido aplicado em parte" in out, out.strip()[-200:])
    rc, out = sh("time", "cancel", e["id"], "--reason", "x")
    check("5.5.cancel-simples-recusado", rc != 0 and "PARCIALMENTE" in out)
    (tmp / "npc-fiador.md").rename(fia)
    must("time", "retry", e["id"])
    rc, _ = sh("time", "resume")
    E = json.loads((C / "state" / "sim" / "economy.json").read_text())
    r = next(x for x in E["routes"] if x["id"] == "conta-x")
    rel = [x for x in sim.read_jsonl(C / "state" / "relations.jsonl") if x.get("from") == "npc-fiador"]
    check("5.5.retomada-sem-duplicar", rc == 0 and r["arrears"] == 999999 and len(rel) == 1
          and len([k for k in E["_bills"] if E["_bills"][k]["entry"] == "conta-x"]) == 1)

    # ============================================================ 5.6 — presença: falha, retry, aceitar parcial, histórico preservado
    must("life", "add", "cmp-aula", "--who", "pc-ana", "--kind", "estudo", "--text", "Aula", "--once", "+30min", "--op", "au",
         "--on-attend", J([{"type": "flag.set", "name": "aula_a", "value": True},
                           {"type": "relation.feel", "from": "npc-fiador", "to": "pc-ana", "dim": "respeito", "delta": 2},
                           {"type": "flag.set", "name": "aula_b", "value": True}]))
    must("time", "advance", "1h", "--op", "aula", "--reason", "x")
    fia.rename(tmp / "npc-fiador.md")
    rc, out = sh("life", "attend", "cmp-aula", "presente", "--op", "pa1")
    e = ev(C, gen_key="life:attend:pa1")
    check("5.6.attend-parcial", rc != 0 and sim.application(e)[:2] == ("parcial", 2), out.strip()[-160:])
    (tmp / "npc-fiador.md").rename(fia)
    rc, out = sh("life", "attend", "cmp-aula", "presente", "--op", "pa1")  # mesma op = conclui o que faltou
    L = json.loads((C / "state" / "sim" / "life.json").read_text())
    rel = [x for x in sim.read_jsonl(C / "state" / "relations.jsonl") if x.get("from") == "npc-fiador" and x.get("dim") == "respeito"]
    check("5.6.mesma-op-conclui-sem-duplicar", rc == 0 and flags(C).get("aula_b") is True and len(rel) == 1
          and len([k for k in L["attendance"] if k.startswith("cmp-aula@")]) == 1)
    rc, out = sh("life", "attend", "cmp-aula", "presente", "--op", "pa1")
    check("5.6.repeticao-idempotente", rc == 0 and "já aplicado" in out and len(rel) == 1)
    # aceitar parcial: histórico intacto (só acréscimos), nada revertido
    must("life", "add", "cmp-aula2", "--who", "pc-ana", "--kind", "estudo", "--text", "Aula 2", "--once", "+30min", "--op", "au2",
         "--on-attend", J([{"type": "flag.set", "name": "aula2_a", "value": True},
                           {"type": "relation.feel", "from": "npc-fiador", "to": "pc-ana", "dim": "afeto", "delta": 1}]))
    must("time", "advance", "1h", "--op", "aula2", "--reason", "x")
    fia.rename(tmp / "npc-fiador.md")
    sh("life", "attend", "cmp-aula2", "presente", "--op", "pa2")
    hist = (C / "state" / "events.jsonl").read_text()
    e = ev(C, gen_key="life:attend:pa2")
    rc, out = sh("time", "cancel", e["id"], "--reason", "o jogador desistiu", "--accept-partial")
    e = ev(C, gen_key="life:attend:pa2")
    check("5.6.accept-partial", rc == 0 and e["status"] == "cancelado" and e["result"]["aplicados"] == ["life.attend", "flag.set"]
          and flags(C).get("aula2_a") is True)
    check("5.6.historico-so-acrescimos", (C / "state" / "events.jsonl").read_text().startswith(hist))
    ins = must("time", "inspect", e["id"])
    check("5.6.inspect-cancelado-parcial", "aplicação: cancelado" in ins and "'parcial': True" in ins)
    (tmp / "npc-fiador.md").rename(fia)
    rc, out = sh("time", "retry", e["id"])
    check("5.6.retry-de-cancelado-recusado", rc != 0)
    v = must("world", "verify")
    check("5.6.verify-sem-pendencias-de-evento", "[evento]" not in v, v[-300:])

    # evento ANTIGO de presença (consequências embutidas, sem 'inline'): continua funcionando
    must("life", "add", "cmp-velho", "--who", "pc-ana", "--kind", "social", "--text", "Velho", "--once", "+10min", "--op", "ve",
         "--on-attend", J([{"type": "flag.set", "name": "velho", "value": True}]))
    must("time", "advance", "20min", "--op", "velho", "--reason", "x")
    w = sim.World(C)
    occ = json.loads((C / "state" / "sim" / "life.json").read_text())["commitments"]["cmp-velho"]["once"]
    eid = w.schedule({"type": "compromisso.presenca", "text": "formato antigo", "due": w.now,  # como 'life attend' gravava
                      "effects": [{"type": "life.attend", "id": "cmp-velho", "at": occ, "status": "presente"}]}, gen_key="life:attend:velho")
    st, _ = sim.process_event(w, w.event(eid))
    check("5.7.presenca-formato-antigo", st == "processado" and flags(C).get("velho") is True and sim.is_composite(w.event(eid)["effects"][0]))

    # ============================================================ 6.1 — calendário: o passado não é reinterpretado
    rc, out = sh("life", "attend", "cmp-trab", "presente", "--at", "2026/10/09 09:00", "--op", "antes")
    check("6.1.ocorrencia-antes-da-criacao-recusada", rc != 0 and "não tem ocorrência" in out)
    must("time", "advance", "5d", "--op", "semana", "--reason", "x")  # → domingo 18
    cal0 = sim.load_clock(C)["calendar"]
    sab17 = sim.parse_when("2026/10/17 10:00", cal0, 0)
    sab24 = sim.parse_when("2026/10/24 10:00", cal0, 0)
    must("time", "calendar", "--rest", "domingo", "--holiday", "10/14=Dia Inventado")
    cal = sim.load_clock(C)["calendar"]
    check("6.1.folga-antiga-vale-no-passado", sim.is_rest(sab17, cal) and not sim.is_rest(sab24, cal))
    check("6.1.feriado-novo-nao-retroage", sim.holiday(sim.parse_when("2026/10/14 09:00", cal, 0), cal) is None
          and sim.holiday(sim.parse_when("2027/10/14 09:00", cal, 0), cal) == "Dia Inventado")
    rc, out = sh("life", "attend", "cmp-trab", "presente", "--at", "2026/10/14 09:00", "--op", "qua14")
    check("6.1.presenca-passada-continua-valida", rc == 0, out.strip()[-120:])

    # ============================================================ 6.2 — economia desligada com conta ligada
    rpg.set_key(C / "campaign.yaml", "economy", "off")
    v = must("world", "verify")
    s0 = snap(C)
    rc, out = sh("life", "end", "cmp-trab", "--reason", "x", "--op", "fim-off")
    check("6.2.verify-avisa-conta-parada", "cmp-trab" in v and "economy" in v and "desligada" in v)
    check("6.2.life-end-recusado-sem-evento", rc != 0 and "economy: on" in out and snap(C) == s0)
    rpg.set_key(C / "campaign.yaml", "economy", "on")

    # ============================================================ 6.3 — local do protagonista × testemunhas
    v = must("world", "verify")
    check("6.3.verify-acusa-pc-fora-da-cena", "[local incompatível] pc-ana" in v)
    must("fact", "add", "fact-briga", "Houve uma briga no bar")
    must("time", "schedule", "briga 1", "--in", "5min", "--loc", "loc-bar", "--effects", J({"type": "knowledge.witness", "fact": "fact-briga", "loc": "loc-bar"}))
    must("time", "advance", "10min", "--op", "briga1", "--reason", "x")
    check("6.3.sem-move-nao-testemunha", not sim.knows(C, "pc-ana", "fact-briga"))
    out = must("world", "move", "pc-ana", "loc-bar", "--reason", "o jogador disse que foi ao bar", "--op", "mv1")
    check("6.3.move-pc-publico", "loc-casa → loc-bar" in out and "foi ao bar" in must("world", "timeline", "--public"))
    must("fact", "add", "fact-briga2", "Segunda briga")
    must("time", "schedule", "briga 2", "--in", "5min", "--loc", "loc-bar", "--effects", J({"type": "knowledge.witness", "fact": "fact-briga2", "loc": "loc-bar"}))
    must("time", "advance", "10min", "--op", "briga2", "--reason", "x")
    check("6.3.com-move-testemunha", sim.knows(C, "pc-ana", "fact-briga2") and "[local incompatível] pc-ana" not in must("world", "verify"))
    must("world", "move", "npc-lia", "loc-escola", "--op", "mv2")
    check("6.3.move-npc-oculto", "npc-lia vai para" not in must("world", "timeline", "--public"))

    # ============================================================ 6.4 — rotina de NPC por dia da semana (com folga/feriado)
    rc, out = sh("npc", "set", "npc-chefe", "--routine", J([{"from": "09:00", "to": "10:00", "loc": "loc-loja", "days": ["segunda"]},
                                                         {"from": "09:00", "to": "10:00", "loc": "loc-casa"}]))
    check("6.4.conflito-de-horario", rc != 0 and "dois blocos" in out)
    must("npc", "set", "npc-chefe", "--routine", J([{"from": "09:00", "to": "18:00", "loc": "loc-loja", "days": WEEK.split(","), "skip_rest": True},
                                                    {"from": "19:00", "to": "23:00", "loc": "loc-casa"}]))
    t_set = sim.load_clock(C)["now"]
    must("time", "advance", "7d", "--op", "rotina", "--reason", "x")  # domingo 18 → domingo 25
    cal = sim.load_clock(C)["calendar"]
    r9 = [e for e in evs(C) if e.get("type") == "npc.rotina" and e.get("npc") == "npc-chefe" and e["due"] > t_set and "loc-loja" in e["text"]]
    days = [sim.weekday(e["due"], cal) for e in r9]
    check("6.4.so-dias-uteis", days == ["segunda", "terça", "quarta", "quinta", "sexta"], days)
    r19 = [e for e in evs(C) if e.get("type") == "npc.rotina" and e.get("npc") == "npc-chefe" and e["due"] > t_set and "loc-casa" in e["text"]]
    check("6.4.bloco-sem-dias-todo-dia", len(r19) == 7, len(r19))

    # ============================================================ 6.5 — condição feel nas decisões de NPC
    must("npc", "set", "npc-lia", "--tier", "2", "--goals", J([{"id": "aproximar", "priority": 2, "actions": [
        {"id": "convidar", "desc": "convidar Ana para jantar", "utility": 3,
         "preconditions": [{"type": "feel", "from": "npc-lia", "to": "pc-ana", "dim": "afeto", "gte": 40}],
         "effects": [{"type": "flag.set", "name": "convite", "value": True}]}]}]))
    out = must("npc", "decide", "npc-lia")
    check("6.5.sem-afeto-nao-age", "ESCOLHA" not in out and "afeto=5" in out, out[-200:])
    must("world", "feel", "npc-lia", "pc-ana", "afeto", "+40", "--reason", "Ana cuidou dela doente", "--op", "f-lia")
    check("6.5.com-afeto-age", "ESCOLHA: aproximar/convidar" in must("npc", "decide", "npc-lia"))
    check("6.5.reciproco-nao-inferido", ("pc-ana", "npc-lia") not in sim.feelings(sim.read_jsonl(C / "state" / "relations.jsonl")))

    # ============================================================ 6.6 — on_late, --until, prazo de objetivo pessoal
    must("life", "add", "cmp-reuniao", "--who", "pc-ana", "--kind", "trabalho", "--text", "Reunião", "--once", "+30min", "--op", "re",
         "--on-attend", J([{"type": "flag.set", "name": "pontual", "value": True}]),
         "--on-late", J([{"type": "relation.feel", "from": "npc-chefe", "to": "pc-ana", "dim": "respeito", "delta": -2}]))
    must("time", "advance", "1h", "--op", "reuniao", "--reason", "x")
    must("life", "attend", "cmp-reuniao", "atrasou", "--op", "late1")
    F = sim.feelings(sim.read_jsonl(C / "state" / "relations.jsonl"))
    check("6.6.on-late-aplicado", F.get(("npc-chefe", "pc-ana"), {}).get("respeito") == -2 and "pontual" not in flags(C))
    must("life", "add", "cmp-curso", "--who", "pc-ana", "--kind", "estudo", "--text", "Curso curto", "--at", "19:00", "--until", "+2d", "--op", "cc")
    ag = must("life", "agenda", "--span", "7d")
    check("6.6.until-limita", ag.count("Curso curto") == 2, ag.count("Curso curto"))
    ent(C, "quest-meta", "", status="em_andamento", type="pessoal", horizon="curto")
    must("quest", "define", "quest-meta", "--json", J({"deadline": "+1d", "on_deadline": {"status": "expirada",
                                                       "effects": [{"type": "flag.set", "name": "meta_expirou", "value": True}]},
                                                       "objectives": [{"id": "juntar", "desc": "Juntar 1 milhão",
                                                                       "done_conditions": [{"type": "resource.gte", "who": "pc-ana", "res": "dinheiro", "qty": 1000000}]}]}))
    must("time", "advance", "2d", "--op", "prazo", "--reason", "x")
    must("time", "advance", "2d", "--op", "prazo2", "--reason", "x")
    Q = json.loads((C / "state" / "sim" / "quests.json").read_text())["quests"]["quest-meta"]
    check("6.6.prazo-expira-uma-vez", rpg.read_meta(C / "quests" / "quest-meta.md")["status"] == "expirada" and flags(C).get("meta_expirou") is True
          and [h["para"] for h in Q["history"]].count("expirada") == 1)

    # ============================================================ 6.7 — contrato do 'time schedule'
    out = must("time", "schedule", "chega o primo", "--in", "1h", "--effects",
               J({"type": "entity.set", "id": "npc-primo", "field": "location", "value": "loc-casa"}))
    check("6.7.ref-futura-avisada", "AVISO: ainda não existem: npc-primo" in out)
    ent(C, "npc-primo", "", status="vivo", location="loc-bar")  # dependência resolvida antes da execução
    must("time", "advance", "2h", "--op", "primo", "--reason", "x")
    check("6.7.ref-resolvida-na-execucao", rpg.read_meta(C / "npcs" / "npc-primo.md")["location"] == "loc-casa")
    must("time", "schedule", "nunca existe", "--in", "1h", "--effects", J({"type": "knowledge.learn", "who": "npc-nunca", "fact": "fact-briga"}))
    rc, out = sh("time", "advance", "2h", "--op", "nunca", "--reason", "x")
    e = ev(C, text="nunca existe")
    check("6.7.ref-nao-resolvida-recuperavel", rc != 0 and "time resume" in out and e["status"] == "falhou" and not sim.is_permanent(e),
          out.strip()[-160:])
    must("time", "cancel", e["id"], "--reason", "o primo nunca veio")
    must("time", "resume")
    invalid = {  # comprovadamente inválidas: recusadas no agendamento, estado COMPLETO idêntico
        "sem-source": {"type": "resource.produce", "who": "pc-ana", "res": "x", "qty": 1},
        "qtd-zero": {"type": "resource.transfer", "from": "pc-ana", "to": "npc-lia", "res": "dinheiro", "qty": 0},
        "para-si": {"type": "resource.transfer", "from": "pc-ana", "to": "pc-ana", "res": "dinheiro", "qty": 1},
        "dimensao": {"type": "relation.feel", "from": "npc-lia", "to": "pc-ana", "dim": "ciume", "delta": 1},
        "pc-sem-declarar": {"type": "relation.feel", "from": "pc-ana", "to": "npc-lia", "dim": "afeto", "delta": 1},
        "campo": {"type": "entity.set", "id": "npc-lia", "field": "name", "value": "X"},
        "status-rota": {"type": "econ.route.status", "route": "sal-trab", "status": "removida"},
        "tipo-desconhecido": {"type": "nao.existe"},
        "sem-tipo": {"qty": 1},
    }
    for cid, eff in invalid.items():
        s0 = snap(C)
        rc, out = sh("time", "schedule", f"inválido {cid}", "--in", "1h", "--effects", J(eff))
        check(f"6.7.agendamento-invalido-recusado.{cid}", rc != 0 and "Traceback" not in out and snap(C) == s0, out.strip()[-120:])
    s0 = snap(C)
    rc, out = sh("time", "schedule", "condição inválida", "--in", "1h", "--conditions", J({"type": "nao.existe"}))
    check("6.7.agendamento-invalido-recusado.condicao", rc != 0 and snap(C) == s0)
    # dependente de estado (saldo hoje insuficiente) NÃO é recusado: julgado na execução
    out = must("time", "schedule", "pagamento futuro", "--in", "1h", "--effects",
               J({"type": "resource.transfer", "from": "npc-senhorio", "to": "pc-ana", "res": "dinheiro", "qty": 10}))
    check("6.7.dependente-de-estado-aceito", "Agendado" in out)
    must("econ", "pay", "npc-chefe", "npc-senhorio", "10", "--reason", "repasse", "--op", "rep")  # o saldo chega antes da execução
    w0 = sim.World(C)  # o motor segue tratando definição inválida que chegue à execução como falha permanente
    w0.schedule({"type": "evento", "text": "regra inválida", "due": w0.now + 30,
                 "effects": [{"type": "resource.produce", "who": "pc-ana", "res": "x", "qty": 1}]})
    rc, out = sh("time", "advance", "2h", "--op", "regra", "--reason", "x")
    check("6.7.dependente-de-estado-executado", ev(C, text="pagamento futuro")["status"] == "processado")
    check("6.7.regra-invalida-nao-bloqueia", rc == 0 and "ATENÇÃO" in out and sim.is_permanent(ev(C, text="regra inválida")))
    s0 = snap(C)
    rc1, _ = sh("time", "schedule", "x", "--in", "1h", "--entities", "npc-inexistente")
    rc2, _ = sh("time", "schedule", "x", "--in", "1h", "--loc", "loc-inexistente")
    check("6.7.campos-do-evento-exigem-existencia", rc1 != 0 and rc2 != 0 and snap(C) == s0)

    # ============================================================ 6.8 — auditoria de relações direcionais (somente leitura)
    must("world", "rel-add", "npc-lia", "pc-ana", "admira", "--directed")                      # privada (padrão 3.1.1)
    must("world", "rel-add", "npc-lia", "npc-det", "teme", "--directed", "--secret")           # privada explícita
    must("world", "rel-add", "pc-ana", "npc-lia", "confia_em", "--directed", "--public")       # pública EXPLÍCITA
    with open(C / "state" / "relations.jsonl", "a") as f:  # registros como os gravados ANTES da 3.1.1 (sem marcação)
        f.write(J({"op": "add", "id": "rel-0900", "a": "npc-det", "b": "pc-ana", "type": "desconfia_de", "directed": True,
                   "secret": False, "session": 1, "t": "2026-10-01T00:00:00"}) + "\n")
        f.write(J({"op": "add", "id": "rel-0901", "a": "npc-primo", "b": "pc-ana", "type": "inveja", "directed": True,
                   "secret": False, "session": 1, "t": "2026-10-01T00:00:00"}) + "\n")
    explicit = [r for r in sim.read_jsonl(C / "state" / "relations.jsonl") if r.get("type") == "confia_em"]
    check("6.8.publica-explicita-marcada", explicit and explicit[0].get("public") is True and not explicit[0].get("secret"))
    s0 = snap(C)
    out = must("world", "rel-audit")
    check("6.8.auditoria-nao-altera-nada", snap(C) == s0)
    check("6.8.contagem", "privadas 2, públicas explícitas 1, ambíguas 2" in out, out.splitlines()[0])
    line = lambda rid: next((ln for ln in out.splitlines() if rid in ln), "")  # noqa: E731
    check("6.8.ambigua-exposta-identificada", "[ambigua]" in line("rel-0900") and "EXPOSTA AGORA" in line("rel-0900"))
    check("6.8.ambigua-nao-exposta", "[ambigua]" in line("rel-0901") and "não exposta agora" in line("rel-0901"))
    pub = must("world", "rel", "pc-ana", "--public")
    check("6.8.exposicao-confirmada", "desconfia_de" in pub and "confia_em" in pub and "admira" not in pub and "inveja" not in pub)

    # ============================================================ 5.8 — --op opcional em 'time schedule' e 'world event-add' (Fase 3.2)
    def by_gk(gk):
        return [e for e in evs(C) if e.get("gen_key") == gk]

    PROD = [{"type": "resource.produce", "who": "npc-lia", "res": "fichas", "qty": 5, "source": "teste 5.8"}]
    n0 = len(evs(C))
    must("time", "schedule", "sem op", "--in", "1h")
    must("time", "schedule", "sem op", "--in", "1h")
    check("5.8.sem-op-cria-outro", len(evs(C)) == n0 + 2)

    base = ["time", "schedule", "lia ganha fichas", "--in", "2d", "--entities", "npc-lia", "--loc", "loc-casa",
            "--effects", J(PROD), "--hidden"]
    out1 = must(*base, "--op", "s1")
    eid1 = by_gk("schedule:s1")[0]["id"]
    s0 = snap(C)
    rc, out = sh(*base, "--op", "s1")
    check("5.8.repeticao-identica", rc == 0 and eid1 in out and "nada novo" in out and snap(C) == s0, out)
    reordered = '[ {"source": "teste 5.8", "qty": 5,  "res": "fichas", "who": "npc-lia", "type": "resource.produce"} ]'
    rc, out = sh(*base[:-3], "--effects", reordered, "--hidden", "--op", "s1")
    check("5.8.json-equivalente", rc == 0 and eid1 in out and snap(C) == s0, out)
    raw = [json.loads(ln) for ln in (C / "state" / "events.jsonl").read_text().splitlines() if ln.strip()]
    line1 = [r for r in raw if r.get("gen_key") == "schedule:s1"]
    check("5.8.chave-e-assinatura-na-mesma-linha", len(line1) == 1 and line1[0]["id"] == eid1 and len(line1[0].get("op_sig", "")) == 64)

    variants = {"texto": (2, "outro texto"), "quando": (4, "3d"), "entidades": (6, "npc-det"), "local": (8, "loc-bar"),
                "efeitos": (10, J([dict(PROD[0], qty=6)]))}
    for name, (i, v) in variants.items():
        args = list(base)
        args[i] = v
        rc, out = sh(*args, "--op", "s1")
        check(f"5.8.conflito.{name}", rc != 0 and "outros dados" in out and snap(C) == s0, out[-150:])
    for name, extra in {"tipo": ["--type", "x"], "prioridade": ["--priority", "10"], "condicoes": ["--conditions", J([{"type": "flag", "name": "z"}])],
                        "on-unmet": ["--on-unmet", "adiar"], "causa": ["--cause", eid1]}.items():
        rc, out = sh(*base, *extra, "--op", "s1")
        check(f"5.8.conflito.{name}", rc != 0 and "outros dados" in out and snap(C) == s0, out[-150:])
    rc, out = sh(*base[:-1], "--op", "s1")  # sem --hidden
    check("5.8.conflito.visibilidade", rc != 0 and "outros dados" in out and snap(C) == s0, out[-150:])

    # relativo × absoluto: --in 2d ≡ --in 48h ≡ --at +2d (mesma duração); absoluto só repete com o mesmo instante
    for name, when in {"in-48h": ["--in", "48h"], "at-relativo": ["--at", "+2d"]}.items():
        args = base[:3] + when + base[5:]
        rc, out = sh(*args, "--op", "s1")
        check(f"5.8.relativo-equivalente.{name}", rc == 0 and eid1 in out and snap(C) == s0, out[-150:])
    must("time", "schedule", "marco", "--at", "2027/01/10 09:00", "--op", "s3")
    rc, out = sh("time", "schedule", "marco", "--at", "2027/1/10 9:00", "--op", "s3")
    check("5.8.absoluto-mesmo-instante", rc == 0 and "nada novo" in out, out[-150:])
    t_now = sim.load_clock(C)["now"]
    dur = sim.parse_when("2027/01/10 09:00", sim.load_clock(C)["calendar"], 0) - t_now
    rc, out = sh("time", "schedule", "marco", "--in", f"{dur}min", "--op", "s3")
    check("5.8.absoluto-x-relativo-conflito", rc != 0 and "outros dados" in out, out[-150:])

    # repetição após avanço do relógio, após processamento e após cancelamento
    must("time", "advance", "1d", "--op", "s58-a1", "--reason", "x")
    s1 = snap(C)
    rc, out = sh(*base, "--op", "s1")
    check("5.8.apos-avanco", rc == 0 and eid1 in out and "pendente" in out and snap(C) == s1, out[-150:])
    must("time", "advance", "2d", "--op", "s58-a2", "--reason", "x")
    bal = sim.balance(C, "npc-lia", "fichas")
    s2 = snap(C)
    rc, out = sh(*base, "--op", "s1")
    check("5.8.apos-processado", rc == 0 and "processado" in out and snap(C) == s2 and bal == 5
          and len(by_gk("schedule:s1")) == 1, f"{out[-150:]} saldo={bal}")
    must("time", "schedule", "vai ser cancelado", "--in", "5d", "--op", "s4")
    must("time", "cancel", by_gk("schedule:s4")[0]["id"], "--reason", "mudou de ideia")
    s3 = snap(C)
    rc, out = sh("time", "schedule", "vai ser cancelado", "--in", "5d", "--op", "s4")
    check("5.8.apos-cancelado", rc == 0 and "cancelado" in out and snap(C) == s3 and len(by_gk("schedule:s4")) == 1, out[-150:])

    # falha de validação não consome o --op
    for name, bad in {"local-inexistente": ["--loc", "loc-nada"], "efeito-invalido": ["--effects", J([{"type": "nada"}])]}.items():
        s4 = snap(C)
        rc, out = sh("time", "schedule", "valida", "--in", "1d", *bad, "--op", f"s5-{name}")
        unchanged = snap(C) == s4
        rc2, out2 = sh("time", "schedule", "valida", "--in", "1d", "--op", f"s5-{name}")
        check(f"5.8.falha-nao-consome.{name}", rc != 0 and unchanged and rc2 == 0 and "Agendado" in out2
              and len(by_gk(f"schedule:s5-{name}")) == 1, out[-120:] + out2[-120:])
    rc, out = sh("time", "schedule", "x", "--op", " ")
    check("5.8.op-vazio-recusado", rc != 0 and "--op vazio" in out)

    # "timeout": o processo grava o evento e morre antes de imprimir; a repetição reconhece pela chave gravada
    import builtins
    real_print = builtins.print

    def dies(*_a, **_k):
        raise TimeoutError("processo morto antes da mensagem")
    builtins.print = dies
    try:
        rpg.main(["time", "schedule", "pos-timeout", "--in", "1d", "--op", "s6"])
    except TimeoutError:
        pass
    finally:
        builtins.print = real_print
    rc, out = sh("time", "schedule", "pos-timeout", "--in", "1d", "--op", "s6")
    check("5.8.repeticao-apos-timeout", len(by_gk("schedule:s6")) == 1 and rc == 0 and "nada novo" in out, out[-150:])

    # world event-add
    n1 = len(evs(C))
    must("world", "event-add", "boato na praça")
    must("world", "event-add", "boato na praça")
    check("5.8.event-add.sem-op-cria-outro", len(evs(C)) == n1 + 2)
    ea = ["world", "event-add", "Lia viu o detetive", "--entities", "npc-lia,npc-det", "--loc", "loc-bar", "--hidden"]
    must(*ea, "--op", "e1")
    e1 = by_gk("event:e1")
    s5 = snap(C)
    rc, out = sh(*ea, "--op", "e1")
    check("5.8.event-add.repeticao-identica", len(e1) == 1 and rc == 0 and e1[0]["id"] in out and snap(C) == s5
          and len(e1[0].get("op_sig", "")) == 64, out)
    for name, args in {"texto": ["world", "event-add", "outra coisa"] + ea[3:], "data": ea + ["--date", "ontem"],
                       "causa": ea + ["--cause", eid1], "visibilidade": ea[:-1], "entidades": ea[:3] + ["--entities", "npc-lia"] + ea[5:]}.items():
        rc, out = sh(*args, "--op", "e1")
        check(f"5.8.event-add.conflito.{name}", rc != 0 and "outros dados" in out and snap(C) == s5, out[-150:])
    must("time", "advance", "1d", "--op", "s58-a3", "--reason", "x")  # sem --date: o "agora" da 1ª chamada vale
    rc, out = sh(*ea, "--op", "e1")
    check("5.8.event-add.apos-avanco", rc == 0 and len(by_gk("event:e1")) == 1 and "nada novo" in out, out[-150:])
    rc, _ = sh("world", "event-add", "x", "--entities", "npc-nada", "--op", "e2")
    rc2, _ = sh("world", "event-add", "x", "--op", "e2")
    check("5.8.event-add.falha-nao-consome", rc != 0 and rc2 == 0 and len(by_gk("event:e2")) == 1)

    # o mesmo nome de --op em comandos diferentes não colide (espaços de chave separados)
    must("time", "schedule", "colisao", "--in", "1h", "--op", "x58")
    must("world", "event-add", "colisao", "--op", "x58")
    must("time", "advance", "1h", "--op", "x58", "--reason", "colisao")
    must("econ", "pay", "pc-ana", "npc-lia", "1", "--reason", "colisao", "--op", "x58")
    gks = {e.get("gen_key") for e in evs(C)}
    check("5.8.sem-colisao-entre-comandos", {"schedule:x58", "event:x58", "op:x58", "econ:pay:x58"} <= gks
          and all(len(by_gk(k)) == 1 for k in ("schedule:x58", "event:x58", "op:x58", "econ:pay:x58")))
finally:
    shutil.rmtree(tmp)
    if sys.exc_info()[0]:  # preparação quebrou: mostra o que já foi verificado antes de propagar o erro
        for c, ok, d in RESULTS:
            print(f"{'OK  ' if ok else 'FALHA'} {c}" + (f"  — {d}" if d and not ok else ""))
        print("INTERROMPIDO: uma etapa de preparação falhou (veja o erro abaixo)")

fails = [(c, d) for c, ok, d in RESULTS if not ok]
for c, ok, d in RESULTS:
    print(f"{'OK  ' if ok else 'FALHA'} {c}" + (f"  — {d}" if d and not ok else ""))
print(f"\n{len(RESULTS) - len(fails)} ok, {len(fails)} falha(s)")
if fails:
    sys.exit(1)
print("OK: testes de recuperação e riscos revisados (Fase 3.1.1, etapas 5–6) passaram")
