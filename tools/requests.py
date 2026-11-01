#!/usr/bin/env python3
"""The producer's requests, from the agent's side (a coding agent building one).

The producer files requests (brain/producer.py's request action); the human approves or
rejects them on the audio page's REQUESTS menu. An agent building an approved one says so
here, so the page and the producer can see who has it and where it's got to:

    tools/requests.py list                          # everything, newest last
    tools/requests.py take 4 --agent Claude --plan "read the voice" "add the module" "test" "deploy"
    tools/requests.py step 4 2 [--note "..."]       # now on step 2 (1-based)
    tools/requests.py done 4 --note "built (abc1234): ..."
    tools/requests.py release 4 [--note "..."]      # give it back: approved, no agent

Only approved requests can be taken, and only by one agent at a time.
"""
import argparse, json, pathlib, sys, time

FILE = pathlib.Path.home() / ".local/state/m00drc/requests.json"


def load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {"requests": []}


def save(data):
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(FILE)


def find(data, rid):
    r = next((r for r in data["requests"] if r["id"] == rid), None)
    if r is None:
        sys.exit(f"no request #{rid}")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("take"); t.add_argument("id", type=int); t.add_argument("--agent", required=True)
    t.add_argument("--plan", nargs="+", required=True)
    s = sub.add_parser("step"); s.add_argument("id", type=int); s.add_argument("n", type=int); s.add_argument("--note")
    d = sub.add_parser("done"); d.add_argument("id", type=int); d.add_argument("--note", required=True)
    r = sub.add_parser("release"); r.add_argument("id", type=int); r.add_argument("--note")
    a = ap.parse_args(argv)
    data, now = load(), time.time()
    if a.cmd == "list":
        for q in data["requests"]:
            work = (f" · {q['agent']}, step {q.get('step', 1)}/{len(q.get('plan', []))}: {q['plan'][q.get('step', 1) - 1]}"
                    if q["status"] == "in_progress" else "")
            print(f"#{q['id']} [{q['status']}] {q['title']}{work}" + (f" · {q['note']}" if q.get("note") else ""))
        return
    q = find(data, a.id)
    if a.cmd == "take":
        if q["status"] != "approved":
            sys.exit(f"#{a.id} is {q['status']}: only approved requests can be taken")
        q.update(status="in_progress", agent=a.agent, plan=a.plan, step=1, taken=now, updated=now)
    elif a.cmd == "step":
        if q["status"] != "in_progress":
            sys.exit(f"#{a.id} isn't in progress")
        if not 1 <= a.n <= len(q["plan"]):
            sys.exit(f"#{a.id}'s plan has {len(q['plan'])} steps")
        q.update(step=a.n, updated=now)
        if a.note:
            q["note"] = a.note
    elif a.cmd == "done":
        if q["status"] not in ("in_progress", "approved"):
            sys.exit(f"#{a.id} is {q['status']}")
        q.update(status="done", note=a.note, done=now, updated=now)
        if q.get("plan"):
            q["step"] = len(q["plan"])
    elif a.cmd == "release":
        if q["status"] != "in_progress":
            sys.exit(f"#{a.id} isn't in progress")
        q.update(status="approved", updated=now)
        for k in ("agent", "plan", "step", "taken"):
            q.pop(k, None)
        if a.note:
            q["note"] = a.note
    save(data)
    print(f"#{q['id']} {q['status']}" + (f", {q['agent']}, step {q['step']}/{len(q['plan'])}" if q["status"] == "in_progress" else ""))


if __name__ == "__main__":
    main()
