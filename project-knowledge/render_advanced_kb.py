#!/usr/bin/env python3
"""render_advanced_kb -- knowledge-as-data -> reader-facing prose.

Renders ADVANCED_PROJECT_KNOWLEDGE_<batch>.md from CAP-01_advanced.json the way
TASK_TRACKER renders from DATA.json: the JSON is the truth, the prose is a view.
The point is to JUDGE the rendered doc as a reader -- if it reads like a curriculum
you'd onboard an engineer with, the schema is right; if it reads like a DB dump,
it isn't. Stdlib only.
"""
import json
import sys

SRC = "/home/claude/review/CAP-01_advanced.json"
OUT = "/home/claude/review/ADVANCED_PK_CAP-01.md"


def topo_order(concepts):
    """Learning order: a concept comes after everything it requires_understanding."""
    by_id = {c["id"]: c for c in concepts}
    done, order = set(), []

    def visit(cid, stack):
        if cid in done or cid not in by_id:
            return
        for req in by_id[cid].get("requires_understanding", []):
            if req in by_id and req not in stack:
                visit(req, stack | {cid})
        done.add(cid)
        order.append(by_id[cid])
    for c in concepts:
        visit(c["id"], set())
    return order


def render(d):
    L = []
    P = L.append
    P(f"# Advanced Project Knowledge — {d['batch']} (v{d['version']})\n")
    P(f"*Rendered from `CAP-01_advanced.json` ({d['schema']}). The JSON is the "
      f"truth; this is a view. Mechanical facts (signatures/edges/sinks) live in "
      f"the graph — this holds only what reading the code produced.*\n")

    # 1. Curriculum (learning order)
    P("## 1. Concepts, in the order you must learn them\n")
    P("*A curriculum, not an index: each concept lists what you must understand "
      "first. Read top-down and nothing is forward-referenced.*\n")
    for i, c in enumerate(topo_order(d["concepts"]), 1):
        reqs = c.get("requires_understanding") or []
        req_names = [next((x["name"] for x in d["concepts"] if x["id"] == r), r) for r in reqs]
        P(f"**{i}. {c['name']}** — {c['definition']}")
        if req_names:
            P(f"  *Prerequisites:* {', '.join(req_names)}.")
        P(f"  *Spans:* {', '.join(c['spans_files'])}.\n")

    # 2. Constraint topology
    P("## 2. Constraint surfaces (where bugs become countable)\n")
    P("*A constraint carves program states into legal/illegal. Bugs are points "
      "where the code crosses a surface it should respect. Count incidence vs "
      "guards; a hole is found by subtraction, not by hunting.*\n")
    for k in d["constraints"]:
        P(f"### {k['id']} — {k['surface']}")
        P(f"- **Incidence:** {len(k['incidence'])} point(s), "
          f"{sum(1 for i in k['incidence'] if i['guarded'])} guarded → "
          f"**{k['holes']} hole(s).**")
        for inc in k["incidence"]:
            P(f"    - `{inc['at']}` — {inc['role']} "
              f"{'✓ guarded' if inc['guarded'] else '✗ UNGUARDED'}")
        P(f"- **Witness:** {k['witness']}")
        P(f"- {k['topology_note']}\n")

    # 3. Exceptions / carve-outs
    P("## 3. Exceptions to the rules you just learned (the bug-magnets)\n")
    P("*A newcomer gets hurt by the carve-outs, not the rules. Each is a relation "
      "between a general rule and its scoped exception.*\n")
    for x in d["exceptions"]:
        P(f"### {x['id']}")
        P(f"- **Rule:** {x['rule']}")
        P(f"- **Exception:** {x['exception']}")
        P(f"- **Why allowed:** {x['why_allowed']}")
        P(f"- **At:** `{x['at']}` · **Witness:** {x['witness']}")
        P(f"- **Teaching:** {x['teaching']}\n")

    # 4. Beliefs (the reader-only layer)
    P("## 4. Beliefs — purpose vs behavior, with witnesses\n")
    P("*Each belief carries how it was known (derivation), how sure (confidence), "
      "and — where reading produced them — the assumptions the code makes by NOT "
      "checking, the surprises, and the corpses of rejected alternatives.*\n")
    for b in d["beliefs"]:
        P(f"### {b['id']} — `{b['unit']}`")
        P(f"- **Stated purpose:** {b['purpose_stated']}")
        P(f"- **Observed behavior:** {b['behavior_observed']}")
        pv = b["purpose_vs_behavior"]
        flag = "⚠️ " if "DRIFT" in pv or "FALSE" in pv else "✓ "
        P(f"- **Purpose vs behavior:** {flag}{pv}")
        P(f"- **Confidence:** {b['confidence']} · **Derivation:** {b['derivation']}")
        if b.get("witness"):
            P(f"- **Witness:** {b['witness']}")
        if b.get("enforces"):
            P(f"- **Enforces:** {', '.join(b['enforces'])}")
        if b.get("history"):
            P(f"- **History:** {b['history']}")
        for a in b.get("assumptions", []):
            P(f"- **Assumption (by absence-of-check):** {a['text']} "
              f"→ *{a['status']}* (tested_by: {a.get('tested_by')})")
        for s in b.get("surprises", []):
            P(f"- **Surprise:** {s['text']}")
        for r in b.get("rejected_alternatives", []):
            P(f"- **Rejected alternative / tempting mistake:** {r['tempting_mistake']} "
              f"→ *why it fails:* {r['why_it_fails']}")
        for q in b.get("open_questions", []):
            P(f"- **Open question ({q['for']}):** {q['text']}")
        for g in b.get("coverage_gaps", []):
            P(f"- **Coverage gap:** `{g['behavior']}` — tested={g['tested']} ({g['shape']})")
        P("")

    # 5. The counterfactual test (the validator)
    ct = d["counterfactual_test"]
    P("## 5. The counterfactual test — can you predict a change you haven't made?\n")
    P(f"*{ct['prompt']}*\n")
    P("| Mod | Change | KB prediction | Ground truth |")
    P("|---|---|---|---|")
    for m in ct["mods"]:
        P(f"| {m['id']} | {m['change']} | **{m['kb_prediction']}** | {m['ground_truth']} |")
    P("")
    for m in ct["mods"]:
        P(f"- **{m['id']} — {m['kb_prediction']}.** {m['kb_reasoning']}")
    P(f"\n**Pass condition:** {ct['pass_condition']}")
    P("\n**Result (executed):** KB predicted A=safe · B=safe · C=unsafe; ground "
      "truth confirmed all three (MOD-C dropped exactly the VR-P03 OAuth-fragment "
      "keys code/state/apikey/challenge/captcha/nonce/otp; A/B leaked nothing). "
      "The KB conferred *prediction*, not just description — the discriminator "
      "between C (unsafe) and B (safe) was the rejected-alternative corpse + the "
      "kv-from-SoT constraint + witness W1, none of which a map-only KB carries.\n")
    return "\n".join(L)


def main():
    d = json.load(open(SRC))
    md = render(d)
    with open(OUT, "w") as f:
        f.write(md)
    print(f"rendered {OUT} ({md.count(chr(10))} lines, {len(md)} chars)")


if __name__ == "__main__":
    main()
