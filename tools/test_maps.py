#!/usr/bin/env python3
"""Testes de mapas e mundo vivo (raiz temporária). Rodado também por tools/test_rpg.py."""
import hashlib, json, os, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(HERE))
import maps  # noqa: E402

GLOBAL = [Path.home() / ".claude" / f for f in ("settings.json", "settings.local.json", "CLAUDE.md")]


def h(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


before = {str(p): h(p) for p in GLOBAL}
tmp = Path(tempfile.mkdtemp())
env = {**os.environ, "RPG_ROOT": str(tmp)}


def run(*args, ok=True):
    p = subprocess.run([sys.executable, str(HERE / "rpg.py"), *args], env=env, capture_output=True, text=True)
    assert (p.returncode == 0) == ok, f"{args}: rc={p.returncode}\n{p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def jmap(camp, mid):
    return json.loads((tmp / "campaigns" / camp / "maps" / f"{mid}.json").read_text())


def strip(m):
    return {k: v for k, v in m.items() if k not in ("updated", "campaign")}


try:
    for d in ("templates", "systems"):
        shutil.copytree(PROJ / d, tmp / d)
    (tmp / "campaigns").mkdir()
    run("new", "mapas", "--name", "Mapas", "--system", "aventura-2d6")
    C = tmp / "campaigns" / "mapas"
    (C / "characters" / "pc-ana.md").write_text("---\nid: pc-ana\nname: Ana\n---\n# Ana\n")
    (C / "locations" / "loc-vau.md").write_text("---\nid: loc-vau\nname: Vau Antigo\n---\n# Vau\n\n## Conhecido pelos jogadores\n\n## Descrição\nponte velha\n")
    (C / "npcs" / "npc-bia.md").write_text("---\nid: npc-bia\nname: Bia\nstatus: viva\nlocation: loc-vau\nfaction: fac-corvos\n---\n# Bia\n\n## Conhecido pelos jogadores\nBarqueira.\n\n## Agenda\n- [dia 3] vai ao mercado\n")
    (C / "world.md").write_text((C / "world.md").read_text() + "\n- fac-corvos: Os Corvos (ladrões)\n")

    # 1. mundo + 6. SVG
    out = run("map", "gen", "mundo", "map-mundo", "--name", "Mundo", "--seed", "11", "--w", "120", "--h", "80")
    assert "Mapa gerado" in out
    W = jmap("mapas", "map-mundo")
    assert maps.validate(W, C) == [], maps.validate(W, C)
    kinds = {p["kind"] for p in W["points"]}
    assert {"capital", "vila", "fortaleza", "ruina"} <= kinds, kinds
    assert W["layers"]["roads"] and len(W["areas"]) == 6
    assert set("".join(W["layers"]["biomes"])) & set("~-") and set("".join(W["layers"]["biomes"])) & set(".fhm")
    ET.parse(C / "maps" / "mestre" / "map-mundo.svg")
    assert (C / "maps" / "mestre" / "index.html").exists()

    # 2. cidade
    run("map", "gen", "cidade", "map-porto", "--name", "Porto", "--seed", "5", "--river", "sim")
    P = jmap("mapas", "map-porto")
    assert maps.validate(P, C) == []
    ck = {p["kind"] for p in P["points"]}
    assert {"mercado", "taverna", "templo", "portao", "guilda", "quartel", "palacio"} <= ck, ck
    assert all(p["id"].startswith("poi-") for p in P["points"]) and len(P["areas"]) >= 5

    # 3. masmorra 3 níveis navegável
    run("map", "gen", "masmorra", "map-cripta", "--name", "Cripta", "--seed", "9", "--levels", "3")
    D = jmap("mapas", "map-cripta")
    assert len(D["layers"]["levels"]) == 3 and maps.validate(D, C) == []
    assert any(p["kind"] == "porta_secreta" for p in D["points"]) and any(a.get("secret") for a in D["areas"])
    for f in ("map-cripta.L1.svg", "map-cripta.L2.svg", "map-cripta.L3.svg"):
        ET.parse(C / "maps" / "mestre" / f)
    broken = json.loads(json.dumps(D))  # 7. conectividade: emparedar a escada de descida
    g = broken["layers"]["levels"][0]["grid"]
    dx, dy = next((x, y) for y, r in enumerate(g) for x, ch in enumerate(r) if ch == ">")
    g[dy] = g[dy][:dx] + "#" + g[dy][dx + 1:]
    assert any("descida" in e for e in maps.validate(broken)), maps.validate(broken)
    iso = json.loads(json.dumps(D))
    lv = iso["layers"]["levels"][0]
    lv["grid"] = ["".join("#" if ch in "+S" else ch for ch in r) for r in lv["grid"]]
    assert any("inalcançável" in e for e in maps.validate(iso))

    # batalha (+ recorte de sala) com tokens e caminho garantido
    run("map", "gen", "batalha", "map-briga", "--name", "Briga", "--seed", "3", "--env", "ruina", "--party", "pc-ana", "--enemies", "Capanga x3", "--ambush")
    B = jmap("mapas", "map-briga")
    assert maps.validate(B, C) == [] and len(B["layers"]["tokens"]) == 4
    assert B["layers"]["tokens"][0]["name"] == "Ana"
    room = next(a["id"] for a in D["areas"] if a["kind"] == "sala" and a["level"] == 1 and not a.get("secret"))
    run("map", "gen", "batalha", "map-sala", "--name", "Sala", "--seed", "4", "--from", "map-cripta", "--area", room, "--party", "pc-ana", "--enemies", "Rato x2")
    run("map", "gen", "batalha", "map-x", "--name", "X", "--party", "pc-fantasma", ok=False)
    run("map", "mark", "map-briga", "--name", "Bola de fogo", "--at", "C3", "--radius", "2")
    assert any(p["kind"] == "aoe" for p in jmap("mapas", "map-briga")["points"])

    # 4. reprodutibilidade (mesma seed/parâmetros em campanha nova) e 5. JSON
    run("new", "mapas2", "--name", "Mapas 2", "--system", "aventura-2d6")
    run("map", "gen", "mundo", "map-mundo", "--name", "Mundo", "--seed", "11", "--w", "120", "--h", "80", "--campaign", "mapas2")
    run("map", "gen", "masmorra", "map-cripta", "--name", "Cripta", "--seed", "9", "--levels", "3", "--campaign", "mapas2")
    run("map", "gen", "cidade", "map-porto", "--name", "Porto", "--seed", "5", "--river", "sim", "--campaign", "mapas2")
    for mid in ("map-mundo", "map-cripta", "map-porto"):
        assert strip(jmap("mapas", mid)) == strip(jmap("mapas2", mid)), f"{mid} não reproduziu"
    run("map", "gen", "mundo", "map-outro", "--name", "Outro", "--seed", "12", "--w", "120", "--h", "80", "--campaign", "mapas2")
    assert jmap("mapas2", "map-outro")["layers"]["biomes"] != jmap("mapas2", "map-mundo")["layers"]["biomes"]
    run("use", "mapas")
    for f in (C / "maps").glob("map-*.json"):
        m = json.loads(f.read_text())
        for k in ("id", "campaign", "type", "name", "generator", "size", "areas", "points", "connections", "discovery", "updated", "files"):
            assert k in m, (f.name, k)
        assert m["generator"]["seed"] is not None and m["updated"]["reason"]

    # região: herda ids do mapa-pai (sem duplicar)
    reg = W["areas"][0]["id"]
    run("map", "gen", "regiao", "map-reg", "--name", "Região", "--parent", "map-mundo", "--area", reg)
    R = jmap("mapas", "map-reg")
    assert maps.validate(R, C) == []
    inherited = {p["id"] for p in W["points"]} & {p["id"] for p in R["points"]}
    assert inherited, "região não herdou locais do mundo"
    assert any(c["to"] == "map-reg" for c in jmap("mapas", "map-mundo")["connections"])

    # 8. referências / 9. sem duplicar após atualização
    cap = next(p for p in W["points"] if p["kind"] == "capital")
    run("map", "place", "map-mundo", "--name", "Vau", "--kind", "ruina", "--ref", "loc-nada", "--near", cap["id"], ok=False)
    out = run("map", "place", "map-mundo", "--name", "Vau Antigo", "--kind", "vila", "--ref", "loc-vau", "--near", cap["id"])
    assert "poi-vau-antigo" in out
    assert "não duplico" in run("map", "place", "map-mundo", "--name", "Vau Antigo", "--kind", "vila", "--near", cap["id"], ok=False)
    assert "já está neste mapa" in run("map", "place", "map-mundo", "--name", "Outro Vau", "--kind", "vila", "--ref", "loc-vau", "--near", cap["id"], ok=False)
    run("map", "place", "map-reg", "--name", "Vau Antigo", "--kind", "vila", "--ref", "loc-vau", "--at", "10,10")
    assert "poi-vau-antigo" in json.dumps(jmap("mapas", "map-reg"))  # mesmo lugar → mesmo id nos dois mapas
    assert maps.validate(jmap("mapas", "map-mundo"), C) == []  # vila nova ganhou estrada
    run("map", "set", "map-mundo", cap["id"], "ref=loc-inexistente", ok=False)
    run("map", "set", "map-mundo", cap["id"], "faction=fac-corvos")
    run("map", "connect", "map-mundo", "map-porto", "--via", cap["id"], "--note", "capital")
    run("map", "connect", "map-mundo", "map-fantasma", ok=False)
    assert "map-mundo" in run("map", "where", cap["id"])
    assert cap["id"] in run("map", "show", "map-mundo", "--kind", "capital")
    ids = [p["id"] for p in jmap("mapas", "map-mundo")["points"]]
    assert len(ids) == len(set(ids))

    # 10. visão pública sem segredos
    secret = next(p for p in W["points"] if p.get("secret"))
    run("map", "set", "map-mundo", secret["id"], "gm_note=CULTO-NO-SUBSOLO-777")
    run("map", "place", "map-mundo", "--name", "Covil Proibido", "--kind", "ruina", "--secret", "--near", cap["id"], "--gm-note", "TESOURO-AQUI-123")
    run("map", "reveal", "map-mundo", "map-mundo", reg)
    run("map", "reveal", "map-mundo", cap["id"], "--status", "visitado")
    run("map", "reveal", "map-cripta", "map-cripta", D["areas"][0]["id"])
    pub = C / "maps" / "jogador"
    blob = "".join(f.read_text() for f in pub.iterdir())
    for s in ("CULTO-NO-SUBSOLO-777", "TESOURO-AQUI-123", "Covil Proibido", secret["id"], secret["name"], "map-briga", "map-sala",
              "map-cripta.L2", "Capanga", "porta_secreta", "gm_note", '"secret"', "seed"):
        assert s not in blob, f"vazou na visão do jogador: {s}"
    assert cap["name"] in blob and "map-mundo.svg" in blob
    for f in pub.glob("*.svg"):
        ET.parse(f)
    assert "Covil Proibido" in (C / "maps" / "mestre" / "map-mundo.svg").read_text()
    run("map", "reveal", "map-mundo", "poi-covil-proibido", "--status", "rumor")
    assert "Covil Proibido" in (pub / "map-mundo.svg").read_text()  # revelado de propósito → aparece
    run("map", "reveal", "map-mundo", "poi-covil-proibido", "--hide")
    assert "Covil Proibido" not in (pub / "map-mundo.svg").read_text()
    # local com 'Conhecido pelos jogadores' preenchido aparece sem reveal explícito (mesma autoridade)
    lv = C / "locations" / "loc-vau.md"
    lv.write_text(lv.read_text().replace("## Conhecido pelos jogadores\n", "## Conhecido pelos jogadores\nUm vau raso.\n"))
    run("map", "render")
    assert "Vau Antigo" in (pub / "map-mundo.svg").read_text() and "Um vau raso." in (pub / "index.html").read_text()
    assert "ponte velha" not in (pub / "index.html").read_text()  # descrição do mestre não vai

    # mundo vivo: relações, linha do tempo, agenda, verificador
    run("world", "rel-add", "npc-bia", "fac-corvos", "informante", "--secret")
    run("world", "rel-add", "npc-bia", "npc-nada", "conhece", ok=False)
    out = run("world", "rel", "fac-corvos", "--with", "npc-")
    assert "npc-bia" in out and "informante" in out
    assert "npc-bia" not in run("world", "rel", "fac-corvos", "--public")
    run("world", "rel-add", "quest-x", "loc-vau", "ocorre_em", ok=False)
    run("session", "start")
    run("world", "event-add", "Ponte do vau desaba", "--date", "Dia 3", "--loc", "loc-vau", "--entities", "npc-bia", "--consequences", "travessia só de barco")
    run("world", "event-add", "Corvos sabotaram a ponte", "--date", "Dia 2", "--entities", "fac-corvos", "--hidden", "--prev", "evt-0001")
    run("world", "event-add", "x", "--date", "Dia 4", "--corrects", "evt-0001", ok=False)  # correção exige motivo
    run("world", "event-add", "Ponte desabou à noite, não de dia", "--date", "Dia 3", "--corrects", "evt-0001", "--reason", "jogador lembrou")
    tl = run("world", "timeline")
    assert "evt-0001" in tl and "corrigido por evt-0003" in tl and "[MESTRE]" in tl
    assert "Corvos" not in run("world", "timeline", "--public")
    assert '"evt-0001"' in (C / "state" / "events.jsonl").read_text()  # original preservado
    assert "vai ao mercado" in run("world", "agenda")
    out = run("world", "verify")
    assert "evt-0002 referencia evt-0001, registrado depois" not in out
    # verificador acusa: coordenada fora, conexão quebrada, segredo exposto, NPC em local inexistente
    bad = json.loads((C / "maps" / "map-porto.json").read_text())
    orig_x = bad["points"][0]["x"]
    bad["points"][0]["x"] = 999
    bad["connections"].append({"to": "map-sumiu", "note": ""})
    (C / "maps" / "map-porto.json").write_text(json.dumps(bad))
    (C / "secrets" / "plot.md").write_text((C / "secrets" / "plot.md").read_text() + "\n- O barqueiro-mor é na verdade o líder dos Corvos há dez anos.\n")
    (C / "journal.md").write_text((C / "journal.md").read_text() + "\nO barqueiro-mor é na verdade o líder dos Corvos há dez anos.\n")
    (C / "npcs" / "npc-rui.md").write_text("---\nid: npc-rui\nname: Rui\nlocation: loc-sumido\n---\n# Rui\n")
    snap = {p: h(p) for p in C.rglob("*") if p.is_file()}
    out = run("world", "verify")
    for s in ("fora do mapa", "map-sumiu", "[segredo exposto] journal.md", "loc-sumido"):
        assert s in out, (s, out)
    assert snap == {p: h(p) for p in C.rglob("*") if p.is_file()}, "verify alterou arquivos"
    (C / "npcs" / "npc-rui.md").unlink()
    run("map", "validate", ok=False)
    bad["points"][0]["x"] = orig_x
    bad["connections"].pop()
    (C / "maps" / "map-porto.json").write_text(json.dumps(bad))

    # 11. mapas preservados ao encerrar e retomar sessão
    snap = {p.name: h(p) for p in (C / "maps").glob("map-*.json")}
    run("session", "end")
    run("session", "start")
    assert "ABERTA" in run("status")
    assert snap == {p.name: h(p) for p in (C / "maps").glob("map-*.json")}
    assert "map-mundo" in run("map", "list")

    # 13. backup e restauração com mapas
    arc = sorted((tmp / "backups").glob("mapas-*.tar.gz"))[-1]
    run("restore", str(arc), "--as", "mapas-copia")
    CC = tmp / "campaigns" / "mapas-copia"
    assert {p.name for p in (CC / "maps").glob("map-*.json")} == set(snap)
    assert "OK" in run("map", "validate", "--campaign", "mapas-copia")
    assert "id: mapas-copia" in (CC / "campaign.yaml").read_text()

    # 12. campanha antiga sem mapas (cópia da teste-validacao, se existir; senão uma recém-criada sem maps/)
    legacy = PROJ / "campaigns" / "teste-validacao"
    if legacy.exists():
        shutil.copytree(legacy, tmp / "campaigns" / "legado", ignore=shutil.ignore_patterns("maps"))
        y = tmp / "campaigns" / "legado" / "campaign.yaml"
        y.write_text(y.read_text().replace("id: teste-validacao", "id: legado"))
    else:
        run("new", "legado", "--name", "Legado", "--system", "aventura-2d6")
    assert not (tmp / "campaigns" / "legado" / "maps").exists()
    assert "Nenhum mapa" in run("map", "list", "--campaign", "legado")
    run("check", "--campaign", "legado")
    run("world", "verify", "--campaign", "legado")
    run("world", "timeline", "--campaign", "legado")
    run("map", "gen", "masmorra", "map-teste", "--name", "Teste", "--seed", "1", "--campaign", "legado")
    assert "OK" in run("map", "validate", "--campaign", "legado")

    # PNG: só afirma que gerou se o arquivo existir
    out = run("map", "render", "--png", "--campaign", "legado")
    pngs = list((tmp / "campaigns" / "legado" / "maps").rglob("*.png"))
    assert ("PNG NÃO gerado" in out) == (not pngs)
finally:
    shutil.rmtree(tmp)

# 14. nenhuma configuração global alterada pelos testes
assert before == {str(p): h(p) for p in GLOBAL}, "configuração global mudou!"
print("OK: testes de mapas e mundo vivo passaram")
